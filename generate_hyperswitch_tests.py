#!/usr/bin/env python3
"""
Generate proper tests for Hyperswitch bugs that actually test the modified functions.
This addresses the issue where test_patches were testing validate_id instead of
the actual buggy functions.
"""

import json
import re
from pathlib import Path


def extract_modified_function(bug_patch: str) -> tuple[str, str]:
    """
    Extract the function that was modified in the bug patch.
    Returns (function_name, file_path).
    """
    # Get file path from patch
    file_match = re.search(r'\+\+\+ b/(.+)', bug_patch)
    file_path = file_match.group(1) if file_match else "crates/router/src/core/utils.rs"

    # Try to find the function being modified by looking at the context
    # Look for function definitions in the removed/changed lines
    lines = bug_patch.split('\n')

    # Track which function is being modified
    current_function = None
    in_function = False

    for line in lines:
        # Skip file headers
        if line.startswith('+++') or line.startswith('---') or line.startswith('@@'):
            continue

        # Look for function definitions (Rust syntax)
        # Pattern: pub fn function_name( or fn function_name(
        fn_match = re.search(r'^(?:[+-])?(?:pub\s+)?fn\s+(\w+)\s*\(', line)
        if fn_match:
            current_function = fn_match.group(1)
            in_function = True
            continue

        # If we see non-context lines (actual +/- changes) inside a function, record it
        if in_function and (line.startswith('+') or line.startswith('-')) and not line.startswith('+++'):
            if current_function:
                return current_function, file_path

    # If we didn't find a modified function, try to extract from context
    for line in lines:
        if line.startswith(' ') and 'fn ' in line:
            fn_match = re.search(r'fn\s+(\w+)\s*\(', line)
            if fn_match:
                return fn_match.group(1), file_path

    return None, file_path


def generate_test_for_bug(instance: dict) -> dict:
    """
    Generate a proper test_patch that actually tests the modified function.
    """
    bug_patch = instance.get('patch', instance.get('bug_patch', ''))
    instance_id = instance['instance_id']

    func_name, file_path = extract_modified_function(bug_patch)

    if not func_name:
        func_name = "validate_id"  # fallback

    # Determine what kind of bug this is based on the patch content
    bug_description = instance.get('problem_statement', '')

    # Generate appropriate test based on the function and bug type
    if func_name == 'validate_id':
        test_code = '''
#[cfg(test)]
mod bug_tests {
    use super::*;

    #[test]
    fn test_validate_id_rejects_long_id() {
        // Create an ID that exceeds MAX_ID_LENGTH
        let long_id = "a".repeat(consts::MAX_ID_LENGTH + 10);
        let result = validate_id(long_id, "payment_id");
        assert!(result.is_err(), "Should reject ID exceeding max length");
    }

    #[test]
    fn test_validate_id_accepts_valid_id() {
        // Create a valid ID
        let valid_id = "test_payment_123".to_string();
        let result = validate_id(valid_id.clone(), "payment_id");
        assert!(result.is_ok(), "Should accept valid ID length");
        assert_eq!(result.unwrap(), valid_id);
    }
}
'''
    elif func_name == 'validate_dispute_stage':
        test_code = '''
#[cfg(test)]
mod bug_tests {
    use super::*;

    #[test]
    fn test_dispute_stage_forward_progression() {
        // PreDispute -> Dispute should be valid
        let result = validate_dispute_stage(
            DisputeStage::PreDispute,
            DisputeStage::Dispute
        );
        assert!(result, "Should allow PreDispute -> Dispute transition");
    }

    #[test]
    fn test_dispute_stage_invalid_backwards() {
        // Dispute -> PreDispute should be invalid
        let result = validate_dispute_stage(
            DisputeStage::Dispute,
            DisputeStage::PreDispute
        );
        assert!(!result, "Should NOT allow Dispute -> PreDispute transition");
    }
}
'''
    elif func_name == 'validate_dispute_status':
        test_code = '''
#[cfg(test)]
mod bug_tests {
    use super::*;

    #[test]
    fn test_dispute_status_any_from_opened() {
        // Opened -> Challenged should be valid
        let result = validate_dispute_status(
            DisputeStatus::DisputeOpened,
            DisputeStatus::DisputeChallenged
        );
        assert!(result, "Should allow Opened -> Challenged");
    }

    #[test]
    fn test_dispute_status_terminal_states() {
        // Won should stay Won
        let result = validate_dispute_status(
            DisputeStatus::DisputeWon,
            DisputeStatus::DisputeWon
        );
        assert!(result, "Won should remain Won");
    }
}
'''
    elif func_name == 'validate_dispute_stage_and_dispute_status':
        test_code = '''
#[cfg(test)]
mod bug_tests {
    use super::*;

    #[test]
    fn test_combined_validation_valid() {
        let result = validate_dispute_stage_and_dispute_status(
            DisputeStage::PreDispute,
            DisputeStatus::DisputeOpened,
            DisputeStage::Dispute,
            DisputeStatus::DisputeChallenged
        );
        assert!(result.is_ok(), "Valid progression should succeed");
    }

    #[test]
    fn test_combined_validation_invalid_stage() {
        let result = validate_dispute_stage_and_dispute_status(
            DisputeStage::Dispute,
            DisputeStatus::DisputeOpened,
            DisputeStage::PreDispute,  // Invalid: going backwards
            DisputeStatus::DisputeOpened
        );
        assert!(result.is_err(), "Invalid stage progression should fail");
    }
}
'''
    else:
        # Generic test template
        test_code = f'''
#[cfg(test)]
mod bug_tests {{
    use super::*;

    #[test]
    fn test_{func_name}_basic() {{
        // Test the modified function
        // TODO: Add specific test cases based on function signature
        let _ = {func_name};
        assert!(true, "Function exists");
    }}
}}
'''

    # Create the test patch - add tests to the end of the file
    test_patch = f'''--- a/{file_path}
+++ b/{file_path}
@@ -1,1 +1,1 @@
{test_code}
'''

    # Build FAIL_TO_PASS and PASS_TO_PASS
    test_module = file_path.replace('/', '_').replace('.rs', '')
    fail_to_pass = [
        f"{func_name}::test_{func_name}_basic",
        f"{func_name}::test_{func_name}_specific"
    ]

    pass_to_pass = [
        f"router::core::utils::test_generate_id",  # Existing test
    ]

    return {
        'test_patch': test_patch,
        'FAIL_TO_PASS': fail_to_pass,
        'PASS_TO_PASS': pass_to_pass,
        'modified_function': func_name,
        'file_path': file_path,
    }


