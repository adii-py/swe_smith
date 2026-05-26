#!/usr/bin/env python3
"""Fix manual patches to have proper test patch format with correct line numbers."""

import json
from pathlib import Path

def create_test_patch_at_end(file_path: str, test_code: str, end_line: int) -> str:
    """Create a test patch that appends at end of file."""
    test_lines = test_code.strip().split('\n')

    diff = f"""diff --git a/{file_path} b/{file_path}
--- a/{file_path}
+++ b/{file_path}
@@ -{end_line},0 +{end_line},{len(test_lines)} @@
"""
    for line in test_lines:
        diff += '+' + line + '\n'

    return diff

def main():
    # File sizes (from earlier curl commands)
    file_sizes = {
        'crates/common_utils/src/consts.rs': 240,
        'crates/router/src/consts.rs': 404,
    }

    input_path = Path('logs/bug_gen/juspay__hyperswitch.fece9bc3/manual_fixed_patches_v2.json')
    output_path = Path('logs/bug_gen/juspay__hyperswitch.fece9bc3/manual_final.json')

    print("Loading instances...")
    with open(input_path) as f:
        data = json.load(f)

    print(f"Fixing {len(data)} instances...\n")

    for inst in data:
        instance_id = inst['instance_id']
        print(f"{instance_id}:")

        # Get file path from bug patch
        file_path = None
        for line in inst['patch'].split('\n'):
            if line.startswith('--- a/'):
                file_path = line[6:].strip()
                break

        if not file_path:
            print("  ERROR: Could not find file path")
            continue

        end_line = file_sizes.get(file_path, 1)
        print(f"  File: {file_path}, end_line: {end_line}")

        # Extract constant name from instance_id
        const_name = instance_id.split('.manual_')[-1].upper()

        # Extract expected value from patch
        expected_val = None
        for line in inst['patch'].split('\n'):
            if line.startswith('-') and 'const' in line and '=' in line:
                import re
                match = re.search(r'=\s*(\d+)', line)
                if match:
                    expected_val = match.group(1)
                    break

        if not expected_val:
            print("  ERROR: Could not find expected value")
            continue

        print(f"  Constant: {const_name}, Expected: {expected_val}")

        # Create simpler test code - just one test that checks the source
        # Put tests in the consts::tests module which is the standard Rust pattern
        test_module_name = f"regression_{const_name.lower()}"

        test_code = f'''#[cfg(test)]
mod {test_module_name} {{
    #[test]
    fn test_constant_value() {{
        let source = include_str!("{file_path.split('/')[-1]}");
        assert!(
            source.contains("const {const_name}: usize = {expected_val}"),
            "{const_name} should be {expected_val} but was changed"
        );
    }}
}}'''

        # Create test patch
        test_patch = create_test_patch_at_end(file_path, test_code, end_line)
        inst['test_patch'] = test_patch
        print(f"  ✓ Updated test patch")

        # Update FAIL_TO_PASS with correct test names
        # Module structure: consts::tests::regression_<name>::test_constant_value
        if 'common_utils' in file_path:
            crate_prefix = 'common_utils::'
        elif 'router' in file_path:
            crate_prefix = 'router::'
        else:
            crate_prefix = ''

        # The test is in the consts module directly, not in a tests submodule
        test_path = f"{crate_prefix}consts::{test_module_name}::test_constant_value"
        inst['FAIL_TO_PASS'] = [test_path]
        print(f"  ✓ Updated FAIL_TO_PASS: {test_path}")

        # Add empty PASS_TO_PASS
        inst['PASS_TO_PASS'] = []

    # Save
    with open(output_path, 'w') as f:
        json.dump(data, f, indent=2)

    print(f"\n{'='*50}")
    print(f"Saved to: {output_path}")

if __name__ == '__main__':
    main()
