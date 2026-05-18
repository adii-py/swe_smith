#!/usr/bin/env python3
"""Actually execute targeted tests in Docker containers to get PASS_TO_PASS data."""
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from datetime import datetime

INSTANCES_PATH = "/Users/aditya.singh.001/Desktop/SWE-smith/logs/bug_gen/mirror_instances_for_validation.json"
OUTPUT_DIR = Path("/Users/aditya.singh.001/Desktop/SWE-smith/logs/targeted_validation")
IMAGE_NAME = "swebench/swesmith.arm64.vllm-project__vllm.39860a4e"


def load_instances():
    """Load instances from the validation file."""
    with open(INSTANCES_PATH) as f:
        return json.load(f)


def get_instance_by_suffix(instances, suffix):
    """Find instance by suffix (e.g., '41110')."""
    for inst in instances:
        if inst["instance_id"].endswith(f".{suffix}"):
            return inst
    return None


def save_patch_temp(patch_content, suffix, state):
    """Save patch to temp file."""
    temp_dir = Path(tempfile.gettempdir()) / "vllm_patches"
    temp_dir.mkdir(exist_ok=True)
    patch_file = temp_dir / f"{suffix}_{state}.patch"
    with open(patch_file, "w") as f:
        f.write(patch_content)
    return patch_file


def run_tests_in_container(instance, test_command, is_gold=True, timeout=300):
    """
    Run targeted tests in Docker container.

    Returns dict with test results.
    """
    instance_id = instance["instance_id"]
    suffix = instance_id.split(".")[-1]
    state = "gold" if is_gold else "buggy"

    print(f"\n{'='*60}")
    print(f"[{suffix}] Running tests in {state} state")
    print(f"Command: {test_command}")
    print(f"{'='*60}")

    # Determine which patch to apply
    if is_gold:
        # Gold state: apply patch normally (creates bug), then reverse it
        # Actually for gold state we DON'T want the bug
        # So we either don't apply the patch, or apply then reverse
        patch_content = instance.get("patch", "")
    else:
        # Buggy state: apply the patch to introduce the bug
        patch_content = instance.get("patch", "")

    if not patch_content:
        print(f"[{suffix}] Warning: No patch content found")
        return {"status": "error", "reason": "no_patch"}

    # Save patch to temp file
    patch_file = save_patch_temp(patch_content, suffix, state)

    # Create container
    container_name = f"vllm_targeted_{suffix}_{state}_{datetime.now().strftime('%H%M%S')}"

    try:
        # Create container
        create_cmd = [
            "docker", "create",
            "--name", container_name,
            "-v", f"{patch_file}:/tmp/patch.patch",
            IMAGE_NAME,
            "tail", "-f", "/dev/null"
        ]
        result = subprocess.run(create_cmd, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"[{suffix}] Failed to create container: {result.stderr}")
            return {"status": "error", "reason": "container_create_failed"}

        # Start container
        subprocess.run(["docker", "start", container_name], capture_output=True)

        # For gold state, we want NO bug patch applied (clean state)
        # For buggy state, we apply the patch
        if not is_gold:
            # Apply patch in buggy state
            apply_cmd = [
                "docker", "exec", container_name,
                "bash", "-c",
                f"cd /testbed && git apply --check /tmp/patch.patch 2>/dev/null && git apply /tmp/patch.patch || echo 'Patch may already be applied or failed'"
            ]
            result = subprocess.run(apply_cmd, capture_output=True, text=True)
            print(f"[{suffix}] Patch apply output: {result.stdout} {result.stderr}")

        # Run tests
        test_cmd_list = [
            "docker", "exec", container_name,
            "bash", "-c",
            f"cd /testbed && {test_command}"
        ]

        print(f"[{suffix}] Executing: {' '.join(test_cmd_list)}")

        try:
            result = subprocess.run(
                test_cmd_list,
                capture_output=True,
                text=True,
                timeout=timeout
            )

            stdout = result.stdout
            stderr = result.stderr
            exit_code = result.returncode

            # Parse test results
            passed_tests = []
            failed_tests = []
            error_tests = []

            for line in (stdout + stderr).split("\n"):
                if "::" in line and ("PASSED" in line or "FAILED" in line or "ERROR" in line):
                    parts = line.strip().split()
                    for part in parts:
                        if "::" in part:
                            if "PASSED" in line:
                                passed_tests.append(part)
                            elif "FAILED" in line:
                                failed_tests.append(part)
                            elif "ERROR" in line:
                                error_tests.append(part)

            return {
                "status": "completed",
                "exit_code": exit_code,
                "passed_count": len(passed_tests),
                "failed_count": len(failed_tests),
                "error_count": len(error_tests),
                "passed_tests": passed_tests,
                "failed_tests": failed_tests,
                "error_tests": error_tests,
                "stdout_preview": stdout[:2000] if stdout else "",
                "stderr_preview": stderr[:2000] if stderr else "",
            }

        except subprocess.TimeoutExpired:
            return {"status": "timeout", "reason": f"Tests exceeded {timeout}s timeout"}

    except Exception as e:
        return {"status": "error", "reason": str(e)}

    finally:
        # Cleanup
        subprocess.run(["docker", "stop", container_name], capture_output=True)
        subprocess.run(["docker", "rm", container_name], capture_output=True)


