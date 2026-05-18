#!/usr/bin/env python3
"""Validate generated test patches against buggy code."""

import json
import subprocess
from pathlib import Path

VLLM_REPO = '/Users/aditya.singh.001/Desktop/SWE-smith/tmp_d6b73da0/vllm-project__vllm.3e1ad443'


def main():
    results = []

    # Load dataset
    with open('vllm_3e1ad443_with_targeted_tests.json') as f:
        instances = json.load(f)

    print('='*80)
    print('VALIDATING GENERATED TESTS')
    print('='*80)
    print(f'Total instances: {len(instances)}')
    print()

    # Test first 10 instances
    for inst in instances[:10]:
        instance_id = inst['instance_id']
        bug_patch = inst.get('bug_patch', '')
        test_patch = inst.get('test_patch', '')

        print(f'Validating {instance_id}...')

        # Reset repo
        subprocess.run(['git', 'reset', '--hard'], cwd=VLLM_REPO,
                       capture_output=True, check=False)
        subprocess.run(['git', 'clean', '-fd'], cwd=VLLM_REPO,
                       capture_output=True, check=False)

        # Apply bug patch
        bug_result = subprocess.run(
            ['git', 'apply', '-'],
            cwd=VLLM_REPO,
            input=bug_patch,
            capture_output=True,
            text=True
        )

        if bug_result.returncode != 0:
            print(f'  ❌ Failed to apply bug patch: {bug_result.stderr[:100]}')
            results.append({'instance_id': instance_id, 'status': 'bug_apply_failed'})
            continue

        # Apply test patch
        test_result = subprocess.run(
            ['git', 'apply', '-'],
            cwd=VLLM_REPO,
            input=test_patch,
            capture_output=True,
            text=True
        )

        if test_result.returncode != 0:
            print(f'  ❌ Failed to apply test patch: {test_result.stderr[:100]}')
            results.append({'instance_id': instance_id, 'status': 'test_apply_failed'})
            continue

        # Check test file exists and has valid syntax
        test_files = [l.split(' b/')[1] for l in test_patch.split('\n')
                      if 'diff --git' in l and ' b/' in l]
        if test_files:
            test_file = test_files[0]
            test_path = Path(VLLM_REPO) / test_file
            if test_path.exists():
                # Check Python syntax
                syntax_result = subprocess.run(
                    ['python3', '-m', 'py_compile', str(test_path)],
                    capture_output=True
                )
                if syntax_result.returncode == 0:
                    print(f'  ✅ Test file valid: {test_file}')
                    results.append({
                        'instance_id': instance_id,
                        'status': 'success',
                        'test_file': str(test_file)
                    })
                else:
                    print(f'  ❌ Syntax error in test file')
                    results.append({
                        'instance_id': instance_id,
                        'status': 'syntax_error'
                    })
            else:
                print(f'  ❌ Test file not found after apply')
                results.append({
                    'instance_id': instance_id,
                    'status': 'file_not_found'
                })

    print()
    print('='*80)
    print('VALIDATION SUMMARY')
    print('='*80)
    success = sum(1 for r in results if r['status'] == 'success')
    print(f'Successful: {success}/{len(results)}')
    for r in results:
        print(f"  {r['instance_id']}: {r['status']}")


if __name__ == '__main__':
    main()
