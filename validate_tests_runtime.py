#!/usr/bin/env python3
"""Validate generated tests by running them against buggy code."""

import json
import subprocess
import sys
from pathlib import Path

VLLM_REPO = '/Users/aditya.singh.001/Desktop/SWE-smith/tmp_d6b73da0/vllm-project__vllm.3e1ad443'


def run_test(inst):
    """Run test against buggy code and check if it fails."""
    instance_id = inst['instance_id']
    bug_patch = inst.get('bug_patch', '')
    test_patch = inst.get('test_patch', '')
    fail_to_pass = inst.get('FAIL_TO_PASS', [])

    # Reset repo
    subprocess.run(['git', 'reset', '--hard'], cwd=VLLM_REPO,
                   capture_output=True, check=False)
    subprocess.run(['git', 'clean', '-fd'], cwd=VLLM_REPO,
                   capture_output=True, check=False)

    # Apply bug patch
    result = subprocess.run(
        ['git', 'apply', '-'],
        cwd=VLLM_REPO,
        input=bug_patch,
        capture_output=True,
        text=True
    )
    if result.returncode != 0:
        return {'status': 'bug_apply_failed', 'error': result.stderr}

    # Apply test patch
    result = subprocess.run(
        ['git', 'apply', '-'],
        cwd=VLLM_REPO,
        input=test_patch,
        capture_output=True,
        text=True
    )
    if result.returncode != 0:
        return {'status': 'test_apply_failed', 'error': result.stderr}

    # Find test file
    test_files = [l.split(' b/')[1] for l in test_patch.split('\n')
                  if 'diff --git' in l and ' b/' in l]
    if not test_files:
        return {'status': 'no_test_file'}

    test_file = test_files[0]
    test_path = Path(VLLM_REPO) / test_file

    if not test_path.exists():
        return {'status': 'test_file_missing'}

    # Try to import the test module to catch import errors
    test_module = test_file.replace('/', '.').replace('.py', '')
    result = subprocess.run(
        [sys.executable, '-c', f'import {test_module}'],
        cwd=VLLM_REPO,
        capture_output=True,
        text=True,
        env={**subprocess.os.environ, 'PYTHONPATH': VLLM_REPO}
    )

    if result.returncode != 0:
        return {
            'status': 'import_error',
            'error': result.stderr[:200]
        }

    # Run pytest on the test
    result = subprocess.run(
        ['python3', '-m', 'pytest', str(test_path), '-v', '--tb=short'],
        cwd=VLLM_REPO,
        capture_output=True,
        text=True,
        timeout=60
    )

    # Check if tests failed (they should fail on buggy code)
    if result.returncode != 0:
        return {
            'status': 'test_failed_as_expected',  # This is F2P!
            'output': result.stdout[:500],
            'test_count': len(fail_to_pass)
        }
    else:
        return {
            'status': 'test_passed',  # Unexpected - test passed on buggy code
            'output': result.stdout[:500]
        }


def main():
    results = []

    with open('vllm_3e1ad443_with_targeted_tests.json') as f:
        instances = json.load(f)

    print('='*80)
    print('RUNTIME VALIDATION - Testing against buggy code')
    print('='*80)
    print(f'Testing {min(10, len(instances))} instances...')
    print()

    for inst in instances[:10]:
        instance_id = inst['instance_id']
        print(f'Testing {instance_id}...')

        try:
            result = run_test(inst)
            results.append({
                'instance_id': instance_id,
                **result
            })

            if result['status'] == 'test_failed_as_expected':
                print('  ✅ Tests FAILED on buggy code (F2P working!)')
            elif result['status'] == 'test_passed':
                print('  ⚠️  Tests PASSED on buggy code (may not detect bug)')
            elif result['status'] == 'import_error':
                err = result.get('error', '')[:80]
                print(f'  ❌ Import error: {err}')
            else:
                status = result['status']
                print(f'  ❌ {status}')

        except subprocess.TimeoutExpired:
            print(f'  ⏱️  Timeout')
            results.append({'instance_id': instance_id, 'status': 'timeout'})
        except Exception as e:
            print(f'  ❌ Error: {e}')
            results.append({'instance_id': instance_id, 'status': f'error: {e}'})

    print()
    print('='*80)
    print('VALIDATION SUMMARY')
    print('='*80)

    f2p_working = sum(1 for r in results if r['status'] == 'test_failed_as_expected')
    import_errors = sum(1 for r in results if r['status'] == 'import_error')
    other_failures = sum(1 for r in results if r['status'] not in
                         ['test_failed_as_expected', 'test_passed', 'import_error'])

    print(f'F2P working (tests fail on buggy): {f2p_working}/{len(results)}')
    print(f'Import errors: {import_errors}/{len(results)}')
    print(f'Other failures: {other_failures}/{len(results)}')
    print()

    # Save results
    with open('validation_results.json', 'w') as f:
        json.dump(results, f, indent=2)
    print('Results saved to validation_results.json')


if __name__ == '__main__':
    main()
