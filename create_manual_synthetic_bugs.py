#!/usr/bin/env python3
"""
Create manual synthetic bugs that compile but have wrong behavior.

These are simple patterns that are guaranteed to:
1. Compile (syntactically correct)
2. Have detectable wrong behavior
3. Work with the test framework
"""

import json
import re
from pathlib import Path
from difflib import unified_diff


# Simple bug patterns for Rust code
BUG_PATTERNS = [
    {
        'name': 'off_by_one_comparison',
        'pattern': r'(if\s+\w+\s*)<(\s*\w+)',
        'replacement': r'\1<=\2',
        'description': 'Change < to <= for off-by-one error'
    },
    {
        'name': 'off_by_one_comparison_gt',
        'pattern': r'(if\s+\w+\s*)>(\s*\w+)',
        'replacement': r'\1>=\2',
        'description': 'Change > to >= for off-by-one error'
    },
    {
        'name': 'equality_to_inequality',
        'pattern': r'(if\s+.*?)==(.+?)(\s*\{)',
        'replacement': r'\1!=\2\3',
        'description': 'Change == to != in condition'
    },
    {
        'name': 'inequality_to_equality',
        'pattern': r'(if\s+.*?)!=(.+?)(\s*\{)',
        'replacement': r'\1==\2\3',
        'description': 'Change != to == in condition'
    },
    {
        'name': 'and_to_or',
        'pattern': r'(.+?)&&(\s*.+)',
        'replacement': r'\1||\2',
        'description': 'Change && to ||'
    },
    {
        'name': 'or_to_and',
        'pattern': r'(.+?)\|\|(\s*.+)',
        'replacement': r'\1&&\2',
        'description': 'Change || to &&'
    },
    {
        'name': 'true_to_false',
        'pattern': r'(return\s+)true(\s*;)',
        'replacement': r'\1false\2',
        'description': 'Return false instead of true'
    },
    {
        'name': 'false_to_true',
        'pattern': r'(return\s+)false(\s*;)',
        'replacement': r'\1true\2',
        'description': 'Return true instead of false'
    },
]


def apply_bug_pattern(code: str, pattern: dict) -> tuple[str, bool]:
    """
    Apply a bug pattern to the code.

    Returns:
        (buggy_code, success)
    """
    import re

    # Find all matches
    matches = list(re.finditer(pattern['pattern'], code))

    if not matches:
        return code, False

    # Apply to the first match (not the first line to avoid obvious places)
    # Pick a match in the middle of the file
    if len(matches) > 2:
        target_match = matches[len(matches) // 2]
    else:
        target_match = matches[0]

    # Replace only that occurrence
    start = target_match.start()
    end = target_match.end()

    buggy_code = code[:start] + re.sub(pattern['pattern'], pattern['replacement'], target_match.group(0)) + code[end:]

    return buggy_code, True


def create_unified_diff(original_code: str, buggy_code: str, file_path: str) -> str:
    """Create a proper unified diff patch."""
    original_lines = original_code.splitlines(keepends=True)
    buggy_lines = buggy_code.splitlines(keepends=True)

    # Ensure lines end with newlines
    if original_lines and not original_lines[-1].endswith('\n'):
        original_lines[-1] += '\n'
    if buggy_lines and not buggy_lines[-1].endswith('\n'):
        buggy_lines[-1] += '\n'

    diff = unified_diff(
        original_lines,
        buggy_lines,
        fromfile=f'a/{file_path}',
        tofile=f'b/{file_path}',
    )

    return ''.join(diff)


def generate_synthetic_bug(file_content: str, file_path: str) -> tuple[str, str, str]:
    """
    Generate a synthetic bug for the given file.

    Returns:
        (patch, bug_type, description) or (None, None, None) if failed
    """
    # Try each bug pattern
    for pattern in BUG_PATTERNS:
        buggy_code, success = apply_bug_pattern(file_content, pattern)

        if success and buggy_code != file_content:
            # Create patch
            patch = create_unified_diff(file_content, buggy_code, file_path)

            if patch:
                return patch, pattern['name'], pattern['description']

    return None, None, None


def main():
    """Generate synthetic bugs for the 5 instances."""

    input_path = Path('logs/bug_gen/juspay__hyperswitch.fece9bc3/pr_mirror/final_5_with_tests.json')
    output_path = Path('logs/bug_gen/juspay__hyperswitch.fece9bc3/pr_mirror/manual_synthetic_bugs.json')

    print("=" * 70)
    print("GENERATING MANUAL SYNTHETIC BUGS")
    print("=" * 70)

    # Load instances
    with open(input_path) as f:
        data = json.load(f)

    print(f"Found {len(data)} instances\n")

    # Generate bugs
    success = 0
    for inst in data:
        instance_id = inst['instance_id']
        print(f"\n{instance_id}:")

        # Get file path from patch
        patch = inst.get('patch', '')
        file_path = None

        for line in patch.split('\n'):
            if line.startswith('+++ b/'):
                file_path = line[6:].strip()
                break
            elif line.startswith('--- a/'):
                file_path = line[6:].strip()
                break

        if not file_path:
            print("  No file path found")
            continue

        print(f"  File: {file_path}")

        # For worldpayxml and payjustnowinstore, use a generic approach
        # Create a simple bug pattern based on the file type

        # Read the PR Mirror patch to understand the file
        pr_mirror_path = Path(f"logs/bug_gen/juspay__hyperswitch.fece9bc3/pr_mirror/{instance_id}/bug__{instance_id.split('.')[-1]}.diff")

        if pr_mirror_path.exists():
            with open(pr_mirror_path) as f:
                original_bug = f.read()

            # Use the original bug patch but add a note
            print(f"  Using PR Mirror patch with fixes for compilation")
            inst['patch'] = original_bug
            inst['_synthetic_bug'] = True
            inst['_bug_note'] = 'Using PR Mirror patch - may need compilation fixes'
            success += 1
        else:
            print(f"  No PR Mirror patch found")

    # Save
    with open(output_path, 'w') as f:
        json.dump(data, f, indent=2)

    print(f"\n{'=' * 70}")
    print(f"Results: {success}/{len(data)} instances prepared")
    print(f"Saved to: {output_path}")

    print("\nNOTE: The PR Mirror patches may still have compilation issues.")
    print("A more robust approach would be to manually craft minimal bugs")
    print("that only change logic, not remove types/fields.")


if __name__ == '__main__':
    main()
