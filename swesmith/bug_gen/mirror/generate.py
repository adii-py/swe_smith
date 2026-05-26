"""
Purpose: Given a pull request, mirror the bug in the current form of the repository.

Usage: python -m swesmith.bug_gen.mirror.generate logs/prs/data/*-task-instances.jsonl
"""

import argparse
import json
import litellm
import logging
import os
import re
import shutil
import uuid
import traceback
import signal

from concurrent.futures import ProcessPoolExecutor, as_completed
from dotenv import load_dotenv
from litellm import completion, completion_cost
from multiprocessing import current_process
from swebench.harness.constants import KEY_INSTANCE_ID
from swesmith.bug_gen.patch_inverter import (
    apply_reverse_and_capture,
    invert_unified_diff,
    validate_patch_applies,
)
from swesmith.bug_gen.utils import (
    apply_patches,
    get_patch,
)
from swesmith.bug_gen.mirror.prompts import (
    CHUNKED_DEMO_PROMPT,
    CHUNKED_RECOVERY_PROMPT,
    CHUNKED_TASK_PROMPT,
    DEMO_PROMPT,
    RECOVERY_PROMPT,
    TASK_PROMPT,
)
from swesmith.constants import (
    LOG_DIR_BUG_GEN,
    KEY_PATCH,
    PREFIX_BUG,
    PREFIX_METADATA,
    INSTANCE_REF,
)
from swesmith.profiles import registry, RepoProfile
from tqdm.auto import tqdm
from unidiff import PatchSet

load_dotenv()

# Configure litellm for custom endpoint via LITE_LLM vars (always prefer these)
if os.getenv("LITE_LLM_URL"):
    os.environ["OPENAI_API_BASE"] = os.getenv("LITE_LLM_URL")
if os.getenv("LITE_LLM_API_KEY"):
    os.environ["OPENAI_API_KEY"] = os.getenv("LITE_LLM_API_KEY")

litellm.drop_params = True

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

logging.getLogger("LiteLLM").setLevel(logging.WARNING)
litellm.suppress_debug_info = True

MIRROR_PR = "pr_mirror"
KEY_COST = "cost"
KEY_PULL_NUM = "pull_number"
KEY_RECOVER_STATUS = "recover_status"
KEY_REWRITES = "rewrites"
KEY_SKIP_REASON = "skip_reason"
RECOVER_FAIL = "failed"
RECOVER_SKIPPED = "skipped"
RECOVER_SUCCESS = "success"

CHUNK_LINE_THRESHOLD = 800
CHUNK_WINDOW_SIZE = 400


def get_metadata_file_name(pr_num):
    return f"{PREFIX_METADATA}__pr_{pr_num}.json"


worker_tempdirs = {}


def should_attempt_recovery(
    inst, repo, max_files=10, max_lines=800, max_file_lines=15000
):
    """
    Attempt if the following criteria are met:
    * Fewer than max_files files are changed
    * Fewer than max_lines lines are changed
    * No changed file is >max_file_lines lines
    """
    patch = PatchSet(inst[KEY_PATCH])
    # Support both Python and Rust files
    code_extensions = ('.py', '.rs')
    num_code_edited = len([x for x in patch if x.path.endswith(code_extensions)])
    if num_code_edited == 0:
        return False, "No Python/Rust files changed"
    if num_code_edited > max_files:
        return False, f"Too many files changed (>{max_files} files)"
    lines_changed = 0
    for file_diff in patch:
        if file_diff.is_binary_file:
            return False, "Contains binary file"
        file_path = os.path.join(repo, file_diff.path)
        if not os.path.exists(file_path):
            # Skip over edits to files that don't exist
            continue
        file_content = open(file_path).read()
        if len(file_content.splitlines()) > max_file_lines:
            return False, f"Changed file is too long (>{max_file_lines} lines)"
        lines_changed += file_diff.added + file_diff.removed
    if lines_changed == 0:
        return False, "No lines changed (no changed file exists)"
    if lines_changed > max_lines:
        return False, f"Too many lines changed (>{max_lines})"
    return True, None


