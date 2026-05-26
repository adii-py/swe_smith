#!/usr/bin/env python3
"""
Manual validation script that runs locally without Docker.
Analyzes patches, designs test patches, and collects f2p/p2p results.
"""

import json
import os
import re
import subprocess
import tempfile
import shutil
from pathlib import Path
from unidiff import PatchSet
from dataclasses import dataclass
from typing import Optional

REPO_PATH = Path("/tmp/hyperswitch")
DATASET_PATH = Path("/Users/aditya.singh.001/Desktop/SWE-smith/logs/bug_gen/juspay__hyperswitch.fece9bc3/pr_mirror/recovered_dataset_with_test_patches.json")
OUTPUT_DIR = Path("/Users/aditya.singh.001/Desktop/SWE-smith/logs/manual_validation")

# Test command from profile
TEST_CMD = "CARGO_BUILD_JOBS=1 cargo test --lib --no-fail-fast -- --nocapture --skip redis --skip postgres --skip db --skip database --skip integration"
TIMEOUT = 3600  # 1 hour


@dataclass
class ValidationResult:
    instance_id: str
    status: str  # 'success', 'failed', 'timeout'
    pregold_tests: dict
    postgold_tests: dict
    f2p: list
    p2p: list
    p2f: list
    f2f: list
    error: Optional[str] = None


def get_first_modified_file(patch_text: str) -> Optional[tuple[str, str]]:
    """Get the first modified Rust file path and content."""
    try:
        ps = PatchSet(patch_text)
        for pf in ps:
            if pf.path.endswith(".rs"):
                full_path = REPO_PATH / pf.path
                if full_path.exists():
                    return pf.path, full_path.read_text()
    except Exception as e:
        print(f"  Error parsing patch: {e}")
    return None


def analyze_bug(patch_text: str, file_content: str) -> dict:
    """Analyze the patch to understand what bug it introduces."""
    analysis = {
        'changed_functions': [],
        'changed_types': [],
        'change_type': 'unknown',  # 'validation', 'calculation', 'error_handling', 'logic'
        'test_suggestion': ''
    }

    try:
        ps = PatchSet(patch_text)
        for pf in ps:
            for hunk in pf:
                for line in hunk:
                    if not (line.is_added or line.is_removed):
                        continue

                    text = line.value

                    # Detect function changes
                    m = re.search(r'fn\s+(\w+)', text)
                    if m and m.group(1) not in analysis['changed_functions']:
                        analysis['changed_functions'].append(m.group(1))

                    # Detect type changes
                    m = re.search(r'(?:struct|enum)\s+(\w+)', text)
                    if m and m.group(1) not in analysis['changed_types']:
                        analysis['changed_types'].append(m.group(1))

                    # Detect change type
                    if any(kw in text for kw in ['validate', 'check', 'verify', 'ensure']):
                        analysis['change_type'] = 'validation'
                    elif any(kw in text for kw in ['calculate', 'compute', 'sum', 'amount']):
                        analysis['change_type'] = 'calculation'
                    elif any(kw in text for kw in ['error', 'err', 'Result', 'unwrap', '?']):
                        analysis['change_type'] = 'error_handling'
                    elif analysis['change_type'] == 'unknown':
                        analysis['change_type'] = 'logic'

    except Exception as e:
        print(f"  Error analyzing patch: {e}")

    # Generate test suggestion based on analysis
    if analysis['changed_functions']:
        func = analysis['changed_functions'][0]
        if analysis['change_type'] == 'validation':
            analysis['test_suggestion'] = f"Test {func} with invalid input - should fail with bug"
        elif analysis['change_type'] == 'calculation':
            analysis['test_suggestion'] = f"Test {func} calculation result - should differ with bug"
        elif analysis['change_type'] == 'error_handling':
            analysis['test_suggestion'] = f"Test {func} error case - should panic/error with bug"
        else:
            analysis['test_suggestion'] = f"Test {func} behavior - should differ with bug"

    return analysis


