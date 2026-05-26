"""
Purpose: Validation Option 2 - Isolate test execution to specific crate.

This version modifies validation to:
1. Detect which crate the bug patch modifies
2. Run tests ONLY for that specific crate (not dependencies)
3. Use --lib flag to avoid doc tests and integration tests that might trigger deps
4. Compare pre-gold vs post-gold results

Usage: python -m swesmith.harness.valid_option2 logs/bug_gen/*_patches.json --workers #
"""

import argparse
import json
import os
import shutil
import threading
import re

from collections import defaultdict
from pathlib import Path
from unidiff import PatchSet
from swebench.harness.constants import (
    KEY_INSTANCE_ID,
    KEY_PREDICTION,
    FAIL_TO_PASS,
    LOG_REPORT,
    LOG_TEST_OUTPUT,
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


def extract_modified_crate(patch_content: str) -> str | None:
    """
    Extract the crate name from the patch.
    Looks for paths like 'crates/analytics/src/...' and returns 'analytics'
    """
    if not patch_content:
        return None

    try:
        patch = PatchSet(patch_content)
        for file in patch:
            # Extract path like "crates/analytics/src/query.rs"
            path = file.path
            if path.startswith("crates/"):
                # Extract crate name: "crates/analytics/..." -> "analytics"
                parts = path.split("/")
                if len(parts) >= 2:
                    return parts[1]  # Return "analytics"
    except Exception as e:
        print(f"Warning: Could not parse patch: {e}")

    return None


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


def run_validation_option2(instance: dict) -> dict:
    """
    Run per-instance validation with isolated crate testing.

    Args:
        instance: The bug patch instance

    Returns:
        dict: Result with keys 'status'
        status can be: 'timeout', 'fail', '0_f2p', '1+_f2p'
    """
    instance_id = instance[KEY_INSTANCE_ID]
    rp = registry.get_from_inst(instance)
    valid_folder = LOG_DIR_RUN_VALIDATION / instance["repo"]
    val_postgold_path = (
        valid_folder / f"{instance['repo']}{REF_SUFFIX}" / LOG_TEST_OUTPUT
    )
    report_path = valid_folder / instance_id / LOG_REPORT

    # Determine which crate to test
    bug_patch = instance.get(KEY_PATCH, "")
    target_crate = extract_modified_crate(bug_patch)

    if target_crate:
        print(f"[{instance_id}] Target crate: {target_crate}")
        # Override test command to only test the specific crate
        # Use --lib to test only library unit tests (not integration tests or doc tests)
        # Add --features v1 for Hyperswitch, linker workaround for ARM64, and skip external deps
        instance["test_cmd"] = (
            f"apt-get update && apt-get install -y lld clang && RUSTFLAGS='-C linker=clang -C link-arg=-fuse-ld=lld' CARGO_BUILD_JOBS=1 cargo test -p {target_crate} --lib --features v1 --no-fail-fast -- --nocapture --skip redis --skip postgres --skip db --skip database --skip integration 2>&1"
        )
    else:
        print(
            f"[{instance_id}] Could not detect target crate, using default test command"
        )

    # Run TWO phases to get f2p:
    # Phase 1 (Pre-gold): Apply bug patch + test patch, run tests (should FAIL)
    # Phase 2 (Post-gold): Remove bug patch, keep test patch, run tests (should PASS)

    # Get test patch if available
    test_patch = instance.get("test_patch", "")

    # Combine bug patch + test patch for pre-gold
    combined_patch = bug_patch if bug_patch else ""
    if test_patch:
        combined_patch = combined_patch + "\n" + test_patch if combined_patch else test_patch
        print(f"[{instance_id}] Including test patch")

    # Phase 1: Pre-gold with bug patch + test patch applied
    logger_pre, timed_out_pre = run_patch_in_container(
        instance,
        instance["repo"],
        LOG_DIR_RUN_VALIDATION,
        rp.timeout,
        patch=combined_patch if combined_patch else None,
    )

    if timed_out_pre:
        logger_pre.info(f"Timed out (pre-gold) for {instance_id}.")
        report_path.parent.mkdir(parents=True, exist_ok=True)
        with open(report_path, "w") as f:
            f.write(json.dumps({KEY_TIMED_OUT: True, "timeout": rp.timeout, "phase": "pre-gold"}, indent=4))
        close_logger(logger_pre)
        return {"status": "timeout"}

    val_pregold_path = valid_folder / instance_id / LOG_TEST_OUTPUT
    if not val_pregold_path.exists():
        logger_pre.info(f"Pre-gold for {instance_id} failed to run. Exiting early.")
        report_path.parent.mkdir(parents=True, exist_ok=True)
        with open(report_path, "w") as f:
            f.write(json.dumps({KEY_TIMED_OUT: True, "missing_pregold_output": True}, indent=4))
        close_logger(logger_pre)
        return {"status": "fail"}

    close_logger(logger_pre)

    # Phase 2: Post-gold WITHOUT bug patch but WITH test patch
    # Create a new instance for post-gold with only test patch
    postgold_instance_id = f"{instance_id}{REF_SUFFIX}"
    postgold_instance = {**instance, KEY_INSTANCE_ID: postgold_instance_id}

    logger_post, timed_out_post = run_patch_in_container(
        postgold_instance,
        instance["repo"],
        LOG_DIR_RUN_VALIDATION,
        rp.timeout,
        patch=test_patch if test_patch else None,  # Only test patch, no bug patch
    )

    if timed_out_post:
        logger_post.info(f"Timed out (post-gold) for {instance_id}.")
        with open(report_path, "w") as f:
            f.write(json.dumps({KEY_TIMED_OUT: True, "timeout": rp.timeout, "phase": "post-gold"}, indent=4))
        close_logger(logger_post)
        return {"status": "timeout"}

    val_postgold_path = valid_folder / postgold_instance_id / LOG_TEST_OUTPUT

    # Get report comparing pre-gold vs post-gold
    logger_post.info(f"Grading answer for {instance_id}...")
    report = get_valid_report(
        val_pregold_path=val_pregold_path,
        val_postgold_path=val_postgold_path,
        instance=instance,
    )
    logger_post.info(f"Report: {json.dumps(report)}")

    # Write report to report.json
    with open(report_path, "w") as f:
        f.write(json.dumps(report, indent=4))

    # Return result based on the report
    close_logger(logger_post)
    if len(report.get(FAIL_TO_PASS, [])) == 0:
        return {"status": "0_f2p"}
    else:
        return {"status": "1+_f2p"}


def main(
    bug_patches: str,
    workers: int,
    redo_existing: bool = False,
) -> None:
    print(f"Running validation (Option 2 - Crate Isolation) for {bug_patches}...")
    with open(bug_patches, "r") as f:
        bug_patches_data = json.load(f)
    bug_patches_data = [
        {
            **x,
            KEY_PATCH: x.get(KEY_PATCH, x.get(KEY_PREDICTION, x.get("bug_patch"))),
        }
        for x in bug_patches_data
    ]
    print(f"Found {len(bug_patches_data)} candidate patches.")

    completed = []
    log_dir_parent = None
    for repo in set([bp["repo"] for bp in bug_patches_data]):
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
        bug_patches_data = [
            x for x in bug_patches_data if x[KEY_INSTANCE_ID] not in completed
        ]

    # Group patches by image_name:
    repo_to_bug_patches = defaultdict(list)
    for bp in bug_patches_data:
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
        result = run_validation_option2(instance)
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
        description="Validation Option 2 - Isolate test execution to specific crate."
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
    args = parser.parse_args()
    main(**vars(args))
