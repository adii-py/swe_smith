#!/usr/bin/env python3
"""
Manual validation with INVERTED patches.
Since repo has fixed code, we invert patches to REMOVE fixes (introducing bugs).
"""

import json
import os
import re
import subprocess
from pathlib import Path
from dataclasses import dataclass
from typing import Optional

REPO_PATH = Path("/tmp/hyperswitch")
DATASET_PATH = Path("/Users/aditya.singh.001/Desktop/SWE-smith/logs/bug_gen/juspay__hyperswitch.fece9bc3/pr_mirror/recovered_dataset_with_test_patches.json")
OUTPUT_DIR = Path("/Users/aditya.singh.001/Desktop/SWE-smith/logs/manual_validation_inverted")

TEST_CMD = "cargo test --lib -p analytics --no-fail-fast -- test_ 2>&1"
TIMEOUT = 600  # 10 minutes for quicker iteration


@dataclass
class ValidationResult:
    instance_id: str
    status: str
    pregold_tests: dict
    postgold_tests: dict
    f2p: list
    p2p: list
    error: Optional[str] = None


def invert_patch(patch_text: str) -> str:
    """Properly invert a patch including hunk headers and +/- lines."""
    lines = patch_text.split('\n')
    inverted = []

    for line in lines:
        # Hunk header: @@ -old_start,old_count +new_start,new_count @@
        if line.startswith('@@'):
            # Swap the - and + counts in hunk headers
            match = re.match(r'@@ -(\d+),(\d+) \+(\d+),(\d+) @@', line)
            if match:
                old_start, old_count, new_start, new_count = match.groups()
                # Swap the counts: old_count <-> new_count
                new_header = f"@@ -{old_start},{new_count} +{new_start},{old_count} @@"
                inverted.append(new_header)
            else:
                inverted.append(line)
        elif line.startswith('--- a/'):
            inverted.append(line.replace('--- a/', '+++ b/'))
        elif line.startswith('+++ b/'):
            inverted.append(line.replace('+++ b/', '--- a/'))
        elif line.startswith('+') and not line.startswith('+++'):
            inverted.append('-' + line[1:])
        elif line.startswith('-') and not line.startswith('---'):
            inverted.append('+' + line[1:])
        else:
            inverted.append(line)

    return '\n'.join(inverted)


def apply_patch_directly(patch_text: str, repo_path: Path) -> tuple[bool, str]:
    """Apply patch using git apply with better error handling."""
    try:
        proc = subprocess.run(
            ['git', 'apply', '--verbose', '-'],
            cwd=repo_path,
            input=patch_text,
            capture_output=True,
            text=True,
            timeout=30
        )
        if proc.returncode == 0:
            return True, "Applied successfully"
        else:
            return False, f"git apply failed: {proc.stderr}"
    except Exception as e:
        return False, str(e)


def run_tests_simple(repo_path: Path) -> tuple[bool, dict]:
    """Run tests and parse results."""
    try:
        proc = subprocess.run(
            TEST_CMD,
            shell=True,
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=TIMEOUT
        )
        output = proc.stdout + proc.stderr

        # Parse test results
        tests = {}
        for line in output.split('\n'):
            # Match: test test_name ... ok/FAILED
            match = re.search(r'test\s+(\S+)\s+\.\.\.\s+(ok|FAILED)', line)
            if match:
                test_name = match.group(1)
                status = match.group(2)
                tests[test_name] = 'PASSED' if status == 'ok' else 'FAILED'

        return True, tests
    except subprocess.TimeoutExpired:
        return False, {"TIMEOUT": "Tests timed out"}
    except Exception as e:
        return False, {"ERROR": str(e)}


def reset_repo(repo_path: Path):
    """Reset repo to clean state."""
    subprocess.run(['git', 'checkout', '--', '.'], cwd=repo_path, capture_output=True)
    subprocess.run(['git', 'clean', '-fd'], cwd=repo_path, capture_output=True)


