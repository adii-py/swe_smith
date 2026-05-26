#!/usr/bin/env python3
"""
Create patches with CORRECT line numbers and context from actual files.
"""

import json
import subprocess
from pathlib import Path
from difflib import unified_diff

REPO_PATH = Path('/Users/aditya.singh.001/Desktop/SWE-smith/juspay__hyperswitch.fece9bc3')
BASE_COMMIT = 'fece9bc38b9890a1a40912ce2a95037842362e27'

def get_file_at_commit(commit, file_path):
    """Get file content at a specific commit."""
    result = subprocess.run(
        ['git', 'show', f'{commit}:{file_path}'],
        cwd=REPO_PATH,
        capture_output=True,
        text=True
    )
    if result.returncode != 0:
        print(f"Error getting {file_path}: {result.stderr}")
        return None
    return result.stdout

def create_unified_diff(original_lines, modified_lines, file_path):
    """Create proper unified diff."""
    from difflib import SequenceMatcher

    # Strip trailing newlines for comparison but keep track of original
    original_stripped = [l.rstrip('\n') for l in original_lines]
    modified_stripped = [l.rstrip('\n') for l in modified_lines]

    # Find the first different line
    first_diff = 0
    for i, (o, m) in enumerate(zip(original_stripped, modified_stripped)):
        if o != m:
            first_diff = i
            break
    else:
        if len(original_stripped) != len(modified_stripped):
            first_diff = min(len(original_stripped), len(modified_stripped))
        else:
            return ""  # No difference

    # Find the last different line
    last_diff_orig = len(original_stripped)
    last_diff_mod = len(modified_stripped)
    for i in range(1, min(len(original_stripped), len(modified_stripped)) + 1):
        if original_stripped[-i] != modified_stripped[-i]:
            last_diff_orig = len(original_stripped) - i + 1
            last_diff_mod = len(modified_stripped) - i + 1
            break

    # Calculate context (3 lines before and after)
    context_start = max(0, first_diff - 3)
    context_end_orig = min(len(original_stripped), last_diff_orig + 3)
    context_end_mod = min(len(modified_stripped), last_diff_mod + 3)

    # Build the diff
    diff_lines = [
        f'diff --git a/{file_path} b/{file_path}',
        f'--- a/{file_path}',
        f'+++ b/{file_path}',
        f'@@ -{context_start + 1},{context_end_orig - context_start} +{context_start + 1},{context_end_mod - context_start} @@'
    ]

    i = context_start
    while i < context_end_orig or i < context_end_mod:
        if i < len(original_stripped) and i < len(modified_stripped):
            if original_stripped[i] == modified_stripped[i]:
                diff_lines.append(' ' + original_stripped[i])
            else:
                # Find consecutive changes
                orig_block = []
                mod_block = []
                j = i
                while j < context_end_orig and j < context_end_mod:
                    if original_stripped[j] != modified_stripped[j]:
                        orig_block.append(original_stripped[j])
                        mod_block.append(modified_stripped[j])
                        j += 1
                    else:
                        break

                for line in orig_block:
                    diff_lines.append('-' + line)
                for line in mod_block:
                    diff_lines.append('+' + line)
                i = j - 1
        elif i < len(original_stripped):
            diff_lines.append('-' + original_stripped[i])
        else:
            diff_lines.append('+' + modified_stripped[i])
        i += 1

    return '\n'.join(diff_lines) + '\n'

