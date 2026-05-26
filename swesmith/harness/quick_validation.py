"""
Quick validation - test only the analytics crate with our fixed patches.
This overrides the default multi-crate test command for speed.
"""

import argparse
import json
import os
from pathlib import Path
from swebench.harness.constants import KEY_INSTANCE_ID, LOG_REPORT
from swesmith.harness.utils import run_patch_in_container
from swesmith.harness.grading import get_valid_report
from swesmith.profiles import registry
from swesmith.constants import (
    KEY_PATCH,
    LOG_DIR_RUN_VALIDATION,
    LOG_TEST_OUTPUT,
)


def quick_validate_instance(instance: dict) -> dict:
    """Validate a single instance with analytics-only testing."""
    instance_id = instance[KEY_INSTANCE_ID]
    repo = instance["repo"]

    print(f"\n{'=' * 60}")
    print(f"Validating: {instance_id}")
    print(f"{'=' * 60}")

    # Create instance with overridden test command
    # Force analytics-only testing with --lib flag
    instance_with_cmd = instance.copy()
    instance_with_cmd["test_cmd"] = (
        "CARGO_BUILD_JOBS=1 cargo test -p analytics --lib --no-fail-fast -- --nocapture 2>&1"
    )

    valid_folder = LOG_DIR_RUN_VALIDATION / repo
    report_path = valid_folder / instance_id / LOG_REPORT
    val_pregold_path = valid_folder / instance_id / LOG_TEST_OUTPUT

    # Get repo profile
    rp = registry.get(repo)

    # Run validation
    print("  Running tests (analytics crate only)...")
    logger, timed_out = run_patch_in_container(
        instance_with_cmd,
        repo,
        LOG_DIR_RUN_VALIDATION,
        timeout=600,  # 10 minute timeout
        patch=instance.get(KEY_PATCH),
    )

    if timed_out:
        print("  [TIMEOUT]")
        return {
            "instance_id": instance_id,
            "status": "timeout",
            "report": {
                "FAIL_TO_PASS": [],
                "PASS_TO_PASS": [],
                "FAIL_TO_FAIL": [],
                "PASS_TO_FAIL": [],
            },
        }

    if not val_pregold_path.exists():
        print("  [ERROR] Test output not found")
        return {
            "instance_id": instance_id,
            "status": "fail",
            "report": {
                "FAIL_TO_PASS": [],
                "PASS_TO_PASS": [],
                "FAIL_TO_FAIL": [],
                "PASS_TO_FAIL": [],
            },
        }

    # Grade results
    print("  Grading results...")
    report = get_valid_report(
        val_pregold_path=val_pregold_path,
        val_postgold_path=None,  # Single run for now
        instance=instance,
    )

    f2p_count = len(report.get("FAIL_TO_PASS", []))
    p2p_count = len(report.get("PASS_TO_PASS", []))

    print(f"  Results: {f2p_count} F2P, {p2p_count} P2P")

    return {
        "instance_id": instance_id,
        "status": "success" if f2p_count > 0 else "0_f2p",
        "report": report,
    }


def main(bug_patches_file: str):
    print(f"Loading instances from: {bug_patches_file}")

    with open(bug_patches_file, "r") as f:
        instances = json.load(f)

    print(f"Loaded {len(instances)} instances")

    results = []
    for instance in instances:
        result = quick_validate_instance(instance)
        results.append(result)

    # Print summary
    print(f"\n{'=' * 60}")
    print("VALIDATION SUMMARY")
    print(f"{'=' * 60}")

    success_count = sum(1 for r in results if r["status"] == "success")
    zero_f2p_count = sum(1 for r in results if r["status"] == "0_f2p")

    for r in results:
        print(f"\n{r['instance_id']}:")
        print(f"  Status: {r['status']}")
        print(f"  F2P: {len(r['report'].get('FAIL_TO_PASS', []))}")
        print(f"  P2P: {len(r['report'].get('PASS_TO_PASS', []))}")

    print(f"\n{'=' * 60}")
    print(f"Total: {len(results)} instances")
    print(f"With F2P: {success_count}")
    print(f"Zero F2P: {zero_f2p_count}")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("bug_patches", help="JSON file with bug patches")
    args = parser.parse_args()
    main(args.bug_patches)
