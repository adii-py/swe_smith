#!/usr/bin/env python3
"""
Create new instances following the successful patterns from:
1. juspay__hyperswitch-9474c853 (simple constant change)
2. juspay__hyperswitch.fece9bc3.pr_12234 (function signature change with source tests)
"""

import json
import re
import requests
from pathlib import Path
from difflib import unified_diff


def fetch_file(repo: str, commit: str, file_path: str) -> str:
    """Fetch file content from GitHub."""
    url = f"https://raw.githubusercontent.com/{repo}/{commit}/{file_path}"
    try:
        resp = requests.get(url, timeout=30)
        if resp.status_code == 200:
            return resp.text
    except Exception as e:
        print(f"  Error: {e}")
    return None


def create_constant_bug_instance(file_content: str, file_path: str, constant_name: str, old_val: str, new_val: str) -> dict:
    """Create a simple constant change bug."""

    # Find and replace the constant
    pattern = rf'(const\s+{constant_name}:\s*\w+\s*=\s*){old_val}'
    buggy_content = re.sub(pattern, rf'\g<1>{new_val}', file_content)

    if buggy_content == file_content:
        return None

    # Create patch
    diff = unified_diff(
        file_content.splitlines(keepends=True),
        buggy_content.splitlines(keepends=True),
        fromfile=f'a/{file_path}',
        tofile=f'b/{file_path}'
    )

    patch = ''.join(diff)

    # Create instance
    instance = {
        'instance_id': f'juspay__hyperswitch.fece9bc3.manual_{constant_name.lower()}',
        'repo': 'juspay__hyperswitch.fece9bc3',
        'base_commit': 'fece9bc38b9890a1a40912ce2a95037842362e27',
        'patch': patch,
        'test_patch': '',  # Will add tests
        'problem_statement': f'The {constant_name} constant was incorrectly changed from {old_val} to {new_val}. '
                            f'This affects validation logic and can cause incorrect behavior.',
        'hints_text': f'Look for {constant_name} in {file_path}. The value should be {old_val} but was changed to {new_val}.',
        'version': 'fece9bc38b9890a1a40912ce2a95037842362e27',
        'language': 'rust',
        '_bug_type': 'constant_change'
    }

    return instance


def create_threshold_bug_instance(file_content: str, file_path: str, old_threshold: int, new_threshold: int) -> dict:
    """Create a threshold comparison bug (like > 64 -> > 100)."""

    # Find patterns like "len() > X" or "count > X"
    patterns = [
        rf'(\.len\(\)\s*)>(\s*{old_threshold})(\s*[{{;\)])',
        rf'(\.count\(\)\s*)>(\s*{old_threshold})(\s*[{{;\)])',
        rf'(count\s*)>(\s*{old_threshold})(\s*[{{;\)])',
    ]

    buggy_content = file_content
    for pattern in patterns:
        buggy_content = re.sub(pattern, rf'\g<1>>\g<2>{new_threshold}\g<3>', buggy_content)
        if buggy_content != file_content:
            break

    if buggy_content == file_content:
        return None

    # Create patch
    diff = unified_diff(
        file_content.splitlines(keepends=True),
        buggy_content.splitlines(keepends=True),
        fromfile=f'a/{file_path}',
        tofile=f'b/{file_path}'
    )

    patch = ''.join(diff)

    instance = {
        'instance_id': f'juspay__hyperswitch.fece9bc3.manual_threshold_{old_threshold}_{new_threshold}',
        'repo': 'juspay__hyperswitch.fece9bc3',
        'base_commit': 'fece9bc38b9890a1a40912ce2a95037842362e27',
        'patch': patch,
        'test_patch': '',
        'problem_statement': f'A validation threshold was incorrectly changed from {old_threshold} to {new_threshold}. '
                            f'This allows values that should be rejected to pass validation.',
        'hints_text': f'Look for comparisons with {old_threshold} in {file_path}. The threshold should be {old_threshold}.',
        'version': 'fece9bc38b9890a1a40912ce2a95037842362e27',
        'language': 'rust',
        '_bug_type': 'threshold_change'
    }

    return instance


def create_test_patch_for_constant(file_path: str, constant_name: str, expected_value: str) -> str:
    """Create a test patch that checks for the constant value."""

    test_code = f'''
#[cfg(test)]
mod regression_tests {{
    use super::*;

    #[test]
    fn test_{constant_name.lower()}_value() {{
        // Source code analysis: verify constant has correct value
        let source = include_str!("{file_path.split('/')[-1]}");
        assert!(
            source.contains("const {constant_name}: usize = {expected_value}"),
            "{constant_name} should be {expected_value}"
        );
    }}
}}
'''

    return test_code