def main():
    print("="*70)
    print("Targeted Test Execution - PASS_TO_PASS Data Collection")
    print("="*70)

    # Load instances
    instances = load_instances()
    print(f"Loaded {len(instances)} total instances")

    # Load targeted configs
    configs_path = OUTPUT_DIR / "targeted_validation_configs.json"
    with open(configs_path) as f:
        configs = json.load(f)
    print(f"Loaded {len(configs)} targeted test configurations")

    # Results storage
    all_results = []

    # Process each config
    for config in configs:
        instance_id = config["instance_id"]
        suffix = instance_id.split(".")[-1]
        test_command = config["test_command"]

        instance = get_instance_by_suffix(instances, suffix)
        if not instance:
            print(f"[{suffix}] Instance not found!")
            continue

        print(f"\n{'#'*70}")
        print(f"# Processing {instance_id}")
        print(f"{'#'*70}")

        # Run in GOLD state (no bug patch)
        gold_results = run_tests_in_container(instance, test_command, is_gold=True, timeout=300)

        # Run in BUGGY state (with bug patch)
        buggy_results = run_tests_in_container(instance, test_command, is_gold=False, timeout=300)

        # Calculate PASS_TO_PASS: tests that passed in BOTH states
        p2p_tests = []
        if gold_results.get("status") == "completed" and buggy_results.get("status") == "completed":
            gold_passed = set(gold_results.get("passed_tests", []))
            buggy_passed = set(buggy_results.get("passed_tests", []))
            p2p_tests = list(gold_passed & buggy_passed)

        result_record = {
            "instance_id": instance_id,
            "suffix": suffix,
            "timestamp": datetime.now().isoformat(),
            "gold_results": gold_results,
            "buggy_results": buggy_results,
            "pass_to_pass_tests": p2p_tests,
            "pass_to_pass_count": len(p2p_tests),
        }

        all_results.append(result_record)

        # Save intermediate results
        results_file = OUTPUT_DIR / "targeted_test_results.json"
        with open(results_file, "w") as f:
            json.dump(all_results, f, indent=2)

        print(f"\n[{suffix}] Summary:")
        print(f"  Gold passed: {gold_results.get('passed_count', 0)}")
        print(f"  Buggy passed: {buggy_results.get('passed_count', 0)}")
        print(f"  PASS_TO_PASS tests: {len(p2p_tests)}")

    print(f"\n{'='*70}")
    print("All targeted tests completed!")
    print(f"Results saved to: {OUTPUT_DIR / 'targeted_test_results.json'}")
    print("="*70)

    # Print summary table
    print("\nSUMMARY:")
    print("-" * 70)
    print(f"{'Instance':<40} {'Gold':<8} {'Buggy':<8} {'P2P':<8} {'Status'}")
    print("-" * 70)
    for r in all_results:
        inst = r['instance_id'].split('.')[-1]
        gold = r['gold_results'].get('passed_count', '-')
        buggy = r['buggy_results'].get('passed_count', '-')
        p2p = r.get('pass_to_pass_count', '-')
        status = "OK" if r['gold_results'].get('status') == 'completed' and r['buggy_results'].get('status') == 'completed' else "ISSUE"
        print(f"{inst:<40} {str(gold):<8} {str(buggy):<8} {str(p2p):<8} {status}")


if __name__ == "__main__":
    main()
