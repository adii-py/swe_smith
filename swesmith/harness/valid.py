"""
Purpose: Transform a bunch of patches that cause bugs into a SWE-bench style dataset.

Usage: python -m swesmith.harness.valid logs/bug_gen/*_patches.json --workers #
"""

import argparse
import json
import os
import shutil
import subprocess
import threading

from collections import defaultdict
from pathlib import Path
from swebench.harness.constants import (
    KEY_INSTANCE_ID,
    KEY_PREDICTION,
    FAIL_TO_PASS,
    LOG_REPORT,
    LOG_TEST_OUTPUT,
    PASS_TO_PASS,
)
from swebench.harness.docker_build import close_logger
from tqdm.auto import tqdm
from swesmith.constants import (
    KEY_PATCH,
    KEY_TIMED_OUT,
    LOG_TEST_OUTPUT_PRE_GOLD,
    REF_SUFFIX,
    LOG_DIR_RUN_VALIDATION,
)
from swesmith.harness.grading import get_valid_report
from swesmith.harness.utils import run_patch_in_container, run_threadpool
from swesmith.profiles import registry


def verify_docker_image(image_name: str) -> tuple[bool, str]:
    """Check that the validation Docker image exists locally."""
    try:
        result = subprocess.run(
            ["docker", "image", "inspect", image_name],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            return True, "image_ok"
        return False, (result.stderr or "image not found")[:500]
    except Exception as e:
        return False, str(e)


def diagnose_instance_patches(instance: dict) -> dict:
    """Lightweight pre-validation diagnostics for patch fields."""
    diag = {
        "has_bug_patch": bool(instance.get(KEY_PATCH, "").strip()),
        "has_test_patch": bool(instance.get("test_patch", "").strip()),
        "f2p_count": len(instance.get(FAIL_TO_PASS, [])),
        "p2p_count": len(instance.get(PASS_TO_PASS, [])),
    }
    patch = instance.get(KEY_PATCH, "")
    if patch:
        diag["patch_files"] = len(
            [ln for ln in patch.splitlines() if ln.startswith("diff --git")]
        )
        diag["patch_hunks"] = patch.count("@@")
    return diag


def print_report(log_dir: Path) -> None:
    time_outs, f2p_none, f2p_some, other = 0, 0, 0, 0
    for folder in os.listdir(log_dir):
        if LOG_REPORT in os.listdir(log_dir / folder):
            with open(log_dir / folder / LOG_REPORT, "r") as f:
                report = json.load(f)
            if KEY_TIMED_OUT in report:
                time_outs += 1
            elif len(report[FAIL_TO_PASS]) > 0:
                f2p_some += 1
            elif len(report[FAIL_TO_PASS]) == 0:
                f2p_none += 1
            else:
                other += 1
    print(f"Total instances: {len(os.listdir(log_dir))}")
    print(f"- Timed out: {time_outs}")
    print(f"- Fail to pass: 0 ({f2p_none}); 1+ ({f2p_some})")
    print(f"- Other: {other}")


def run_validation(instance: dict, phase: str = "full") -> dict:
    """
    Run per-instance validation. Steps are generally:
    1. Run the patch on the instance.
    2. Get the report from the test output.

    Returns:
        dict: Result with keys 'status'
        status can be: 'timeout', 'fail', '0_f2p', '1+_f2p'
    """
    instance_id = instance[KEY_INSTANCE_ID]
    rp = registry.get_from_inst(instance)
    patch_diag = diagnose_instance_patches(instance)
    image_ok, image_msg = verify_docker_image(rp.image_name)
    valid_folder = LOG_DIR_RUN_VALIDATION / instance["repo"]
    inst_dir = valid_folder / instance_id
    val_clean_path = inst_dir / LOG_TEST_OUTPUT_PRE_GOLD
    val_buggy_path = inst_dir / LOG_TEST_OUTPUT
    report_path = inst_dir / LOG_REPORT
    logger = None

    if not image_ok:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        with open(report_path, "w") as f:
            f.write(
                json.dumps(
                    {
                        "validation_error": "docker_image_missing",
                        "image": rp.image_name,
                        "message": image_msg,
                        "patch_diagnostics": patch_diag,
                    },
                    indent=4,
                )
            )
        return {"status": "fail"}

    if rp.min_pregold and phase in ("full", "clean", "pregold"):
        test_patch_only = instance.get("test_patch")
        ref_inst_id = f"{instance[KEY_INSTANCE_ID]}{REF_SUFFIX}"
        if not val_clean_path.exists() or phase == "pregold":
            logger, timed_out = run_patch_in_container(
                {**instance, KEY_INSTANCE_ID: ref_inst_id},
                instance["repo"],
                LOG_DIR_RUN_VALIDATION,
                rp.timeout,
                patch=test_patch_only if test_patch_only else None,
            )
            close_logger(logger)
            if timed_out:
                logger.info(f"Timed out (clean/test-only) for {instance_id}.")
                report_path.parent.mkdir(parents=True, exist_ok=True)
                with open(report_path, "w") as f:
                    f.write(
                        json.dumps(
                            {KEY_TIMED_OUT: True, "timeout": rp.timeout, "phase": "clean"},
                            indent=4,
                        )
                    )
                if (valid_folder / ref_inst_id).exists():
                    shutil.rmtree(valid_folder / ref_inst_id)
                return {"status": "timeout"}

            inst_dir.mkdir(parents=True, exist_ok=True)
            ref_test_output = valid_folder / ref_inst_id / LOG_TEST_OUTPUT
            if ref_test_output.exists():
                shutil.copy(ref_test_output, val_clean_path)
            if (valid_folder / ref_inst_id).exists():
                shutil.rmtree(valid_folder / ref_inst_id)
        else:
            print(f"Skipping clean run for {instance_id} (found {val_clean_path})")

    if phase in ("full", "buggy", "postgold"):
        test_patch = instance.get("test_patch")
        bug_patch = instance[KEY_PATCH]
        combined_patch = None
        if test_patch and bug_patch:
            combined_patch = test_patch + "\n" + bug_patch
        elif bug_patch:
            combined_patch = bug_patch
        elif test_patch:
            combined_patch = test_patch

        if not val_buggy_path.exists() or phase == "postgold":
            logger, timed_out = run_patch_in_container(
                instance,
                instance["repo"],
                LOG_DIR_RUN_VALIDATION,
                rp.timeout,
                patch=combined_patch,
            )
            if timed_out:
                logger.info(f"Timed out (buggy) for {instance_id}.")
                with open(report_path, "w") as f:
                    f.write(
                        json.dumps(
                            {KEY_TIMED_OUT: True, "timeout": rp.timeout, "phase": "buggy"},
                            indent=4,
                        )
                    )
                close_logger(logger)
                return {"status": "timeout"}
        else:
            print(f"Skipping buggy run for {instance_id} (found {val_buggy_path})")

    if phase in ("full", "grade") and val_clean_path.exists() and val_buggy_path.exists():
        pass
    elif phase == "grade":
        report_path.parent.mkdir(parents=True, exist_ok=True)
        with open(report_path, "w") as f:
            f.write(
                json.dumps(
                    {
                        "validation_error": "missing_test_outputs",
                        "clean_log": str(val_clean_path),
                        "buggy_log": str(val_buggy_path),
                    },
                    indent=4,
                )
            )
        return {"status": "fail"}

    if phase in ("clean", "pregold", "buggy", "postgold"):
        return {"status": "phase_done"}

    val_pregold_path = val_buggy_path
    val_postgold_path = val_clean_path
    if not val_pregold_path.exists():
        logger.info(f"Pre-gold for {instance_id} failed to run. Exiting early.")
        with open(report_path, "w") as f:
            f.write(
                json.dumps(
                    {KEY_TIMED_OUT: True, "missing_pregold_output": True}, indent=4
                )
            )
        if logger is not None:
            close_logger(logger)
        return {"status": "fail"}

    # Get report from test output
    logger.info(f"Grading answer for {instance_id}...")
    report = get_valid_report(
        val_pregold_path=val_pregold_path,
        val_postgold_path=val_postgold_path,
        instance=instance,
    )
    report["patch_diagnostics"] = patch_diag
    report["docker_image"] = rp.image_name
    logger.info(f"Report: {json.dumps(report)}")

    # Write report to report.json
    with open(report_path, "w") as f:
        f.write(json.dumps(report, indent=4))

    if logger is not None:
        close_logger(logger)
    if len(report.get(FAIL_TO_PASS, [])) == 0:
        return {"status": "0_f2p"}
    else:
        return {"status": "1+_f2p"}


def main(
    bug_patches: str,
    workers: int,
    redo_existing: bool = False,
    phase: str = "full",
) -> None:
    # Bug patch should be a dict that looks like this:
    # {
    #     "instance_id": <instance_id>,
    #     "patch" / "model_patch": <bug inducing patch>,
    #     "repo": <mirror repo name>,
    # }
    print(f"Running validation for {bug_patches}...")
    with open(bug_patches, "r") as f:
        bug_patches = json.load(f)
    bug_patches = [
        {
            **x,
            KEY_PATCH: x.get(KEY_PATCH, x.get(KEY_PREDICTION, x.get("bug_patch"))),
        }
        for x in bug_patches
    ]
    print(f"Found {len(bug_patches)} candidate patches.")

    completed = []
    log_dir_parent = None
    for repo in set([bp["repo"] for bp in bug_patches]):
        log_dir_parent = LOG_DIR_RUN_VALIDATION / repo
        log_dir_parent.mkdir(parents=True, exist_ok=True)
        if not redo_existing and log_dir_parent.exists():
            for folder in os.listdir(log_dir_parent):
                # Identify completed instances (does report.json exist)
                log_report_path = log_dir_parent / folder / LOG_REPORT
                if log_report_path.exists():
                    completed.append(folder)
    if len(completed) > 0:
        print(f"Skipping {len(completed)} instances... (--redo_existing to not skip)")
        bug_patches = [x for x in bug_patches if x[KEY_INSTANCE_ID] not in completed]

    # Group patches by image_name:
    repo_to_bug_patches = defaultdict(list)
    for bp in bug_patches:
        repo_to_bug_patches[bp["repo"]].append(bp)

    # Log
    print("Will run validation for these images:")
    for repo, patches in repo_to_bug_patches.items():
        print(f"- {repo}: {len(patches)} patches")

    # Run validation
    payloads = list()
    for repo, repo_bug_patches in repo_to_bug_patches.items():
        rp = registry.get(repo)
        ref_inst = f"{rp.repo_name}{REF_SUFFIX}"
        ref_dir = LOG_DIR_RUN_VALIDATION / repo / ref_inst
        if not rp.min_pregold and not os.path.exists(ref_dir):
            # Run pytest for each repo/commit to get pre-gold behavior.
            print(f"Running pre-gold for {repo}...")
            logger, timed_out = run_patch_in_container(
                {KEY_INSTANCE_ID: ref_inst},
                repo,
                LOG_DIR_RUN_VALIDATION,
                rp.timeout_ref,
            )
            close_logger(logger)
            if timed_out:
                # If timed out, skip this repo/commit (remove log directory)
                print(
                    f"Timed out for {repo}, not running validation. (Increase --timeout?)"
                )
                shutil.rmtree(ref_dir)
                continue

        # Add payloads
        for bug_patch in repo_bug_patches:
            payloads.append((bug_patch,))

    # Check if we have any payloads to process
    if len(payloads) == 0:
        print("No patches to run.")
        if log_dir_parent:
            print_report(log_dir_parent)
        return

    # Initialize progress bar and stats
    stats = {"fail": 0, "timeout": 0, "0_f2p": 0, "1+_f2p": 0}
    pbar = tqdm(total=len(payloads), desc="Validation", postfix=stats)
    lock = threading.Lock()

    # Create a wrapper function for threadpool that updates progress bar
    def run_validation_with_progress(*args):
        instance = args[0] if args else {}
        result = run_validation(instance, phase=phase)
        with lock:
            stats[result["status"]] += 1
            pbar.set_postfix(stats)
            pbar.update()
        return result

    run_threadpool(run_validation_with_progress, payloads, workers)

    # Close progress bar
    pbar.close()

    print("All instances run.")
    print_report(log_dir_parent)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Transform a bunch of patches that cause bugs into a SWE-bench style dataset."
    )
    parser.add_argument(
        "bug_patches",
        type=str,
        help="Json file containing bug patches.",
    )
    parser.add_argument(
        "-w", "--workers", type=int, default=4, help="Number of workers to use."
    )
    parser.add_argument(
        "--redo_existing",
        action="store_true",
        help="Redo completed validation instances.",
    )
    parser.add_argument(
        "--phase",
        type=str,
        default="full",
        choices=["full", "clean", "pregold", "buggy", "postgold", "grade"],
        help="Validation phase: full (default), clean/pregold (test patch only), buggy/postgold (test+bug), grade (compare saved logs).",
    )
    args = parser.parse_args()
    main(**vars(args))
