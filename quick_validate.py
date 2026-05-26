#!/usr/bin/env python3
"""Quick validation of the final instances."""

import json
import subprocess
from pathlib import Path

REPO = Path('/Users/aditya.singh.001/Desktop/SWE-smith/juspay__hyperswitch.fece9bc3')

def run_cmd(cmd, cwd=None, input_text=None, timeout=300):
    result = subprocess.run(
        cmd, cwd=cwd or REPO, input=input_text,
        capture_output=True, text=True, timeout=timeout
    )
    return result.returncode, result.stdout, result.stderr

def test_instance(instance):
    print(f"\n{'='*60}")
    print(f"Testing: {instance['instance_id']}")
    print(f"{'='*60}")

    # Reset repo
    run_cmd(['git', 'reset', '--hard', 'HEAD'])
    run_cmd(['git', 'clean', '-fd'])

    # Apply bug patch
    code, _, err = run_cmd(['git', 'apply', '-'], input_text=instance['patch'])
    if code != 0:
        print(f"❌ Bug patch failed: {err[:200]}")
        return False
    print("✓ Bug patch applied")

    # Apply test patch
    code, _, err = run_cmd(['git', 'apply', '-'], input_text=instance['test_patch'])
    if code != 0:
        print(f"❌ Test patch failed: {err[:200]}")
        return False
    print("✓ Test patch applied")

    # Run tests
    print(f"Running: {instance['test_cmd']}")
    code, stdout, stderr = run_cmd(instance['test_cmd'].split(), timeout=600)
    output = stdout + stderr

    # Check for FAIL_TO_PASS tests
    f2p = 0
    for test in instance['FAIL_TO_PASS']:
        test_name = test.split('::')[-1]
        if 'FAILED' in output and test_name in output:
            f2p += 1
            print(f"✓ {test_name} FAILED (f2p)")
        elif 'test result: ok' in output:
            print(f"✗ {test_name} PASSED unexpectedly")
        else:
            print(f"? {test_name} status unclear")

    print(f"\nf2p = {f2p}")
    return f2p > 0

def main():
    with open('final_instances.json') as f:
        instances = json.load(f)

    print('='*60)
    print('QUICK VALIDATION OF FINAL INSTANCES')
    print('='*60)

    results = []
    for inst in instances:
        success = test_instance(inst)
        results.append((inst['instance_id'], success))

    print(f"\n{'='*60}")
    print('SUMMARY')
    print(f"{'='*60}")
    for name, success in results:
        icon = "✅" if success else "❌"
        print(f"{icon} {name}")

if __name__ == '__main__':
    main()