def create_validate_id_bug():
    """Create validate_id bug with correct line numbers."""
    file_path = 'crates/router/src/core/utils.rs'

    # Get original content
    content = get_file_at_commit(BASE_COMMIT, file_path)
    if not content:
        return None

    lines = content.split('\n')

    # Find the line with "if id.len() > consts::MAX_ID_LENGTH"
    target_line = None
    for i, line in enumerate(lines):
        if 'if id.len() > consts::MAX_ID_LENGTH' in line:
            target_line = i
            break

    if target_line is None:
        print("Could not find target line in validate_id")
        return None

    print(f"Found target line at {target_line + 1}: {lines[target_line]}")

    # Create bug by changing consts::MAX_ID_LENGTH to 100
    modified_lines = lines.copy()
    modified_lines[target_line] = modified_lines[target_line].replace(
        'consts::MAX_ID_LENGTH', '100'
    )

    # Create bug patch
    bug_patch = create_unified_diff(lines, modified_lines, file_path)

    # Now create test patch - add tests at the end of existing tests
    # Find the last closing brace of mod tests
    test_module_end = None
    for i in range(len(lines) - 1, -1, -1):
        if '#[cfg(test)]' in lines[i] or ('mod tests' in lines[i] and '{' in lines[i]):
            # Found test module start, now find its end
            brace_count = 0
            for j in range(i, len(lines)):
                brace_count += lines[j].count('{')
                brace_count -= lines[j].count('}')
                if brace_count == 0 and j > i:
                    test_module_end = j
                    break
            break

    if test_module_end is None:
        print("Could not find test module end")
        return None

    print(f"Found test module end at line {test_module_end + 1}")

    # Add tests before the closing brace
    test_code = '''
    #[test]
    fn test_validate_id_uses_max_id_length_constant() {
        // Source-code analysis test: verify validate_id uses MAX_ID_LENGTH constant
        // This test will fail if the bug is present (using hardcoded 100 instead)
        let utils_source = include_str!("../utils.rs");
        assert!(
            utils_source.contains("if id.len() > consts::MAX_ID_LENGTH"),
            "validate_id should use consts::MAX_ID_LENGTH for validation, not hardcoded value"
        );
    }

    #[test]
    fn test_validate_id_rejects_65_char_id() {
        // Test that 65 character ID is rejected (MAX_ID_LENGTH is 64)
        let long_id = "a".repeat(65);
        let result = validate_id(long_id, "payment_id");
        assert!(result.is_err(), "65 char ID should be rejected when MAX_ID_LENGTH is 64");
    }
'''

    # Insert test code before the closing brace
    test_lines = lines.copy()
    test_lines.insert(test_module_end, test_code)

    test_patch = create_unified_diff(lines, test_lines, file_path)

    return {
        "instance_id": "juspay__hyperswitch.fece9bc3.validate_id_max_length",
        "repo": "juspay/hyperswitch",
        "base_commit": BASE_COMMIT,
        "patch": bug_patch,
        "test_patch": test_patch,
        "problem_statement": "The validate_id function in crates/router/src/core/utils.rs has a bug where the maximum ID length validation was incorrectly changed from consts::MAX_ID_LENGTH (64) to 100. This allows IDs longer than the intended 64-character limit to pass validation.",
        "hints_text": "Look for the validate_id function in crates/router/src/core/utils.rs. The validation checks id.len() against a threshold value.",
        "version": BASE_COMMIT,
        "language": "rust",
        "FAIL_TO_PASS": [
            "router::core::utils::tests::test_validate_id_uses_max_id_length_constant",
            "router::core::utils::tests::test_validate_id_rejects_65_char_id"
        ],
        "PASS_TO_PASS": [
            "router::core::utils::tests::validate_id_length_constraint",
            "router::core::utils::tests::validate_id_proper_response",
            "router::core::utils::tests::test_generate_id"
        ],
        "test_cmd": "CARGO_BUILD_JOBS=1 cargo test --release -p router --lib core::utils::tests --no-fail-fast -- --nocapture"
    }

