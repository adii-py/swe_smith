"""
Purpose: Given a repository, generate bug patches for functions/classes/objects in the repository.

Usage: python -m swesmith.bug_gen.llm.modify \
    --n_bugs <n_bugs> \
    --config_file <config_file> \
    --model <model> \
    repo  # e.g., tkrajina__gpxpy.09fc46b3

Where model follows the litellm format.

Example:

python -m swesmith.bug_gen.llm.modify tkrajina__gpxpy.09fc46b3 --config_file configs/bug_gen/class_basic.yml --model claude-3-7-sonnet-20250219 --n_bugs 1
"""

import argparse
import dataclasses
import shutil
import json
import litellm
import logging
import os
import random
import yaml

from concurrent.futures import ThreadPoolExecutor, as_completed
from dotenv import load_dotenv
from litellm import completion
from litellm.cost_calculator import completion_cost
from litellm.exceptions import BadRequestError
from swesmith.bug_gen.llm.utils import PROMPT_KEYS, extract_code_block
from swesmith.bug_gen.utils import (
    apply_code_change,
    get_bug_directory,
    get_patch,
)
from swesmith.constants import (
    LOG_DIR_BUG_GEN,
    PREFIX_BUG,
    PREFIX_METADATA,
    BugRewrite,
    CodeEntity,
)
from swesmith.profiles import registry
from tqdm.auto import tqdm
from tqdm.contrib.logging import logging_redirect_tqdm
from typing import Any


load_dotenv(dotenv_path=os.getenv("SWEFT_DOTENV_PATH"))

logging.getLogger("LiteLLM").setLevel(logging.WARNING)
litellm.suppress_debug_info = True


def gen_bug_from_code_lm(
    candidate: CodeEntity, configs: dict, n_bugs: int, model: str
) -> list[BugRewrite]:
    """
    Given the source code of a function, return `n` bugs with an LM
    """

    def format_prompt(prompt: str | None, config: dict, candidate: CodeEntity) -> str:
        if not prompt:
            return ""

        prompt_content = {
            field.name: getattr(candidate, field.name)
            for field in dataclasses.fields(candidate)
        }
        # Add commonly used properties that may exist on the candidate
        for attr in ["name", "signature", "stub"]:
            if hasattr(candidate, attr):
                prompt_content[attr] = getattr(candidate, attr)
        # Add file_src_code (full file content) for templates that need it
        try:
            file_path = os.path.join(os.getcwd(), candidate.file_path)
            with open(file_path) as f:
                prompt_content["file_src_code"] = f.read()
        except Exception:
            prompt_content["file_src_code"] = candidate.src_code

        return prompt.format(**prompt_content, **config.get("parameters", {}))

    def get_role(key: str) -> str:
        if key == "system":
            return "system"
        return "user"

    bugs = []
    messages = [
        {"content": format_prompt(configs[k], configs, candidate), "role": get_role(k)}
        for k in PROMPT_KEYS
    ]
    # Remove empty messages
    messages = [x for x in messages if x["content"]]
    # Use litellm config from environment
    api_key = os.getenv("LITE_LLM_API_KEY", os.getenv("OPENAI_API_KEY"))
    api_base = os.getenv("LITE_LLM_URL", os.getenv("OPENAI_API_BASE"))

    response: Any = completion(
        model=model,
        messages=messages,
        n=n_bugs,
        temperature=1,
        api_key=api_key,
        base_url=api_base,
    )
    # Calculate cost with fallback for unknown models
    try:
        total_cost = completion_cost(completion_response=response)
    except Exception:
        # Model not in litellm's known models, estimate cost
        total_cost = 0.0

    for choice in response.choices:
        message = choice.message
        explanation = (
            message.content.split("Explanation:")[-1].strip()
            if "Explanation" in message.content
            else message.content.split("```")[-1].strip()
        )
        bugs.append(
            BugRewrite(
                rewrite=extract_code_block(message.content),
                explanation=explanation,
                cost=total_cost / n_bugs,
                output=message.content,
                strategy="llm",
            )
        )
    return bugs