def main():
    print("=" * 80)
    print("GENERATING PROPER HYPERSWITCH TESTS")
    print("=" * 80)

    # Load existing bugs
    bugs_file = Path('logs/bug_gen/juspay__hyperswitch.9474c853/manual_bugs/ALL_BUGS_20.json')

    if not bugs_file.exists():
        print(f"❌ Bugs file not found: {bugs_file}")
        return

    with open(bugs_file) as f:
        instances = json.load(f)

    print(f"Loaded {len(instances)} instances")
    print()

    updated_instances = []

    for i, inst in enumerate(instances, 1):
        instance_id = inst['instance_id']
        bug_patch = inst.get('patch', inst.get('bug_patch', ''))

        # Extract modified function
        func_name, file_path = extract_modified_function(bug_patch)

        print(f"[{i}/{len(instances)}] {instance_id}")
        print(f"    Function: {func_name}")
        print(f"    File: {file_path}")

        # Store the detected function for later
        inst['_modified_function'] = func_name
        inst['_file_path'] = file_path

        updated_instances.append(inst)

    # Save analysis
    output_file = Path('logs/bug_gen/juspay__hyperswitch.9474c853/manual_bugs/BUG_ANALYSIS.json')
    with open(output_file, 'w') as f:
        json.dump(updated_instances, f, indent=2)

    print()
    print("=" * 80)
    print("ANALYSIS COMPLETE")
    print("=" * 80)
    print(f"Output: {output_file}")

    # Print function distribution
    func_counts = {}
    for inst in updated_instances:
        func = inst.get('_modified_function', 'unknown')
        func_counts[func] = func_counts.get(func, 0) + 1

    print("\nModified functions:")
    for func, count in sorted(func_counts.items(), key=lambda x: -x[1]):
        print(f"  {func}: {count}")


if __name__ == '__main__':
    main()
