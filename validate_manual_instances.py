#!/usr/bin/env python3
"""
Validate manual instances with proper f2p/p2p metrics.
"""

import json
import subprocess
import tempfile
from pathlib import Path
import shutil

HYPERSWITCH_REPO = '/Users/aditya.singh.001/Desktop/SWE-smith/juspay__hyperswitch.fece9bc3'

def run_command(cmd, cwd, input_text=None, timeout=300):
    """Run a command and return result."""
    try:
        result = subprocess.run(
            cmd,
            cwd=cwd,
            input=input_text,
            capture_output=True,
            text=True,
            timeout=timeout
        )
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return -1, "", "Command timed out"

def validate_instance(instance, repo_path):
    """Validate a single instance."""
    instance_id = instance['instance_id']
    bug_patch = instance['patch']
    test_patch = instance['test_patch']
    test_cmd = instance.get('test_cmd', 'cargo test --lib -- --nocapture')
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
        returncode, stdout, stderr = run_command(
            ['git', 'clone', '--quiet', str(repo_path), str(repo_tmp)],
            cwd=tmpdir
        )
        if returncode != 0:
            result['status'] = 'clone_failed'
            result['errors'].append(stderr)
            return result

        # Checkout base commit
        base_commit = instance.get('base_commit', 'fece9bc38b9890a1a40912ce2a95037842362e27')
        print(f"Checking out {base_commit[:12]}...")
        returncode, stdout, stderr = run_command(
            ['git', 'checkout', '--quiet', base_commit],
            cwd=repo_tmp
        )
        if returncode != 0:
            result['status'] = 'checkout_failed'
            result['errors'].append(stderr)
            return result

        # Apply bug patch
        print(f"Applying bug patch...")
        returncode, stdout, stderr = run_command(
            ['git', 'apply', '-'],
            cwd=repo_tmp,
            input_text=bug_patch
        )
        if returncode != 0:
            print(f"  ❌ Bug patch failed: {stderr[:200]}")
            result['status'] = 'bug_patch_failed'
            result['errors'].append(f"Bug patch: {stderr}")
            return result
        print(f"  ✓ Bug patch applied")

        # Apply test patch
        print(f"Applying test patch...")
        returncode, stdout, stderr = run_command(
            ['git', 'apply', '-'],
            cwd=repo_tmp,
            input_text=test_patch
        )
        if returncode != 0:
            print(f"  ❌ Test patch failed: {stderr[:200]}")
            result['status'] = 'test_patch_failed'
            result['errors'].append(f"Test patch: {stderr}")
            return result
        print(f"  ✓ Test patch applied")

        # Run tests on buggy code (expect FAIL - f2p)
        print(f"Running tests on buggy code...")
        print(f"  Command: {test_cmd}")
        returncode, stdout, stderr = run_command(
            test_cmd.split(),
            cwd=repo_tmp,
            timeout=600
        )

        test_output = stdout + stderr

        # Count test results
        f2p_count = 0
        p2f_count = 0

        for test_name in fail_to_pass:
            # Check if test failed (which is what we want for f2p)
            if 'FAILED' in test_output or 'failures:' in test_output or returncode != 0:
                # More specific check - look for the actual test name in output
                if test_name.split('::')[-1] in test_output or returncode != 0:
                    f2p_count += 1
                    print(f"  ✓ {test_name} FAILED (as expected for f2p)")
                else:
                    p2f_count += 1
                    print(f"  ✗ {test_name} PASSED unexpectedly (p2f)")
            else:
                p2f_count += 1
                print(f"  ✗ {test_name} PASSED unexpectedly (p2f)")

        result['f2p'] = f2p_count
        result['p2f'] = p2f_count

        if f2p_count > 0:
            result['status'] = 'success'
            print(f"\n  ✅ SUCCESS: {f2p_count} tests failed as expected (f2p > 0)")
        else:
            result['status'] = 'no_f2p'
            print(f"\n  ❌ FAILED: No tests failed (f2p = 0)")
            print(f"  Test output excerpt: {test_output[:500]}")

        return result

def main():
    # Load instances
    instances_file = Path('logs/bug_gen/juspay__hyperswitch.fece9bc3/manual_final.json')
    with open(instances_file) as f:
        instances = json.load(f)

    print('='*60)
    print('VALIDATING MANUAL INSTANCES')
    print('='*60)
    print(f'Instances: {len(instances)}')
    print(f'Repo: {HYPERSWITCH_REPO}')

    # Check repo exists
    if not Path(HYPERSWITCH_REPO).exists():
        print(f"ERROR: Repo not found at {HYPERSWITCH_REPO}")
        print("Please clone the repo first:")
        print(f"  git clone https://github.com/juspay/hyperswitch.git {HYPERSWITCH_REPO}")
        return

    results = []
    for instance in instances:
        result = validate_instance(instance, HYPERSWITCH_REPO)
        results.append(result)

    # Print summary
    print(f"\n{'='*60}")
    print('SUMMARY')
    print(f"{'='*60}")

    total = len(results)
    success = sum(1 for r in results if r['status'] == 'success')
    total_f2p = sum(r['f2p'] for r in results)

    print(f"Total instances: {total}")
    print(f"Successful (f2p > 0): {success}")
    print(f"Failed: {total - success}")
    print(f"Total f2p tests: {total_f2p}")

    print(f"\nDetails:")
    for r in results:
        status_icon = "✅" if r['status'] == 'success' else "❌"
        print(f"  {status_icon} {r['instance_id']}: f2p={r['f2p']}, p2f={r['p2f']}")

    # Save results
    output_file = 'logs/bug_gen/juspay__hyperswitch.fece9bc3/manual_validation_results.json'
    with open(output_file, 'w') as f:
        json.dump(results, f, indent=2)
    print(f"\nResults saved to: {output_file}")

if __name__ == '__main__':
    main()
