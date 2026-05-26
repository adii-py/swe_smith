#!/usr/bin/env python3
"""
Validate the correct bug instances.
"""

import json
import subprocess
import tempfile
from pathlib import Path

REPO_PATH = Path('/Users/aditya.singh.001/Desktop/SWE-smith/juspay__hyperswitch.fece9bc3')

def run_cmd(cmd, cwd=None, input_text=None, timeout=600):
    """Run a command."""
    try:
        result = subprocess.run(
            cmd,
            cwd=cwd or REPO_PATH,
            input=input_text,
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=isinstance(cmd, str)
        )
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return -1, "", "Timeout"

def validate_instance(instance):
    """Validate a single instance."""
    instance_id = instance['instance_id']
    bug_patch = instance['patch']
    test_patch = instance['test_patch']
    test_cmd = instance['test_cmd']
    fail_to_pass = instance.get('FAIL_TO_PASS', [])

    print(f"\n{'='*60}")
    print(f"Validating: {instance_id}")
    print(f"{'='*60}")

    result = {
        'instance_id': instance_id,
        'status': 'unknown',
        'f2p': 0,
        'p2p': 0,
        'f2f': 0,
        'p2f': 0,
        'errors': []
    }

    # Create temp directory
    with tempfile.TemporaryDirectory() as tmpdir:
        repo_tmp = Path(tmpdir) / 'repo'

        # Clone repo
        print(f"Cloning repo...")
        code, _, err = run_cmd(['git', 'clone', '--quiet', str(REPO_PATH), str(repo_tmp)], cwd=tmpdir)
        if code != 0:
            result['status'] = 'clone_failed'
            result['errors'].append(err)
            return result

        # Checkout base commit
        base_commit = instance['base_commit']
        print(f"Checking out {base_commit[:12]}...")
        code, _, err = run_cmd(['git', 'checkout', '--quiet', base_commit], cwd=repo_tmp)
        if code != 0:
            result['status'] = 'checkout_failed'
            result['errors'].append(err)
            return result

        # Apply bug patch
        print(f"Applying bug patch...")
        code, _, err = run_cmd(['git', 'apply', '-'], cwd=repo_tmp, input_text=bug_patch)
        if code != 0:
            print(f"  ❌ Bug patch failed: {err[:300]}")
            result['status'] = 'bug_patch_failed'
            result['errors'].append(f"Bug patch: {err}")
            return result
        print(f"  ✓ Bug patch applied")

        # Apply test patch
        print(f"Applying test patch...")
        code, _, err = run_cmd(['git', 'apply', '-'], cwd=repo_tmp, input_text=test_patch)
        if code != 0:
            print(f"  ❌ Test patch failed: {err[:300]}")
            result['status'] = 'test_patch_failed'
            result['errors'].append(f"Test patch: {err}")
            return result
        print(f"  ✓ Test patch applied")

        # Run tests
        print(f"Running tests...")
        print(f"  Command: {test_cmd}")
        code, stdout, stderr = run_cmd(test_cmd.split(), cwd=repo_tmp, timeout=900)

        output = stdout + stderr

        # Check for test results
        f2p_count = 0
        p2f_count = 0

        for test_name in fail_to_pass:
            test_short = test_name.split('::')[-1]
            # Check if test failed
            if 'FAILED' in output or 'failures:' in output:
                if test_short in output:
                    f2p_count += 1
                    print(f"  ✓ {test_short} FAILED (f2p)")
                else:
                    p2f_count += 1
                    print(f"  ✗ {test_short} PASSED unexpectedly")
            else:
                # No failures shown - might mean tests passed
                if 'test result: ok' in output:
                    p2f_count += 1
                    print(f"  ✗ {test_short} PASSED unexpectedly (bug not detected)")
                else:
                    f2p_count += 1
                    print(f"  ✓ {test_short} likely FAILED")

        result['f2p'] = f2p_count
        result['p2f'] = p2f_count

        if f2p_count > 0:
            result['status'] = 'success'
            print(f"\n  ✅ SUCCESS: {f2p_count} f2p tests")
        else:
            result['status'] = 'no_f2p'
            print(f"\n  ❌ No f2p tests detected")
            print(f"  Output excerpt: {output[:500]}")

        return result

def main():
    # Load instances
    instances_file = Path('logs/bug_gen/juspay__hyperswitch.fece9bc3/correct_bugs.json')
    with open(instances_file) as f:
        instances = json.load(f)

    print('='*60)
    print('VALIDATING CORRECT BUG INSTANCES')
    print('='*60)
    print(f'Instances: {len(instances)}')
    print(f'Repo: {REPO_PATH}')

    if not REPO_PATH.exists():
        print(f"ERROR: Repo not found at {REPO_PATH}")
        return

    results = []
    for instance in instances:
        result = validate_instance(instance)
        results.append(result)

    # Print summary
    print(f"\n{'='*60}")
    print('SUMMARY')
    print(f"{'='*60}")

    total = len(results)
    success = sum(1 for r in results if r['status'] == 'success')
    total_f2p = sum(r['f2p'] for r in results)

    print(f"Total instances: {total}")
    print(f"Successful: {success}")
    print(f"Failed: {total - success}")
    print(f"Total f2p: {total_f2p}")

    print(f"\nDetails:")
    for r in results:
        status_icon = "✅" if r['status'] == 'success' else "❌"
        print(f"  {status_icon} {r['instance_id']}: {r['status']}, f2p={r['f2p']}, p2f={r['p2f']}")

    # Save results
    output_file = 'logs/bug_gen/juspay__hyperswitch.fece9bc3/correct_bugs_validation.json'
    with open(output_file, 'w') as f:
        json.dump(results, f, indent=2)
    print(f"\nResults saved to: {output_file}")

if __name__ == '__main__':
    main()