def generate_targeted_test(instance: dict, bug_analysis: dict) -> Optional[str]:
    """Generate a targeted test patch based on bug analysis."""
    patch = instance.get("patch", "")
    title = instance.get("title", "")

    file_info = get_first_modified_file(patch)
    if not file_info:
        return None

    filepath, file_content = file_info
    funcs = bug_analysis.get('changed_functions', [])
    change_type = bug_analysis.get('change_type', 'unknown')

    if not funcs:
        return None

    target_fn = funcs[0]

    # Find the function signature to understand parameters
    fn_pattern = rf'(?:pub\s+)?(?:async\s+)?fn\s+{re.escape(target_fn)}\s*\(([^)]*)\)'
    fn_match = re.search(fn_pattern, file_content)

    # Generate test based on change type
    if change_type == 'validation':
        test_body = f'''    #[test]
    fn test_{target_fn}_validation() {{
        // Test that validation works correctly
        // This should pass without bug, fail with bug
        let result = {target_fn}(Default::default());
        assert!(result.is_ok(), "{target_fn} should validate successfully");
    }}'''
    elif change_type == 'calculation':
        test_body = f'''    #[test]
    fn test_{target_fn}_calculation() {{
        // Test calculation result
        // Bug may produce wrong calculation
        let result = {target_fn}(Default::default());
        // Assert expected calculation result
        assert_ne!(result, Default::default());
    }}'''
    elif change_type == 'error_handling':
        test_body = f'''    #[test]
    fn test_{target_fn}_error_handling() {{
        // Test error handling
        // Bug may cause panic or wrong error
        let result = std::panic::catch_unwind(|| {{
            {target_fn}(Default::default())
        }});
        assert!(result.is_ok(), "{target_fn} should not panic");
    }}'''
    else:
        test_body = f'''    #[test]
    fn test_{target_fn}_behavior() {{
        // Test general behavior
        let result = {target_fn}(Default::default());
        // Basic smoke test - function should complete
        drop(result);
    }}'''

    test_code = f'''#[cfg(test)]
mod tests {{
    use super::*;

{test_body}
}}'''

    # Build diff
    lines_count = file_content.count('\n') + 1
    test_lines = test_code.count('\n') + 1

    diff = f'''diff --git a/{filepath} b/{filepath}
index 0000000..1111111 100644
--- a/{filepath}
+++ b/{filepath}
@@ -{lines_count},0 +{lines_count},{test_lines} @@
''' + '\n'.join('+' + line for line in test_code.split('\n')) + '\n'

    return diff


def run_tests_in_repo(repo_path: Path, test_cmd: str) -> tuple[bool, str]:
    """Run tests in the repo and return (success, output)."""
    try:
        result = subprocess.run(
            test_cmd,
            shell=True,
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=TIMEOUT
        )
        output = result.stdout + result.stderr
        success = result.returncode == 0 or "test result:" in output
        return success, output
    except subprocess.TimeoutExpired:
        return False, "TIMEOUT"
    except Exception as e:
        return False, str(e)


def parse_test_output(output: str) -> dict:
    """Parse cargo test output to extract test results."""
    tests = {}

    for line in output.split('\n'):
        line = line.strip()

        # Match: test test_name ... ok
        # Match: test test_name ... FAILED
        match = re.match(r'test\s+(\S+)\s+\.\.\.\s+(ok|FAILED|ignored|running)', line)
        if match:
            test_name = match.group(1)
            status = match.group(2)

            if status == 'ok':
                tests[test_name] = 'PASSED'
            elif status == 'FAILED':
                tests[test_name] = 'FAILED'
            elif status == 'ignored':
                tests[test_name] = 'IGNORED'

    return tests


