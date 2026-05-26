#!/usr/bin/env python3
"""
Generate high-quality Hyperswitch bugs following the successful pattern from pr_12234.

Pattern:
1. Bug patch: Modify function signature (remove parameter) and update all call sites
2. Test patch: Add tests to EXISTING test module in a DIFFERENT file
3. Tests use include_str! to verify source code patterns
4. Ensures compilation and f2p > 0
"""

import json
import re
import subprocess
from pathlib import Path
from difflib import unified_diff

REPO_PATH = Path('/Users/aditya.singh.001/Desktop/SWE-smith/juspay__hyperswitch.fece9bc3')
BASE_COMMIT = 'fece9bc38b9890a1a40912ce2a95037842362e27'

def run_git(cmd, cwd=None):
    """Run git command."""
    result = subprocess.run(
        cmd,
        cwd=cwd or REPO_PATH,
        capture_output=True,
        text=True,
        shell=isinstance(cmd, str)
    )
    return result.returncode, result.stdout, result.stderr

def get_file_content(commit, file_path):
    """Get file content at a specific commit."""
    code, stdout, stderr = run_git(f'git show {commit}:{file_path}')
    if code != 0:
        return None
    return stdout

def find_test_modules(file_content):
    """Find existing test modules in file content."""
    # Look for #[cfg(test)] mod tests { ... }
    pattern = r'#\[cfg\(test\)\]\s*mod\s+(\w+)\s*\{'
    matches = re.finditer(pattern, file_content)
    return [(m.group(1), m.start()) for m in matches]

def find_existing_tests_end(file_content, module_start):
    """Find the end of test module (last closing brace at module level)."""
    # Simple heuristic: find the matching closing brace
    depth = 0
    pos = module_start
    while pos < len(file_content):
        if file_content[pos] == '{':
            depth += 1
        elif file_content[pos] == '}':
            depth -= 1
            if depth == 0:
                return pos
        pos += 1
    return len(file_content) - 1

def create_source_analysis_test(target_file: str, search_pattern: str, test_name: str) -> str:
    """Create a source analysis test."""
    file_name = target_file.split('/')[-1]
    return f'''
    #[test]
    fn {test_name}() {{
        let source = include_str!("{file_name}");
        assert!(
            source.contains("{search_pattern}"),
            "Source code should contain: {search_pattern}"
        );
    }}
'''

def create_unified_diff(original_lines, modified_lines, file_path):
    """Create unified diff."""
    diff = unified_diff(
        original_lines,
        modified_lines,
        fromfile=f'a/{file_path}',
        tofile=f'b/{file_path}'
    )
    return ''.join(diff)

def generate_function_signature_bug():
    """
    Generate a bug that modifies a function signature.
    Following the pattern from pr_12234.
    """
    instances = []

    # Candidate 1: Modify a function in common_utils that takes a parameter
    # and is called from multiple places

    # First, let's find good candidates
    candidates = [
        {
            'name': 'get_merchant_id',
            'file': 'crates/common_utils/src/id_type/merchant.rs',
            'test_file': 'crates/common_utils/src/id_type.rs',
            'search_module': 'merchant_reference_id_tests',
        },
        # Add more candidates...
    ]

    for candidate in candidates:
        print(f"Processing {candidate['name']}...")

        # Get file content
        content = get_file_content(BASE_COMMIT, candidate['file'])
        if not content:
            print(f"  Could not get content for {candidate['file']}")
            continue

        # Find test module in test_file
        test_content = get_file_content(BASE_COMMIT, candidate['test_file'])
        if not test_content:
            print(f"  Could not get test content for {candidate['test_file']}")
            continue

        # For now, let's manually create a simple but working bug
        # Based on the successful pattern

    return instances

def create_simple_parameter_removal_bug():
    """
    Create a simple bug that removes an optional parameter from a function.
    This follows the successful pr_12234 pattern.
    """

    # Define the bug: Remove a parameter from a function and update call sites
    # We'll use a simplified version for demonstration

    instance = {
        "instance_id": "juspay__hyperswitch.fece9bc3.manual_func_param_removal",
        "repo": "juspay/hyperswitch",
        "base_commit": BASE_COMMIT,
        "language": "rust",
        "version": BASE_COMMIT,
    }

    # For a proper implementation, we need to:
    # 1. Identify a function with a parameter that can be removed
    # 2. Create the bug patch that removes the parameter
    # 3. Update all call sites
    # 4. Create test patch that checks for the original signature
    # 5. Add tests to an EXISTING test module in a DIFFERENT file

    return instance

def main():
    """Generate bugs following the successful pattern."""
    print("=" * 70)
    print("GENERATING HYPERSWITCH BUGS")
    print("=" * 70)
    print(f"Repo: {REPO_PATH}")
    print(f"Base commit: {BASE_COMMIT}")
    print()

    # Check repo exists
    if not REPO_PATH.exists():
        print(f"ERROR: Repo not found at {REPO_PATH}")
        print("Please clone first:")
        print(f"  git clone https://github.com/juspay/hyperswitch.git {REPO_PATH}")
        return

    # Generate bugs
    instances = generate_function_signature_bug()

    # Save
    output_file = Path('logs/bug_gen/juspay__hyperswitch.fece9bc3/generated_bugs_proper.json')
    output_file.parent.mkdir(parents=True, exist_ok=True)

    with open(output_file, 'w') as f:
        json.dump(instances, f, indent=2)

    print(f"\nSaved {len(instances)} instances to {output_file}")

if __name__ == '__main__':
    main()