def validate_instance(instance: dict) -> ValidationResult:
    """Validate a single instance with inverted patch."""
    iid = instance['instance_id']
    bug_patch = instance.get('patch', '')  # This is actually the FIX patch
    test_patch = instance.get('test_patch', '')

    print(f"\n{'='*80}")
    print(f"Validating: {iid}")
    print(f"Title: {instance.get('title', '')[:60]}")
    print(f"{'='*80}")

    # Step 1: Invert the patch (fix -> bug introduction)
    print("\n[1/4] Inverting patch...")
    inverted_patch = invert_patch(bug_patch)
    print(f"  Original patch: {len(bug_patch)} chars")
    print(f"  Inverted patch: {len(inverted_patch)} chars")

    # Step 2: Apply test patch (adds tests to detect bug)
    print("\n[2/4] Adding test patch...")
    if test_patch:
        success, msg = apply_patch_directly(test_patch, REPO_PATH)
        if success:
            print(f"  Test patch applied: {msg}")
        else:
            print(f"  Test patch failed: {msg}")

    # Step 3: Run pre-gold tests (fixed state, should PASS)
    print("\n[3/4] Running pre-gold tests (fixed state)...")
    success, pregold_tests = run_tests_simple(REPO_PATH)
    if isinstance(pregold_tests, dict) and not pregold_tests.get('ERROR') and not pregold_tests.get('TIMEOUT'):
        print(f"  Tests found: {len(pregold_tests)}")
        passed = sum(1 for v in pregold_tests.values() if v == 'PASSED')
        print(f"  Passed: {passed}")
    else:
        print(f"  Test run issue: {pregold_tests}")
        pregold_tests = {}

    # Step 4: Apply inverted patch (introduce bug)
    print("\n[4/4] Applying inverted patch (introducing bug)...")
    success, msg = apply_patch_directly(inverted_patch, REPO_PATH)
    if not success:
        print(f"  Failed to apply inverted patch: {msg}")
        reset_repo(REPO_PATH)
        return ValidationResult(
            instance_id=iid, status='failed',
            pregold_tests=pregold_tests, postgold_tests={},
            f2p=[], p2p=[], error=f"Patch apply failed: {msg}"
        )
    print(f"  Inverted patch applied")

    # Step 5: Run post-gold tests (buggy state, should FAIL)
    print("\n[5/5] Running post-gold tests (buggy state)...")
    success, postgold_tests = run_tests_simple(REPO_PATH)
    if isinstance(postgold_tests, dict) and not postgold_tests.get('ERROR') and not postgold_tests.get('TIMEOUT'):
        print(f"  Tests found: {len(postgold_tests)}")
        passed = sum(1 for v in postgold_tests.values() if v == 'PASSED')
        failed = sum(1 for v in postgold_tests.values() if v == 'FAILED')
        print(f"  Passed: {passed}, Failed: {failed}")
    else:
        print(f"  Test run issue: {postgold_tests}")
        postgold_tests = {}

    # Step 6: Compare results
    print("\n[6/6] Comparing results...")
    f2p = []
    p2p = []

    all_tests = set(pregold_tests.keys()) | set(postgold_tests.keys())
    for test in all_tests:
        pre = pregold_tests.get(test, 'UNKNOWN')
        post = postgold_tests.get(test, 'UNKNOWN')

        if pre == 'PASSED' and post == 'FAILED':
            f2p.append(test)
        elif pre == 'PASSED' and post == 'PASSED':
            p2p.append(test)

    print(f"  FAIL_TO_PASS (f2p): {len(f2p)} {f2p}")
    print(f"  PASS_TO_PASS (p2p): {len(p2p)} {p2p}")

    # Reset repo
    reset_repo(REPO_PATH)

    status = 'success' if f2p else ('partial' if p2p else 'failed')

    return ValidationResult(
        instance_id=iid,
        status=status,
        pregold_tests=pregold_tests,
        postgold_tests=postgold_tests,
        f2p=f2p,
        p2p=p2p
    )


def main():
    # Load dataset
    print(f"Loading dataset from {DATASET_PATH}")
    with open(DATASET_PATH) as f:
        data = json.load(f)

    print(f"Loaded {len(data)} instances")

    # Select pilot instances (analytics crate only for now - smaller/faster)
    pilot_instances = [
        inst for inst in data
        if inst.get('test_patch') and 'analytics' in str(inst.get('patch', ''))
    ][:2]

    print(f"\nRunning pilot validation on {len(pilot_instances)} analytics instances...")
    print("Using INVERTED patches to introduce bugs")

    # Create output directory
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    results = []
    for inst in pilot_instances:
        result = validate_instance(inst)
        results.append(result)

        # Save result
        result_file = OUTPUT_DIR / f"{result.instance_id}.json"
        with open(result_file, 'w') as f:
            json.dump({
                'instance_id': result.instance_id,
                'status': result.status,
                'f2p': result.f2p,
                'p2p': result.p2p,
                'error': result.error
            }, f, indent=2)

    # Print summary
    print("\n" + "="*80)
    print("VALIDATION SUMMARY")
    print("="*80)

    total_f2p = 0
    total_p2p = 0

    for r in results:
        print(f"\n{r.instance_id}: {r.status}")
        print(f"  f2p: {len(r.f2p)} - {r.f2p}")
        print(f"  p2p: {len(r.p2p)} - {r.p2p}")
        total_f2p += len(r.f2p)
        total_p2p += len(r.p2p)

    print(f"\n{'='*80}")
    print(f"Total f2p: {total_f2p}")
    print(f"Total p2p: {total_p2p}")

    if total_f2p > 0:
        print("\n✅ SUCCESS: Found fail-to-pass cases!")
    else:
        print("\n❌ No f2p found - tests may need adjustment")

    print(f"\nResults saved to: {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