def compare_results(pregold: dict, postgold: dict) -> tuple[list, list, list, list]:
    """Compare pre and post gold results to get f2p, p2p, p2f, f2f."""
    f2p, p2p, p2f, f2f = [], [], [], []

    all_tests = set(pregold.keys()) | set(postgold.keys())

    for test in all_tests:
        pre = pregold.get(test, 'UNKNOWN')
        post = postgold.get(test, 'UNKNOWN')

        if pre == 'FAILED' and post == 'PASSED':
            f2p.append(test)
        elif pre == 'PASSED' and post == 'PASSED':
            p2p.append(test)
        elif pre == 'PASSED' and post == 'FAILED':
            p2f.append(test)
        elif pre == 'FAILED' and post == 'FAILED':
            f2f.append(test)

    return f2p, p2p, p2f, f2f


def validate_instance(instance: dict) -> ValidationResult:
    """Validate a single instance manually."""
    iid = instance['instance_id']
    bug_patch = instance.get('patch', '')
    test_patch = instance.get('test_patch', '')

    print(f"\n{'='*80}")
    print(f"Validating: {iid}")
    print(f"Title: {instance.get('title', '')}")
    print(f"{'='*80}")

    # Step 1: Analyze the bug
    print("\n[1/5] Analyzing bug patch...")
    file_info = get_first_modified_file(bug_patch)
    if not file_info:
        return ValidationResult(
            instance_id=iid,
            status='failed',
            pregold_tests={}, postgold_tests={},
            f2p=[], p2p=[], p2f=[], f2f=[],
            error="Could not read modified file"
        )

    filepath, file_content = file_info
    bug_analysis = analyze_bug(bug_patch, file_content)

    print(f"  Changed functions: {bug_analysis['changed_functions']}")
    print(f"  Change type: {bug_analysis['change_type']}")
    print(f"  Test suggestion: {bug_analysis['test_suggestion']}")

    # Step 2: Generate or use existing test patch
    print("\n[2/5] Preparing test patch...")
    if not test_patch:
        print("  Generating targeted test patch...")
        test_patch = generate_targeted_test(instance, bug_analysis)
        if test_patch:
            print(f"  Generated test patch ({len(test_patch)} chars)")
        else:
            print("  Failed to generate test patch")
    else:
        print(f"  Using existing test patch ({len(test_patch)} chars)")

    # Step 3: Pre-gold test run (without bug patch)
    print("\n[3/5] Running pre-gold tests (baseline)...")

    # Apply test patch if available
    if test_patch:
        # Apply test patch
        proc = subprocess.run(
            ['git', 'apply', '--allow-empty', '-'],
            cwd=REPO_PATH,
            input=test_patch,
            capture_output=True,
            text=True
        )
        if proc.returncode != 0:
            print(f"  Warning: Could not apply test patch: {proc.stderr}")

    # Run tests
    success, output = run_tests_in_repo(REPO_PATH, TEST_CMD)
    pregold_tests = parse_test_output(output)

    print(f"  Tests found: {len(pregold_tests)}")
    print(f"  Passed: {sum(1 for v in pregold_tests.values() if v == 'PASSED')}")
    print(f"  Failed: {sum(1 for v in pregold_tests.values() if v == 'FAILED')}")

    # Reset repo
    subprocess.run(['git', 'checkout', '--', '.'], cwd=REPO_PATH, capture_output=True)
    subprocess.run(['git', 'clean', '-fd'], cwd=REPO_PATH, capture_output=True)

    # Step 4: Post-gold test run (with bug patch)
    print("\n[4/5] Running post-gold tests (with bug patch)...")

    # Apply bug patch
    proc = subprocess.run(
        ['git', 'apply', '--allow-empty', '-'],
        cwd=REPO_PATH,
        input=bug_patch,
        capture_output=True,
        text=True
    )
    if proc.returncode != 0:
        print(f"  ERROR: Could not apply bug patch: {proc.stderr}")
        return ValidationResult(
            instance_id=iid,
            status='failed',
            pregold_tests=pregold_tests, postgold_tests={},
            f2p=[], p2p=[], p2f=[], f2f=[],
            error=f"Could not apply bug patch: {proc.stderr}"
        )

    # Apply test patch if available
    if test_patch:
        proc = subprocess.run(
            ['git', 'apply', '--allow-empty', '-'],
            cwd=REPO_PATH,
            input=test_patch,
            capture_output=True,
            text=True
        )
        if proc.returncode != 0:
            print(f"  Warning: Could not apply test patch after bug: {proc.stderr}")

    # Run tests
    success, output = run_tests_in_repo(REPO_PATH, TEST_CMD)
    postgold_tests = parse_test_output(output)

    print(f"  Tests found: {len(postgold_tests)}")
    print(f"  Passed: {sum(1 for v in postgold_tests.values() if v == 'PASSED')}")
    print(f"  Failed: {sum(1 for v in postgold_tests.values() if v == 'FAILED')}")

    # Reset repo
    subprocess.run(['git', 'checkout', '--', '.'], cwd=REPO_PATH, capture_output=True)
    subprocess.run(['git', 'clean', '-fd'], cwd=REPO_PATH, capture_output=True)

    # Step 5: Compare results
    print("\n[5/5] Comparing results...")
    f2p, p2p, p2f, f2f = compare_results(pregold_tests, postgold_tests)

    print(f"  FAIL_TO_PASS (f2p): {len(f2p)} {f2p[:3] if f2p else ''}")
    print(f"  PASS_TO_PASS (p2p): {len(p2p)} {p2p[:3] if p2p else ''}")
    print(f"  PASS_TO_FAIL (p2f): {len(p2f)} {p2f[:3] if p2f else ''}")
    print(f"  FAIL_TO_FAIL (f2f): {len(f2f)} {f2f[:3] if f2f else ''}")

    status = 'success' if f2p else ('partial' if p2p else 'failed')

    return ValidationResult(
        instance_id=iid,
        status=status,
        pregold_tests=pregold_tests,
        postgold_tests=postgold_tests,
        f2p=f2p,
        p2p=p2p,
        p2f=p2f,
        f2f=f2f
    )


