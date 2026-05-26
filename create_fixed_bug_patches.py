#!/usr/bin/env python3
"""
Manually fix bug patches to ensure they compile while still having wrong behavior.

Strategy:
1. For pr_10814: Add wildcard arm to match statement instead of removing arms
2. For others: Modify patches to not break compilation by keeping shared types
"""

import json
import re
from pathlib import Path


def fix_pr_10814_bug_patch():
    """
    Fix pr_10814 bug patch to compile.

    Original bug: Removes match arms for BankTransferData variants
    Problem: New variants added in base commit cause non-exhaustive match
    Fix: Change the match arms to have wrong behavior instead of removing them
    """
    # Read the original bug patch
    patch_path = Path('logs/bug_gen/juspay__hyperswitch.fece9bc3/pr_mirror/juspay__hyperswitch.fece9bc3.pr_10814/bug__pr_10814.diff')
    if not patch_path.exists():
        return None

    with open(patch_path) as f:
        original = f.read()

    # The fix: Instead of removing match arms, change them to wrong logic
    # This maintains compilation but introduces behavioral bugs
    # For now, return the original and let the user know the issue
    return original


def create_minimal_compilable_bug(file_path: str, bug_type: str) -> str:
    """
    Create a minimal bug that compiles but has wrong behavior.

    Types of bugs:
    - off_by_one: Change < to <= or vice versa
    - wrong_default: Change default value
    - swapped_args: Swap function arguments
    - wrong_operator: Change + to -, * to /, etc.
    """
    # This would require parsing the actual code and making targeted changes
    # For now, this is a placeholder
    pass


def main():
    """Create fixed bug patches for instances."""

    print("=" * 70)
    print("CREATING FIXED BUG PATCHES")
    print("=" * 70)

    print("""
ANALYSIS:
---------
The PR Mirror generated bugs that BREAK COMPILATION:
- Remove enum variants that other files import
- Remove match arms that new code requires
- Remove struct fields that other code initializes

FIX STRATEGY:
-------------
Instead of REMOVING code (breaks compilation),
we need to CHANGE code to have WRONG BEHAVIOR:

Examples:
1. Change logic:  if x > 0  →  if x >= 0  (off-by-one)
2. Change default: Some(val) → None
3. Swap operations: a + b → a - b
4. Reverse conditions: if valid → if !valid
5. Wrong return: Ok(result) → Err(error)

CURRENT STATUS:
---------------
The bug patches in the dataset cannot be easily fixed automatically.
They require manual code analysis to create semantically wrong but
syntactically correct bugs.

RECOMMENDATION:
---------------
1. Select instances where the bug is in LOCAL code (not shared types)
2. Manually rewrite bug patches to change behavior not syntax
3. Or use synthetic bugs that are known to compile and be detectable
    """)

    # Let me check if there are any instances that might work
    target_instances = [
        'juspay__hyperswitch.fece9bc3.pr_10150',
        'juspay__hyperswitch.fece9bc3.pr_10814',
        'juspay__hyperswitch.fece9bc3.pr_10924',
        'juspay__hyperswitch.fece9bc3.pr_10937',
        'juspay__hyperswitch.fece9bc3.pr_10947',
        'juspay__hyperswitch.fece9bc3.pr_10952',
        'juspay__hyperswitch.fece9bc3.pr_10961',
        'juspay__hyperswitch.fece9bc3.pr_10972',
        'juspay__hyperswitch.fece9bc3.pr_10992',
        'juspay__hyperswitch.fece9bc3.pr_11022',
    ]

    print("\nChecking which instances can potentially be fixed:\n")

    for instance_id in target_instances:
        pr_num = instance_id.split('.pr_')[-1]
        patch_path = Path(f'logs/bug_gen/juspay__hyperswitch.fece9bc3/pr_mirror/juspay__hyperswitch.fece9bc3.pr_{pr_num}/bug__pr_{pr_num}.diff')

        if patch_path.exists():
            with open(patch_path) as f:
                patch = f.read()

            # Count what the patch removes
            removed_lines = [l for l in patch.split('\n') if l.startswith('-') and not l.startswith('---')]
            added_lines = [l for l in patch.split('\n') if l.startswith('+') and not l.startswith('+++')]

            # Check if it removes enum variants or struct fields
            removes_enum = any('pub enum' in l for l in removed_lines)
            removes_field = any(':' in l and l.strip().endswith(',') for l in removed_lines)
            removes_match = any('=>' in l for l in removed_lines)

            fixable = "MAYBE" if not (removes_enum or removes_field) else "HARD"

            print(f"{instance_id}:")
            print(f"  Removed lines: {len(removed_lines)}, Added: {len(added_lines)}")
            print(f"  Removes enum: {removes_enum}, fields: {removes_field}, match arms: {removes_match}")
            print(f"  Fix difficulty: {fixable}")
            print()


if __name__ == '__main__':
    main()
