#!/usr/bin/env python3
"""
Validate PR mirror instances one by one.
Monitor each result, fix issues immediately, retry if 0 F2P.
"""

import json
import subprocess
import os
from pathlib import Path
import time

REPO = "juspay__hyperswitch.fece9bc3"
INPUT_FILE = Path(f"logs/bug_gen/{REPO}/pr_mirror/pr_mirror_with_tests_77.json")


def run_validation_instance(instance_file, instance_id):
    """Run validation for a single instance."""
    cmd = [
        "docker",
        "run",
        "--rm",
        "-v",
        f"{Path.cwd()}/logs:/workspace/logs",
        "-v",
        f"{Path.cwd()}/swesmith:/workspace/swesmith",
        "-v",
        "/var/run/docker.sock:/var/run/docker.sock",
        "-e",
        "PYTHONUNBUFFERED=1",
        "swesmith-validation:latest",
        "bash",
        "-c",
        f"cd /workspace && python3 -m swesmith.harness.valid {instance_file} --workers 1 --redo_existing 2>&1",
    ]

    print(f"  Running: {instance_id}")
    start = time.time()
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    elapsed = time.time() - start

    print(f"  Time: {elapsed:.1f}s")

    # Check for F2P in output
    if "1+_f2p=1" in result.stdout or "1+_f2p" in result.stdout:
        f2p_count = 1
    elif "0_f2p=1" in result.stdout:
        f2p_count = 0
    else:
        f2p_count = -1  # Unknown

    return {
        "stdout": result.stdout,
        "stderr": result.stderr,
        "returncode": result.returncode,
        "f2p_count": f2p_count,
        "elapsed": elapsed,
    }


def check_report(instance_id):
    """Check the report.json for an instance."""
    report_path = Path(f"logs/run_validation/{REPO}/{instance_id}/report.json")

    if not report_path.exists():
        return None

    try:
        with open(report_path) as f:
            report = json.load(f)
        return report
    except:
        return None


def analyze_failure(instance_id, result):
    """Analyze why validation failed."""
    print(f"\n  ⚠ Analyzing failure for {instance_id}...")

    # Check for common errors
    if "Hunk" in result["stderr"] and "expected" in result["stderr"]:
        print("    → Issue: Patch hunk mismatch (wrong line numbers)")
        return "patch_format"

    if "could not compile" in result["stdout"].lower() or "error[" in result["stdout"]:
        print("    → Issue: Compilation error")
        return "compilation"

    if "Timed out" in result["stdout"]:
        print("    → Issue: Timeout")
        return "timeout"

    if result["f2p_count"] == 0:
        print("    → Issue: 0 F2P cases (tests passed both times)")
        return "zero_f2p"

    print("    → Issue: Unknown")
    return "unknown"


def fix_instance(instance, issue_type):
    """Fix the instance based on issue type."""
    print(f"  🔧 Attempting fix for {issue_type}...")

    if issue_type == "patch_format":
        # The issue is patch format - let's simplify to sed-like patches
        # But for now, just mark it
        print("    → Skipping (needs manual fix)")
        return False

    elif issue_type == "compilation":
        # Check what's in test output
        inst_id = instance["instance_id"]
        test_output = Path(f"logs/run_validation/{REPO}/{inst_id}/test_output.txt")
        if test_output.exists():
            with open(test_output) as f:
                content = f.read()
                if "redis" in content.lower():
                    print("    → Redis dependency issue")
                elif "postgres" in content.lower() or "database" in content.lower():
                    print("    → Database dependency issue")
        return False

    elif issue_type == "zero_f2p":
        # The bug didn't break any tests
        # Need stronger bug or better tests
        print("    → Bug not detected by tests")
        return False

    return False


def main():
    print("=" * 60)
    print("VALIDATING INSTANCES ONE BY ONE")
    print("=" * 60)
    print()

    # Load instances
    with open(INPUT_FILE) as f:
        instances = json.load(f)

    print(f"Total instances: {len(instances)}")
    print()

    # Track results
    results = {"success": [], "failed": [], "skipped": []}

    # Process first 10 instances as pilot
    pilot_batch = instances[:10]

    print(f"Processing pilot batch: {len(pilot_batch)} instances")
    print()

    for i, inst in enumerate(pilot_batch, 1):
        instance_id = inst["instance_id"]

        print(f"\n[{i}/{len(pilot_batch)}] Processing: {instance_id}")
        print("-" * 60)

        # Create single-instance file
        single_file = Path(f"/tmp/single_{instance_id}.json")
        with open(single_file, "w") as f:
            json.dump([inst], f, indent=2)

        # Run validation
        result = run_validation_instance(single_file, instance_id)

        # Check result
        if result["returncode"] == 0 and result["f2p_count"] > 0:
            print(f"  ✅ SUCCESS: F2P = {result['f2p_count']}")
            results["success"].append(instance_id)
        elif result["returncode"] == 0 and result["f2p_count"] == 0:
            print(f"  ⚠️  0 F2P - analyzing...")
            issue = analyze_failure(instance_id, result)

            # Try to fix
            if fix_instance(inst, issue):
                print("  🔄 Retrying with fix...")
                # Retry logic here if we implemented fixes
            else:
                print("  ❌ Could not fix automatically")
                results["failed"].append(
                    {
                        "instance_id": instance_id,
                        "issue": issue,
                        "reason": "0 F2P or unfixable",
                    }
                )
        else:
            print(f"  ❌ FAILED: Exit code {result['returncode']}")
            issue = analyze_failure(instance_id, result)

            if fix_instance(inst, issue):
                print("  🔄 Retrying with fix...")
            else:
                print("  ❌ Could not fix automatically")
                results["failed"].append(
                    {
                        "instance_id": instance_id,
                        "issue": issue,
                        "reason": f"Exit code {result['returncode']}",
                    }
                )

        # Show summary so far
        print(
            f"\n  Progress: {len(results['success'])} success, {len(results['failed'])} failed"
        )

    # Final summary
    print()
    print("=" * 60)
    print("PILOT BATCH RESULTS")
    print("=" * 60)
    print()
    print(
        f"Success: {len(results['success'])}/{len(pilot_batch)} ({len(results['success']) / len(pilot_batch) * 100:.1f}%)"
    )
    print(f"Failed: {len(results['failed'])}/{len(pilot_batch)}")
    print()

    if results["failed"]:
        print("Failed instances:")
        for fail in results["failed"]:
            print(f"  - {fail['instance_id']}: {fail['issue']} ({fail['reason']})")

    print()
    print("Next steps:")
    if len(results["success"]) / len(pilot_batch) > 0.5:
        print("  ✅ Good success rate! Continue with remaining instances.")
    else:
        print("  ⚠️  Low success rate. Need to fix common issues first.")

    # Save detailed results
    results_file = Path("logs/pr_mirror_pilot_results.json")
    with open(results_file, "w") as f:
        json.dump(results, f, indent=2)

    print(f"\nDetailed results saved to: {results_file}")


if __name__ == "__main__":
    main()