def main():
    """Create working instances following successful patterns."""

    print("=" * 70)
    print("CREATING WORKING INSTANCES")
    print("=" * 70)

    repo = 'juspay/hyperswitch'
    commit = 'fece9bc38b9890a1a40912ce2a95037842362e27'

    instances = []

    # Pattern 1: Simple constant changes
    print("\n--- Pattern 1: Constant Changes ---\n")

    # Fetch files that might have constants
    files_to_check = [
        'crates/common_utils/src/consts.rs',
        'crates/router/src/consts.rs',
        'crates/common_utils/src/lib.rs',
    ]

    for file_path in files_to_check:
        print(f"Checking: {file_path}")
        content = fetch_file(repo, commit, file_path)
        if not content:
            continue

        # Look for const definitions with numeric values
        const_patterns = [
            (r'const\s+MAX_\w+:\s*usize\s*=\s*(\d+)', 'MAX_*'),
            (r'const\s+MIN_\w+:\s*usize\s*=\s*(\d+)', 'MIN_*'),
            (r'const\s+DEFAULT_\w+:\s*usize\s*=\s*(\d+)', 'DEFAULT_*'),
        ]

        for pattern, desc in const_patterns:
            matches = re.finditer(pattern, content)
            for match in list(matches)[:2]:  # Take first 2 matches
                const_match = re.search(r'(const\s+)(\w+)(:\s*usize\s*=\s*)(\d+)', match.group(0))
                if const_match:
                    const_name = const_match.group(2)
                    old_val = const_match.group(4)
                    new_val = str(int(old_val) * 2)  # Double the value

                    print(f"  Found {const_name} = {old_val}")

                    instance = create_constant_bug_instance(
                        content, file_path, const_name, old_val, new_val
                    )

                    if instance:
                        instances.append(instance)
                        print(f"  ✓ Created instance for {const_name}")

    # Pattern 2: Threshold changes (like > 64 -> > 100)
    print("\n--- Pattern 2: Threshold Changes ---\n")

    threshold_files = [
        'crates/router/src/core/utils.rs',
        'crates/router/src/core/validation.rs',
        'crates/common_utils/src/validation.rs',
    ]

    for file_path in threshold_files:
        print(f"Checking: {file_path}")
        content = fetch_file(repo, commit, file_path)
        if not content:
            continue

        # Look for common threshold values
        thresholds = [64, 128, 256, 512, 1024]
        for threshold in thresholds:
            if f'> {threshold}' in content or f'>= {threshold}' in content:
                new_threshold = int(threshold * 1.5)
                instance = create_threshold_bug_instance(
                    content, file_path, threshold, new_threshold
                )
                if instance:
                    instances.append(instance)
                    print(f"  ✓ Created threshold instance: {threshold} -> {new_threshold}")
                    break  # Only one per file

    # Pattern 3: Function signature changes (remove optional param)
    print("\n--- Pattern 3: Function Signature Changes ---\n")

    sig_files = [
        'crates/common_utils/src/id_type/payment.rs',
        'crates/common_utils/src/id_type/connector.rs',
    ]

    for file_path in sig_files:
        print(f"Checking: {file_path}")
        content = fetch_file(repo, commit, file_path)
        if not content:
            continue

        # Look for functions with optional parameters
        func_pattern = r'pub fn (\w+)\(&self(?:,\s*(\w+):\s*&str)?\)(?:\s*->\s*\w+)?\s*\{'
        matches = re.finditer(func_pattern, content)

        for match in list(matches)[:1]:
            func_name = match.group(1)
            has_param = match.group(2)

            if has_param:
                print(f"  Found function with param: {func_name}({has_param})")
                # Create bug by removing the parameter
                # This requires more sophisticated patch creation
                print(f"  (Skipping - requires manual patch creation)")

    # Save instances
    output_path = Path('logs/bug_gen/juspay__hyperswitch.fece9bc3/manual_working_instances.json')
    with open(output_path, 'w') as f:
        json.dump(instances, f, indent=2)

    print(f"\n{'=' * 70}")
    print(f"Created {len(instances)} instances")
    print(f"Saved to: {output_path}")

    if instances:
        print("\nInstances created:")
        for inst in instances:
            print(f"  - {inst['instance_id']} ({inst['_bug_type']})")

    print("\nNext: Add test patches and run validation")


if __name__ == '__main__':
    main()