def main():
    # Load dataset
    print(f"Loading dataset from {DATASET_PATH}")
    with open(DATASET_PATH) as f:
        data = json.load(f)

    print(f"Loaded {len(data)} instances")

    # Filter to instances with test patches or select pilot
    pilot_instances = [inst for inst in data if inst.get('test_patch')][:2]

    if len(pilot_instances) < 2:
        print("Not enough instances with test patches, selecting first 2 from dataset")
        pilot_instances = data[:2]

    print(f"\nRunning pilot validation on {len(pilot_instances)} instances...")

    # Create output directory
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    results = []
    for inst in pilot_instances:
        result = validate_instance(inst)
        results.append(result)

        # Save individual result
        result_file = OUTPUT_DIR / f"{result.instance_id}.json"
        with open(result_file, 'w') as f:
            json.dump({
                'instance_id': result.instance_id,
                'status': result.status,
                'f2p': result.f2p,
                'p2p': result.p2p,
                'p2f': result.p2f,
                'f2f': result.f2f,
                'error': result.error
            }, f, indent=2)

    # Print summary
    print("\n" + "="*80)
    print("VALIDATION SUMMARY")
    print("="*80)

    for r in results:
        print(f"\n{r.instance_id}: {r.status}")
        print(f"  f2p: {len(r.f2p)} tests")
        print(f"  p2p: {len(r.p2p)} tests")
        if r.error:
            print(f"  ERROR: {r.error}")

    total_f2p = sum(len(r.f2p) for r in results)
    total_p2p = sum(len(r.p2p) for r in results)

    print(f"\nTotal f2p: {total_f2p}")
    print(f"Total p2p: {total_p2p}")

    if total_f2p > 0:
        print("\n✓ SUCCESS: Found fail-to-pass cases! Validation is working.")
        print(f"  Output saved to: {OUTPUT_DIR}")
    else:
        print("\n✗ No f2p found. Consider:")
        print("  - Improving test patches")
        print("  - Increasing timeout")
        print("  - Using more targeted tests")


if __name__ == "__main__":
    main()