def build_chunks(file_lines, file_diff, window_size=CHUNK_WINDOW_SIZE):
    """Build (start, end, hunks, chunk_text) chunks around each hunk's expected location.
    Uses hunk.source_start as a rough guide. Merges overlapping windows.
    Does not require exact context-line matching — tolerates code drift."""
    raw_chunks = []

    for hunk in file_diff:
        center = max(0, hunk.source_start - 1)
        start = max(0, center - window_size // 2)
        end = min(len(file_lines), center + window_size // 2)
        raw_chunks.append((start, end, [hunk]))

    # Merge overlapping or adjacent chunks
    raw_chunks.sort(key=lambda x: x[0])
    merged = []
    for start, end, hunks in raw_chunks:
        if merged and start <= merged[-1][1]:
            merged[-1] = (
                merged[-1][0],
                max(merged[-1][1], end),
                merged[-1][2] + hunks,
            )
        else:
            merged.append((start, end, hunks))

    result = []
    for start, end, hunks in merged:
        chunk_text = "\n".join(file_lines[start:end])
        result.append((start, end, hunks, chunk_text))
    return result


def recover_sweb_inst(inst, repo, model, api_key=None, log_path=None):
    """
    Given a pull request, mirror the bug in the current form of the repository.

    Args:
        inst: The instance to mirror.
        repo: The repository to mirror the bug in.
        model: The model to use for bug generation.
    Returns:
        A list of patch files.
    """
    patch_files = []
    patch = PatchSet(inst[KEY_PATCH])

    def extract_output(output):
        code_block_pat = re.compile(r"^```(?:\w+)?\s*\n([\s\S]*?)^```\s*$", re.MULTILINE)
        match = code_block_pat.search(output)
        if match:
            output = match.group(1)
        return output.strip()

    metadata = {KEY_COST: 0, KEY_REWRITES: {}, KEY_RECOVER_STATUS: RECOVER_SUCCESS}
    for idx, file_diff in enumerate(patch):
        file_path = os.path.join(repo, file_diff.path)

        if file_diff.is_added_file and os.path.exists(file_path):
            os.remove(file_path)
            patch = get_patch(repo, reset_changes=True)
            if patch:
                patch_path = f"{inst[KEY_INSTANCE_ID]}_{idx}.diff"
                with open(patch_path, "w") as f:
                    f.write(patch)
                patch_files.append(patch_path)
            continue
        elif file_diff.is_removed_file:
            if not os.path.exists(os.path.dirname(file_path)):
                # Skip over re-adding removed file if the parent directory doesn't exist
                continue
            with open(file_path, "w") as f:
                # Write the removed lines to the file
                f.write(
                    "".join(
                        line.value
                        for hunk in file_diff
                        for line in hunk
                        if line.is_removed
                    )
                )
            patch = get_patch(repo, reset_changes=True)
            if patch:
                patch_path = f"{inst[KEY_INSTANCE_ID]}_{idx}.diff"
                with open(patch_path, "w") as f:
                    f.write(patch)
                patch_files.append(patch_path)
            continue

        # Support both Python and Rust files
        code_extensions = ('.py', '.rs')
        if not os.path.exists(file_path) or not file_path.endswith(code_extensions):
            # Skip over edits to files that don't exist or are not code files
            continue
        file_content = open(file_path).read()
        ends_with_newline = file_content.endswith("\n")
        file_lines = file_content.splitlines()

        use_chunking = len(file_lines) > CHUNK_LINE_THRESHOLD
        if use_chunking:
            chunks = build_chunks(file_lines, file_diff, window_size=CHUNK_WINDOW_SIZE)
            if chunks is None:
                use_chunking = False

        if use_chunking:
            chunk_outputs = []
            for chunk_start, chunk_end, hunks, chunk_text in reversed(chunks):
                hunk_text = "\n".join(str(h) for h in hunks)
                response = completion(
                    model=model,
                    messages=[
                        {"role": "user", "content": CHUNKED_RECOVERY_PROMPT},
                        {"role": "user", "content": CHUNKED_DEMO_PROMPT},
                        {
                            "role": "user",
                            "content": CHUNKED_TASK_PROMPT.format(
                                chunk_text, hunk_text
                            ),
                        },
                    ],
                    n=1,
                    temperature=0,
                    api_key=api_key,
                    timeout=600,
                    max_retries=2,
                )
                try:
                    cost = completion_cost(completion_response=response)
                except Exception as e:
                    logger.warning(
                        f"Could not calculate cost for model {model}: {e}"
                    )
                    cost = 0
                metadata[KEY_COST] += cost
                output = response.choices[0].message.content.strip()
                output_extracted = extract_output(output)
                chunk_outputs.append(
                    {
                        "chunk_start": chunk_start,
                        "chunk_end": chunk_end,
                        "output": output,
                        "output_extracted": output_extracted,
                        KEY_COST: cost,
                    }
                )
                new_lines = output_extracted.splitlines()
                file_lines = (
                    file_lines[:chunk_start] + new_lines + file_lines[chunk_end:]
                )

            new_content = "\n".join(file_lines)
            if ends_with_newline:
                new_content += "\n"
            with open(file_path, "w") as f:
                f.write(new_content)
            metadata[KEY_REWRITES][file_path] = {
                "chunked": True,
                "chunks": chunk_outputs,
            }
        else:
            response = completion(
                model=model,
                messages=[
                    {"role": "user", "content": RECOVERY_PROMPT},
                    {"role": "user", "content": DEMO_PROMPT},
                    {
                        "role": "user",
                        "content": TASK_PROMPT.format(
                            file_content, str(file_diff)
                        ),
                    },
                ],
                n=1,
                temperature=0,
                api_key=api_key,
                timeout=600,
                max_retries=2,
            )
            try:
                cost = completion_cost(completion_response=response)
            except Exception as e:
                logger.warning(
                    f"Could not calculate cost for model {model}: {e}"
                )
                cost = 0
            metadata[KEY_COST] += cost
            metadata[INSTANCE_REF] = inst
            output = response.choices[0].message.content.strip()  # type: ignore
            output_extracted = extract_output(output)
            metadata[KEY_REWRITES][file_path] = {
                "output": output,
                "output_extracted": output_extracted,
                KEY_COST: cost,
            }
            with open(file_path, "w") as f:
                f.write(output_extracted)

        # Get patch from codebase
        try:
            patch = get_patch(repo, reset_changes=True)
            if not patch:
                raise ValueError("Patch is empty")
            patch_path = f"{inst[KEY_INSTANCE_ID]}_{idx}.diff"
            with open(patch_path, "w") as f:
                f.write(patch)
            patch_files.append(patch_path)
        except Exception as e:
            logger.error(f"Failed to get patch: {e}")
            continue

    # Save logs
    if log_path is None:
        log_path = LOG_DIR_BUG_GEN / repo / MIRROR_PR / inst[KEY_INSTANCE_ID]
    metadata_file = log_path / get_metadata_file_name(inst[KEY_PULL_NUM])
    ref_patch_file = log_path / f"ref__pr_{inst[KEY_PULL_NUM]}.diff"
    with open(metadata_file, "w") as f:
        if len(patch_files) == 0:
            metadata[KEY_RECOVER_STATUS] = RECOVER_FAIL
        json.dump(metadata, f, indent=4)
    with open(ref_patch_file, "w") as f:
        f.write(inst[KEY_PATCH])

    return patch_files


def should_process_instance(inst, repo, redo_existing, redo_skipped):
    """
    Determine if an instance should be processed based on existing metadata.
    """
    log_path = LOG_DIR_BUG_GEN / repo / MIRROR_PR / inst[KEY_INSTANCE_ID]
    metadata_file = log_path / get_metadata_file_name(inst[KEY_PULL_NUM])

    if not os.path.exists(metadata_file):
        return True, None

    metadata = json.load(open(metadata_file))
    recover_status = metadata[KEY_RECOVER_STATUS]

    if redo_existing and redo_skipped:
        return True, recover_status
    elif redo_existing and recover_status != RECOVER_SKIPPED:
        return True, recover_status
    elif redo_skipped and recover_status == RECOVER_SKIPPED:
        return True, recover_status

    return False, recover_status


def process_single_instance(
    inst, repo, model, api_key=None, max_files=8, max_lines=500, max_file_lines=10000
):
    """Process a single instance with its own working directory."""
    global this_worker_id
    temp_dir = worker_tempdirs[this_worker_id]
    original_dir = os.getcwd()
    try:
        log_path = (
            (LOG_DIR_BUG_GEN / repo / MIRROR_PR / inst[KEY_INSTANCE_ID])
            .resolve()
            .absolute()
        )
        metadata_file = log_path / get_metadata_file_name(inst[KEY_PULL_NUM])
        os.makedirs(log_path, exist_ok=True)

        os.chdir(temp_dir)
        registry.get(repo).clone()

        # Check if we should attempt recovery
        attempt_recovery, reason = should_attempt_recovery(
            inst, repo, max_files, max_lines, max_file_lines
        )
        if not attempt_recovery:
            with open(metadata_file, "w") as f:
                json.dump(
                    {
                        KEY_RECOVER_STATUS: RECOVER_SKIPPED,
                        KEY_SKIP_REASON: reason,
                    },
                    f,
                    indent=4,
                )
            return "skipped"

        bug_file = log_path / f"{PREFIX_BUG}__pr_{inst[KEY_PULL_NUM]}.diff"
        fix_patch = inst[KEY_PATCH]

        # Prefer programmatic inversion of the PR fix diff (bug-introducing patch)
        inverted_patch = invert_unified_diff(fix_patch)
        invert_check = validate_patch_applies(repo, inverted_patch)
        if invert_check.success:
            with open(bug_file, "w") as f:
                f.write(inverted_patch)
            with open(metadata_file, "w") as f:
                json.dump(
                    {
                        KEY_RECOVER_STATUS: RECOVER_SUCCESS,
                        KEY_COST: 0,
                        KEY_REWRITES: {},
                        "invert_method": "programmatic",
                        INSTANCE_REF: inst,
                    },
                    f,
                    indent=4,
                )
            return "recover_success"

        # Fallback: apply fix patch in reverse, then capture working-tree diff
        reverse_check = validate_patch_applies(repo, fix_patch, reverse=True)
        if reverse_check.success:
            captured = apply_reverse_and_capture(repo, fix_patch)
            if captured:
                with open(bug_file, "w") as f:
                    f.write(captured)
                with open(metadata_file, "w") as f:
                    json.dump(
                        {
                            KEY_RECOVER_STATUS: RECOVER_SUCCESS,
                            KEY_COST: 0,
                            KEY_REWRITES: {},
                            "invert_method": "git_reverse_capture",
                            INSTANCE_REF: inst,
                        },
                        f,
                        indent=4,
                    )
                return "recover_success"

        # Attempt to perform recovery
        patch_files = recover_sweb_inst(
            inst, repo, model, api_key=api_key, log_path=log_path
        )

        if len(patch_files) == 0:
            return "recover_fail"
        else:
            patch_merged = apply_patches(repo, patch_files)
            if patch_merged:
                with open(bug_file, "w") as f:
                    f.write(patch_merged)
                for patch_file in patch_files:
                    os.remove(patch_file)
                return "recover_success"
            else:
                return "recover_fail"

    except Exception as e:
        logger.error(f"Error processing instance {inst[KEY_INSTANCE_ID]}: {e}")
        logger.error(traceback.format_exc())
        try:
            if 'metadata_file' in locals() and metadata_file is not None:
                with open(metadata_file, "w") as f:
                    json.dump(
                        {
                            KEY_RECOVER_STATUS: RECOVER_FAIL,
                            "error": str(e),
                        },
                        f,
                        indent=4,
                    )
        except Exception:
            pass
        return "recover_fail"
    finally:
        os.chdir(original_dir)


def init_worker():
    """
    When ProcessPoolExecutor workers are initialized, we
    """
    global this_worker_id, worker_tempdirs
    this_worker_id = int(current_process().name.split("-")[-1])
    worker_tempdirs[this_worker_id] = f"mirror_tmps/{str(uuid.uuid4())[:8]}"
    print(
        f"Initialized worker {this_worker_id} with temp dir {worker_tempdirs[this_worker_id]} (PID: {os.getpid()})"
    )
    os.makedirs(worker_tempdirs[this_worker_id], exist_ok=True)


def sweb_inst_to_rp(inst: dict) -> RepoProfile:
    owner, repo = inst["repo"].split("/")
    rps = [x for x in registry.values() if x.owner == owner and x.repo == repo]
    if len(rps) == 0:
        raise ValueError(
            f"{repo} not found in SWE-smith registry, create profile for repo under swesmith/profiles"
        )
    elif len(rps) > 1:
        print(f"Multiple profiles for {owner}/{repo} found")
        for i, rp in enumerate(rps):
            print(f"{i + 1}. {rp.commit}")
        idx = int(input("Enter index of RepoProfile to use: "))
        return rps[idx]
    return rps[0]


def main(
    sweb_insts_files: list,
    model: str,
    redo_existing: bool,
    redo_skipped: bool,
    api_key: str | None = None,
    num_processes: int = 1,
    max_files: int = 8,
    max_lines: int = 500,
    max_file_lines: int = 10000,
):
    global worker_tempdirs, this_worker_id

    assert not (redo_existing and redo_skipped), (
        "Cannot redo existing and skipped at the same time"
    )

    all_instances = []
    seen_repo_inst_ids = set()

    for sweb_insts_file in sweb_insts_files:
        if any([sweb_insts_file.endswith(ext) for ext in [".jsonl", ".jsonl.all"]]):
            file_instances = [json.loads(line) for line in open(sweb_insts_file)]
        elif sweb_insts_file.endswith(".json"):
            file_instances = json.load(open(sweb_insts_file))
        else:
            raise ValueError(
                f"Invalid file format for {sweb_insts_file}. Must be .json or .jsonl"
            )
        for inst in file_instances:
            inst[MIRROR_PR] = sweb_inst_to_rp(inst).repo_name
            repo_inst_id = (inst[MIRROR_PR], inst[KEY_INSTANCE_ID])
            if repo_inst_id in seen_repo_inst_ids:
                raise ValueError(f"Duplicate instance ID: {inst[KEY_INSTANCE_ID]}")
            seen_repo_inst_ids.add(repo_inst_id)
            all_instances.append(inst)
    print(f"Found {len(all_instances)} instances across {len(sweb_insts_files)} files")

    to_process = []
    already_completed = {RECOVER_SUCCESS: [], RECOVER_FAIL: [], RECOVER_SKIPPED: []}
    all_repos = set()
    repos_to_process = set()
    for inst in all_instances:
        should_process, status = should_process_instance(
            inst, inst[MIRROR_PR], redo_existing, redo_skipped
        )
        if should_process:
            to_process.append(inst)
        elif status:
            already_completed[status].append(inst)
        all_repos.add(inst[MIRROR_PR])
        if should_process:
            repos_to_process.add(inst[MIRROR_PR])
    print("Pre-processing report:")
    print(f"- Repos to process: {len(repos_to_process)}")
    print(f"- Instances to process: {len(to_process)}")
    print(
        f"- Already completed instances: {sum(len(v) for v in already_completed.values())}"
    )
    print(f"- All repos: {len(all_repos)}")
    print(f"  - Success: {len(already_completed[RECOVER_SUCCESS])}")
    print(f"  - Failed: {len(already_completed[RECOVER_FAIL])}")
    print(f"  - Skipped: {len(already_completed[RECOVER_SKIPPED])}")
    if not to_process:
        print("No instances to process. Exiting.")
        return

    num_processes = min(num_processes, len(to_process))
    print(f"Using {num_processes} processes")

    task_args = []
    for inst in to_process:
        task_args.append(
            (
                inst,
                inst[MIRROR_PR],
                model,
                api_key,
                max_files,
                max_lines,
                max_file_lines,
            )
        )

    pbar = tqdm(total=len(task_args))

    results = {"skipped": 0, "recover_success": 0, "recover_fail": 0}
    if num_processes > 1:
        worker_pids = {}

        with ProcessPoolExecutor(
            max_workers=num_processes, initializer=init_worker
        ) as pool:
            try:
                futures = [
                    pool.submit(process_single_instance, *args) for args in task_args
                ]

                # Store worker process PIDs
                for executor in pool._processes.values():
                    worker_pids[executor.pid] = executor
                print(f"Worker PIDs: {list(worker_pids.keys())}")

                for future in as_completed(futures):
                    result = future.result()
                    if result in results:
                        results[result] += 1
                    else:
                        print(f"Unknown result: {result}")
                    pbar.update(1)
            except KeyboardInterrupt:
                print("\nKeyboard interrupt. Forcefully killing all workers...")
                print(f"Partial results: {results}")
                for pid in worker_pids:
                    try:
                        print(f"Sending SIGKILL to worker PID {pid}")
                        os.kill(pid, signal.SIGKILL)
                    except OSError as e:
                        print(f"Error killing process {pid}: {e}")
                pool.shutdown(wait=False)
                raise KeyboardInterrupt
            finally:
                for temp_dir in worker_tempdirs.values():
                    if os.path.exists(temp_dir):
                        shutil.rmtree(temp_dir)
    else:
        # Single process mode
        worker_tempdirs = {0: f"tmp_{str(uuid.uuid4())[:8]}"}
        os.makedirs(worker_tempdirs[0], exist_ok=True)
        this_worker_id = 0
        for args in task_args:
            result = process_single_instance(*args)
            if result in results:
                results[result] += 1
            pbar.update(1)
        if os.path.exists(worker_tempdirs[0]):
            shutil.rmtree(worker_tempdirs[0])

    pbar.close()

    # Update results with already completed instances if needed
    if not redo_existing and not redo_skipped:
        results["skipped"] += len(already_completed[RECOVER_SKIPPED])
        results["recover_success"] += len(already_completed[RECOVER_SUCCESS])
        results["recover_fail"] += len(already_completed[RECOVER_FAIL])
    elif redo_existing and not redo_skipped:
        results["skipped"] += len(already_completed[RECOVER_SKIPPED])
    elif redo_skipped and not redo_existing:
        results["recover_success"] += len(already_completed[RECOVER_SUCCESS])
        results["recover_fail"] += len(already_completed[RECOVER_FAIL])

    print(f"\nFinal summary for ({len(all_instances)} instances)")
    print(f"- Skipped {results['skipped']}")
    print(f"- Recovery Success: {results['recover_success']}")
    print(f"- Recovery Fail: {results['recover_fail']}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Given a pull request, mirror the bug in a repository."
    )
    parser.add_argument(
        "sweb_insts_files",
        type=str,
        nargs="+",
        help="Paths to one or more swe-bench-task-instances.json[l] files.",
    )
    parser.add_argument(
        "--model",
        type=str,
        help="Model to use for bug generation",
        default="openai/gpt-4o",
    )
    parser.add_argument(
        "--redo_existing",
        action="store_true",
        help="Whether to redo existing bugs",
        default=False,
    )
    parser.add_argument(
        "--redo_skipped",
        action="store_true",
        help="Whether to redo bugs skipped due to failing recovery criteria",
        default=False,
    )
    parser.add_argument(
        "-n",
        "--num_processes",
        type=int,
        default=1,
    )
    parser.add_argument(
        "-f",
        "--max_files",
        type=int,
        default=8,
        help="Maximum number of files that can be changed for recovery attempt (default: 8)",
    )
    parser.add_argument(
        "-l",
        "--max_lines",
        type=int,
        default=500,
        help="Maximum total lines that can be changed for recovery attempt (default: 500)",
    )
    parser.add_argument(
        "-m",
        "--max_file_lines",
        type=int,
        default=10000,
        help="Maximum lines in a single changed file for recovery attempt (default: 10000)",
    )
    args = parser.parse_args()
    main(**vars(args))
