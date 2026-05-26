#!/usr/bin/env python3
"""Fix manual patches to have proper diff format with 'diff --git' header."""

import json
from pathlib import Path

def add_diff_git_header(patch: str, file_path: str) -> str:
    """Add diff --git header if missing."""
    if patch.startswith('diff --git'):
        return patch

    # Extract file path from the --- line if not provided
    lines = patch.split('\n')
    for line in lines:
        if line.startswith('--- a/'):
            file_path = line[6:].strip()
            break

    # Add the diff --git header
    header = f"diff --git a/{file_path} b/{file_path}\n"
    return header + patch

def fix_test_patch_format(patch: str, file_path: str) -> str:
    """Fix test patch format to append tests at end of file."""
    # The test patch format should be:
    # diff --git a/<file> b/<file>
    # --- a/<file>
    # +++ b/<file>
    # @@ -<end_line>,0 +<end_line>,<test_lines> @@
    # +<test_line_1>
    # ...

    # Extract the test code (lines starting with +)
    test_lines = []
    for line in patch.split('\n'):
        if line.startswith('+') and not line.startswith('+++ '):
            test_lines.append(line[1:])

    if not test_lines:
        return patch

    # Get original file path
    for line in patch.split('\n'):
        if line.startswith('--- a/'):
            file_path = line[6:].strip()
            break

    # Create new patch with correct format
    # We'll append at line 1 (beginning) since we don't know file length
    # Actually, let's use the original format but ensure headers are correct

    new_patch = f"""diff --git a/{file_path} b/{file_path}
--- a/{file_path}
+++ b/{file_path}
@@ -1,0 +1,{len(test_lines)} @@
"""
    for line in test_lines:
        new_patch += '+' + line + '\n'

    return new_patch

def main():
    input_path = Path('logs/bug_gen/juspay__hyperswitch.fece9bc3/manual_fixed_patches.json')
    output_path = Path('logs/bug_gen/juspay__hyperswitch.fece9bc3/manual_fixed_patches_v2.json')

    print("Loading instances...")
    with open(input_path) as f:
        data = json.load(f)

    print(f"Fixing {len(data)} instances...\n")

    for inst in data:
        instance_id = inst['instance_id']
        print(f"{instance_id}:")

        # Fix bug patch
        bug_patch = inst.get('patch', '')
        if bug_patch and not bug_patch.startswith('diff --git'):
            bug_patch = add_diff_git_header(bug_patch, '')
            inst['patch'] = bug_patch
            print("  ✓ Fixed bug patch header")

        # Fix test patch format
        test_patch = inst.get('test_patch', '')
        if test_patch:
            # Get file path from bug patch
            file_path = ''
            for line in bug_patch.split('\n'):
                if line.startswith('--- a/'):
                    file_path = line[6:].strip()
                    break

            test_patch = fix_test_patch_format(test_patch, file_path)
            inst['test_patch'] = test_patch
            print("  ✓ Fixed test patch format")

        # Add FAIL_TO_PASS and PASS_TO_PASS if missing
        if 'FAIL_TO_PASS' not in inst:
            # Extract test names from test code
            test_names = []
            for line in inst.get('test_patch', '').split('\n'):
                if 'fn test_' in line and 'fn test_' in line:
                    # Extract test function name
                    import re
                    match = re.search(r'fn\s+(test_\w+)', line)
                    if match:
                        test_names.append(match.group(1))

            if test_names:
                # Determine crate name from file path
                file_path = ''
                for line in inst.get('patch', '').split('\n'):
                    if line.startswith('--- a/'):
                        file_path = line[6:].strip()
                        break

                if 'common_utils' in file_path:
                    crate_prefix = 'common_utils::'
                elif 'router' in file_path:
                    crate_prefix = 'router::'
                else:
                    crate_prefix = ''

                # Format: crate::module::test_name
                # For consts.rs tests, they go in the root tests module
                fail_to_pass = [f"{crate_prefix}consts::tests::{t}" if t.startswith('test_') else t for t in test_names]
                inst['FAIL_TO_PASS'] = fail_to_pass
                print(f"  ✓ Added FAIL_TO_PASS: {fail_to_pass}")

        # Add test command
        if 'test_cmd' not in inst:
            file_path = ''
            for line in inst.get('patch', '').split('\n'):
                if line.startswith('--- a/'):
                    file_path = line[6:].strip()
                    break

            if 'common_utils' in file_path:
                inst['test_cmd'] = 'cargo test -p common_utils --lib -- --nocapture'
            elif 'router' in file_path:
                inst['test_cmd'] = 'cargo test -p router --lib -- --nocapture'
            print(f"  ✓ Added test_cmd")

    # Save
    with open(output_path, 'w') as f:
        json.dump(data, f, indent=2)

    print(f"\n{'='*50}")
    print(f"Saved to: {output_path}")

if __name__ == '__main__':
    main()
