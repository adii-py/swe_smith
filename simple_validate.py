#!/usr/bin/env python3
"""
Simple validation script for vLLM bugs.
Applies bug patch and runs tests to check if they fail.
"""

import json
import subprocess
import tempfile
import os
from pathlib import Path

DATASET_FILE = "vllm_lm_unified_bugs_swebench_ready.json"
VLLM_REPO = "./tmp_d6b73da0/vllm-project__vllm.3e1ad443"

def run_command(cmd, cwd=None, capture=True, timeout=120):
    """Run a shell command."""
    try:
        result = subprocess.run(
            cmd,
            shell=True,
            cwd=cwd or VLLM_REPO,
            capture_output=capture,
            text=True,
            timeout=timeout
        )
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return -1, "", "Timeout"
    except Exception as e:
        return -1, "", str(e)

def validate_instance(instance, idx):
    """Validate a single instance."""
    instance_id = instance["instance_id"]
    func_name = instance["tags"]["function_name"]

    print(f"\n{'='*70}")
    print(f"[{idx}] Validating: {func_name}")
    print(f"    Instance: {instance_id}")
    print('='*70)

    # Get bug patch
    bug_patch = instance.get("bug_patch") or instance.get("patch", "")
    if not bug_patch:
        print("❌ No bug patch found")
        return {"status": "error", "reason": "no_bug_patch"}

    f2p_tests = instance.get("FAIL_TO_PASS", [])
    p2p_tests = instance.get("PASS_TO_PASS", [])

    print(f"   F2P tests: {len(f2p_tests)}")
    print(f"   P2P tests: {len(p2p_tests)}")

    # Reset repo
    print("\n📋 Resetting repository...")
    run_command("git checkout -- . && git clean -fd", timeout=30)

    # Apply bug patch
    print("📋 Applying bug patch...")
    with tempfile.NamedTemporaryFile(mode='w', suffix='.patch', delete=False) as f:
        f.write(bug_patch)
        patch_file = f.name

    try:
        code, _, stderr = run_command(f"git apply {patch_file}", timeout=30)
        if code != 0:
            print(f"❌ Failed to apply bug patch: {stderr[:200]}")
            return {"status": "error", "reason": "patch_apply_failed"}
        print("✅ Bug patch applied")
    finally:
        os.unlink(patch_file)

    # Run F2P tests
    f2p_results = []
    f2p_passed = 0
    f2p_failed = 0

    print(f"\n📋 Running {len(f2p_tests)} F2P tests (should FAIL with bug)...")
    for test in f2p_tests[:3]:  # Limit to first 3 for speed
        test_path = test.split('::')[0]
        code, stdout, stderr = run_command(f"python -m pytest {test} -x --tb=no -q 2>&1", timeout=60)
        output = stdout + stderr

        if 'passed' in output.lower() and 'failed' not in output.lower():
            f2p_passed += 1
            f2p_results.append({"test": test, "result": "PASSED"})
            print(f"   ⚠️  {test} PASSED (unexpected - test should fail with bug)")
        elif 'failed' in output.lower():
            f2p_failed += 1
            f2p_results.append({"test": test, "result": "FAILED"})
            print(f"   ✅ {test} FAILED (expected)")
        elif 'error' in output.lower():
            f2p_results.append({"test": test, "result": "ERROR"})
            print(f"   ⚠️  {test} ERROR")
        else:
            f2p_results.append({"test": test, "result": "UNKNOWN"})
            print(f"   ❓ {test} UNKNOWN")

    # Reset repo
    run_command("git checkout -- . && git clean -fd", timeout=30)

    # Determine status
    if f2p_failed > 0:
        status = "valid"
        print(f"\n✅ VALID: {f2p_failed}/{len(f2p_tests[:3])} F2P tests failed as expected")
    elif f2p_passed == len(f2p_tests[:3]):
        status = "0_f2p"
        print(f"\n❌ 0_F2P: All F2P tests passed (bug not detected)")
    else:
        status = "unclear"
        print(f"\n⚠️  UNCLEAR: Could not determine F2P status")

    return {
        "status": status,
        "instance_id": instance_id,
        "f2p_failed": f2p_failed,
        "f2p_passed": f2p_passed,
        "f2p_total": len(f2p_tests[:3]),
        "results": f2p_results
    }

def main():
    print("="*70)
    print("SIMPLE VALIDATION FOR VLLM BUGS")
    print("="*70)

    if not os.path.exists(DATASET_FILE):
        print(f"❌ Dataset not found: {DATASET_FILE}")
        return

    if not os.path.exists(VLLM_REPO):
        print(f"❌ Repo not found: {VLLM_REPO}")
        return

    with open(DATASET_FILE) as f:
        instances = json.load(f)

    print(f"\nFound {len(instances)} instances to validate")
    print(f"Repository: {VLLM_REPO}")

    results = []
    valid_count = 0
    f2p_zero_count = 0
    error_count = 0

    for i, instance in enumerate(instances, 1):
        result = validate_instance(instance, i)
        results.append(result)

        if result["status"] == "valid":
            valid_count += 1
        elif result["status"] == "0_f2p":
            f2p_zero_count += 1
        else:
            error_count += 1

    # Summary
    print("\n" + "="*70)
    print("VALIDATION SUMMARY")
    print("="*70)
    print(f"\nTotal instances: {len(instances)}")
    print(f"  ✅ Valid (F2P > 0): {valid_count}")
    print(f"  ❌ 0 F2P: {f2p_zero_count}")
    print(f"  ⚠️  Errors: {error_count}")

    # Save results
    with open("validation_results.json", 'w') as f:
        json.dump(results, f, indent=2)
    print(f"\n💾 Results saved to: validation_results.json")

if __name__ == "__main__":
    main()