def create_consts_bug():
    """Create constant change bug with correct line numbers."""
    consts_path = 'crates/common_utils/src/consts.rs'
    id_type_path = 'crates/common_utils/src/id_type.rs'

    # Get consts.rs content
    consts_content = get_file_at_commit(BASE_COMMIT, consts_path)
    if not consts_content:
        return None

    consts_lines = consts_content.split('\n')

    # Find the line with MAX_ALLOWED_MERCHANT_NAME_LENGTH
    target_line = None
    for i, line in enumerate(consts_lines):
        if 'pub const MAX_ALLOWED_MERCHANT_NAME_LENGTH: usize = 64;' in line:
            target_line = i
            break

    if target_line is None:
        print("Could not find MAX_ALLOWED_MERCHANT_NAME_LENGTH in consts.rs")
        return None

    print(f"Found constant at line {target_line + 1}: {consts_lines[target_line]}")

    # Create bug by changing 64 to 128
    modified_consts = consts_lines.copy()
    modified_consts[target_line] = modified_consts[target_line].replace('= 64', '= 128')

    bug_patch = create_unified_diff(consts_lines, modified_consts, consts_path)

    # Now create test patch in id_type.rs
    id_type_content = get_file_at_commit(BASE_COMMIT, id_type_path)
    if not id_type_content:
        return None

    id_type_lines = id_type_content.split('\n')

    # Find the test module and add a test
    # Look for mod tests in id_type.rs
    test_module_end = None
    for i in range(len(id_type_lines) - 1, -1, -1):
        if '#[cfg(test)]' in id_type_lines[i] or ('mod tests' in id_type_lines[i] and '{' in id_type_lines[i]):
            brace_count = 0
            for j in range(i, len(id_type_lines)):
                brace_count += id_type_lines[j].count('{')
                brace_count -= id_type_lines[j].count('}')
                if brace_count == 0 and j > i:
                    test_module_end = j
                    break
            break

    if test_module_end is None:
        # Try finding any test function
        for i in range(len(id_type_lines) - 1, -1, -1):
            if '#[test]' in id_type_lines[i]:
                # Find end of this test function
                for j in range(i + 1, len(id_type_lines)):
                    if id_type_lines[j].startswith('    }') or id_type_lines[j] == '}':
                        test_module_end = j
                        break
                break

    if test_module_end is None:
        print("Could not find test insertion point in id_type.rs")
        # Just append at end of file
        test_module_end = len(id_type_lines) - 1

    print(f"Found test insertion point at line {test_module_end + 1}")

    # Add test that directly checks the constant value
    test_code = '''

#[cfg(test)]
mod regression_max_allowed_merchant_name_length {
    #[test]
    fn test_constant_value_is_64() {
        // Direct constant value check - will fail if bug is present (value = 128)
        assert_eq!(crate::MAX_ALLOWED_MERCHANT_NAME_LENGTH, 64);
    }
}
'''

    test_lines = id_type_lines.copy()
    test_lines.insert(test_module_end + 1, test_code)

    test_patch = create_unified_diff(id_type_lines, test_lines, id_type_path)

    return {
        "instance_id": "juspay__hyperswitch.fece9bc3.max_merchant_name_length",
        "repo": "juspay/hyperswitch",
        "base_commit": BASE_COMMIT,
        "patch": bug_patch,
        "test_patch": test_patch,
        "problem_statement": "The MAX_ALLOWED_MERCHANT_NAME_LENGTH constant in crates/common_utils/src/consts.rs was incorrectly changed from 64 to 128. This affects validation logic for merchant names.",
        "hints_text": "Look for MAX_ALLOWED_MERCHANT_NAME_LENGTH in crates/common_utils/src/consts.rs. The value should be 64.",
        "version": BASE_COMMIT,
        "language": "rust",
        "FAIL_TO_PASS": [
            "common_utils::id_type::regression_max_allowed_merchant_name_length::test_constant_value_is_64"
        ],
        "PASS_TO_PASS": [],
        "test_cmd": "CARGO_BUILD_JOBS=1 cargo test --release -p common_utils --lib regression_max_allowed_merchant_name_length --no-fail-fast -- --nocapture"
    }

def main():
    print("Creating bugs with CORRECT line numbers...")
    print()

    instances = []

    # Instance 1: validate_id bug
    print("Creating validate_id bug...")
    inst1 = create_validate_id_bug()
    if inst1:
        instances.append(inst1)
        print("✓ Created validate_id bug")
    print()

    # Instance 2: consts bug
    print("Creating consts bug...")
    inst2 = create_consts_bug()
    if inst2:
        instances.append(inst2)
        print("✓ Created consts bug")
    print()

    # Save
    output_file = Path('logs/bug_gen/juspay__hyperswitch.fece9bc3/correct_bugs.json')
    output_file.parent.mkdir(parents=True, exist_ok=True)

    with open(output_file, 'w') as f:
        json.dump(instances, f, indent=2)

    print(f"Created {len(instances)} bug instances with correct patches")
    print(f"Saved to: {output_file}")

if __name__ == '__main__':
    main()