def main(
    config_file: str,
    model: str,
    n_bugs: int,
    repo: str,
    n_workers: int = 1,
    max_bugs: int = -1,
):
    # Check arguments
    assert os.path.exists(config_file), f"{config_file} not found"
    assert n_bugs > 0, "n_bugs must be greater than 0"
    configs = yaml.safe_load(open(config_file))
    assert all(key in configs for key in PROMPT_KEYS + ["name"]), (
        f"Missing keys in {config_file}"
    )

    # Clone repository, identify valid candidates
    print("Cloning repository...")
    rp = registry.get(repo)
    rp.clone()
    print("Extracting candidates...")
    candidates = rp.extract_entities()
    print(f"{len(candidates)} candidates found in {repo}")
    if not candidates:
        print(f"No candidates found in {repo}.")
        return

    # Filter for runtime Python files (no GPU/build required)
    RUNTIME_PATTERNS = [
        'vllm/config/', 'vllm/utils.py', 'vllm/sequence.py',
        'vllm/sampling_params.py', 'vllm/engine/arg_utils.py',
        'vllm/attention/', 'vllm/model_executor/layers/',
        'vllm/transformers_utils/', 'vllm/usage/'
    ]
    BUILD_PATTERNS = ['setup.py', 'cmake', 'tools/', 'benchmarks/', 'tests/']

    original_count = len(candidates)
    candidates = [
        c for c in candidates
        if hasattr(c, 'complexity') and c.complexity > 5
        and any(pat in c.file_path for pat in RUNTIME_PATTERNS)
        and not any(pat in c.file_path for pat in BUILD_PATTERNS)
    ]
    print(f"Filtered to {len(candidates)} runtime functions (complexity > 5) from {original_count}")

    # Shuffle candidates for file diversity before limiting
    import random
    random.seed(42)  # For reproducibility
    random.shuffle(candidates)

    # Ensure file diversity - track files we've selected from
    if max_bugs > 0:
        diverse_candidates = []
        seen_files = set()
        # First pass: pick one from each file until we have enough
        for c in candidates:
            if c.file_path not in seen_files and len(diverse_candidates) < max_bugs:
                diverse_candidates.append(c)
                seen_files.add(c.file_path)
        # Second pass: fill remaining slots
        for c in candidates:
            if len(diverse_candidates) < max_bugs:
                diverse_candidates.append(c)
        candidates = diverse_candidates
        print(f"Selected {len(candidates)} candidates from {len(seen_files)} different files for diversity")

    # Adjust candidates if max_bugs is specified
    if max_bugs > 0:
        max_candidates = max_bugs // n_bugs
        if max_candidates < len(candidates):
            candidates = candidates[:max_candidates]
            print(
                f"Limited to {len(candidates)} candidates to generate ~{len(candidates) * n_bugs} bugs (max: {max_bugs})"
            )
        else:
            print(f"Will generate {len(candidates) * n_bugs} bugs (max: {max_bugs})")

    print(f"Generating bugs in {repo} using {model}...")

    # Set up logging
    log_dir = LOG_DIR_BUG_GEN / repo
    log_dir.mkdir(parents=True, exist_ok=True)
    print(f"Logging bugs to {log_dir}")

    def validate_bug_challenging(patch: str) -> bool:
        """Ensure bug is non-trivial and challenging"""
        if not patch:
            return False

        # Count changed lines (added + removed)
        diff_lines = patch.count('\n+') + patch.count('\n-')

        # Too small = trivial (1-2 line changes), too big = rewrite
        if not (3 <= diff_lines <= 20):
            return False

        # Must change control flow or data flow
        control_flow_ops = ['if ', 'for ', 'while ', 'try:', 'return ', 'yield ', 'raise ', 'except']
        if not any(op in patch for op in control_flow_ops):
            return False

        return True

    def _process_candidate(candidate: CodeEntity):
        # Track which line ranges have been modified (for deduplication)
        modified_ranges: list[tuple[int, int]] = []

        def get_patch_line_range(patch: str) -> tuple[int, int] | None:
            """Extract the line range modified by a patch"""
            # Parse @@ -start,count +start,count @@ lines
            import re
            matches = re.findall(r'@@ -(\d+),(\d+) \+(\d+),(\d+) @@', patch)
            if matches:
                # Return the start line of the first hunk
                start = int(matches[0][2])  # New file start line
                return (start, start + int(matches[0][3]))
            return None

        def overlaps_existing(new_range: tuple[int, int]) -> bool:
            """Check if new range overlaps with any existing modified range"""
            for existing in modified_ranges:
                # Check for overlap: [a1, a2] overlaps with [b1, b2] if not (a2 < b1 or a1 > b2)
                if not (new_range[1] < existing[0] or new_range[0] > existing[1]):
                    return True
            return False

        # Run bug generation
        bugs = gen_bug_from_code_lm(candidate, configs, n_bugs, model)
        cost, n_bugs_generated, n_generation_failed = sum([x.cost for x in bugs]), 0, 0

        for bug in bugs:
            # Create artifacts
            bug_dir = get_bug_directory(log_dir, candidate)
            bug_dir.mkdir(parents=True, exist_ok=True)
            uuid_str = f"{configs['name']}__{bug.get_hash()}"
            metadata_path = f"{PREFIX_METADATA}__{uuid_str}.json"
            bug_path = f"{PREFIX_BUG}__{uuid_str}.diff"

            try:
                with open(bug_dir / metadata_path, "w") as f:
                    json.dump(bug.to_dict(), f, indent=2)
                apply_code_change(candidate, bug)
                patch = get_patch(repo, reset_changes=True)
                if not patch:
                    raise ValueError("Patch is empty.")
                # Validate bug is challenging
                if not validate_bug_challenging(patch):
                    raise ValueError("Bug not challenging enough (too trivial or too large).")
                # Check for line range overlap with existing bugs for this candidate
                patch_range = get_patch_line_range(patch)
                if patch_range and overlaps_existing(patch_range):
                    raise ValueError(f"Bug overlaps with existing bug on lines {patch_range}.")
                if patch_range:
                    modified_ranges.append(patch_range)
                with open(bug_dir / bug_path, "w") as f:
                    f.write(patch)
            except Exception as e:
                print(
                    f"Error applying bug to {candidate.name} in {candidate.file_path}: {e}",
                )
                # import traceback
                # print(f"Traceback:\n{''.join(traceback.format_exc())}")
                (bug_dir / metadata_path).unlink(missing_ok=True)
                n_generation_failed += 1
                continue
            else:
                n_bugs_generated += 1
        return {
            "cost": cost,
            "n_bugs_generated": n_bugs_generated,
            "n_generation_failed": n_generation_failed,
        }

    stats = {"cost": 0.0, "n_bugs_generated": 0, "n_generation_failed": 0}
    with ThreadPoolExecutor(max_workers=n_workers) as executor:
        futures = [
            executor.submit(_process_candidate, candidate) for candidate in candidates
        ]

        with logging_redirect_tqdm():
            with tqdm(total=len(candidates), desc="Candidates") as pbar:
                for future in as_completed(futures):
                    cost = future.result()
                    for k, v in cost.items():
                        stats[k] += v
                    pbar.set_postfix(stats, refresh=True)
                    pbar.update(1)

    shutil.rmtree(repo)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "repo",
        type=str,
        help="Name of a SWE-smith repository to generate bugs for.",
    )
    parser.add_argument(
        "-c",
        "--config_file",
        type=str,
        help="Configuration file containing bug gen. strategy prompts",
        required=True,
    )
    parser.add_argument(
        "--model",
        type=str,
        help="Model to use for bug generation",
        default="openai/gpt-4o",
    )
    parser.add_argument(
        "-n",
        "--n_bugs",
        type=int,
        help="Number of bugs to generate per entity",
        default=1,
    )
    parser.add_argument(
        "-m",
        "--max_bugs",
        type=int,
        help="Total, maximum number of bugs to generate",
        default=-1,
    )
    parser.add_argument(
        "-w", "--n_workers", type=int, help="Number of workers to use", default=1
    )
    args = parser.parse_args()
    main(**vars(args))
