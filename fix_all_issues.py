#!/usr/bin/env python3
"""
Comprehensive fix for all validation issues to achieve f2p > 0.

Issues to fix:
1. Compilation errors - bug patches break imports/match statements
2. Linker errors - use --release mode
3. Context mismatches - fix test patch line numbers
"""

import json
import re
import requests
from pathlib import Path
from unidiff import PatchSet


def get_file_at_commit(repo: str, commit: str, file_path: str) -> str:
    """Fetch file content from GitHub."""
    url = f"https://raw.githubusercontent.com/{repo}/{commit}/{file_path}"
    try:
        resp = requests.get(url, timeout=30)
        if resp.status_code == 200:
            return resp.text
    except Exception as e:
        print(f"  Error fetching file: {e}")
    return None


def fix_bug_patch_for_compilation(patch_text: str, file_path: str) -> str:
    """
    Fix bug patches to ensure they compile.

    Common issues:
    1. Removed enum variants that are imported elsewhere
    2. Non-exhaustive match patterns
    3. Missing struct fields that are referenced
    """
    # For now, return as-is. We'll need to manually inspect each patch
    return patch_text


def create_proper_test_patch(file_path: str, test_code: str, file_content: str) -> str:
    """Create a test patch that appends to the end of the file."""
    test_lines = test_code.strip().split('\n')

    if not file_content:
        # Fallback: create simple patch
        diff = [
            f'diff --git a/{file_path} b/{file_path}',
            f'--- a/{file_path}',
            f'+++ b/{file_path}',
            f'@@ -1,0 +1,{len(test_lines)} @@',
        ]
        for line in test_lines:
            diff.append('+' + line)
        return '\n'.join(diff) + '\n'

    lines = file_content.split('\n')
    total_lines = len(lines)

    # Find last non-empty line
    last_idx = total_lines - 1
    while last_idx >= 0 and not lines[last_idx].strip():
        last_idx -= 1

    if last_idx < 0:
        last_idx = 0

    # Create patch with proper line numbers
    diff = [
        f'diff --git a/{file_path} b/{file_path}',
        f'--- a/{file_path}',
        f'+++ b/{file_path}',
        f'@@ -{last_idx + 1},0 +{last_idx + 1},{len(test_lines)} @@',
    ]
    for line in test_lines:
        diff.append('+' + line)

    return '\n'.join(diff) + '\n'


def analyze_patch_compilation_issues(patch_text: str, file_path: str) -> list:
    """Analyze what compilation issues a patch might cause."""
    issues = []

    try:
        patch = PatchSet(patch_text)
        for file in patch:
            for hunk in file:
                for line in hunk:
                    if line.is_removed:
                        removed = line.value.strip()
                        # Check for enum variant removal
                        if removed.startswith('pub enum ') or 'enum ' in removed:
                            issues.append(f"Enum change: {removed[:50]}")
                        # Check for struct field removal
                        if removed.strip().endswith(',') and ':' in removed:
                            field = removed.strip().rstrip(',').split(':')[0].strip()
                            if field:
                                issues.append(f"Field removed: {field}")
    except Exception as e:
        issues.append(f"Parse error: {e}")

    return issues


def main():
    """Fix all issues for the 10 instances."""

    # Target instances
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

    input_path = Path('logs/bug_gen/juspay__hyperswitch.fece9bc3/pr_mirror/recovered_dataset_correct_base.json')
    output_path = Path('logs/bug_gen/juspay__hyperswitch.fece9bc3/pr_mirror/recovered_dataset_fixed_all.json')

    print("Loading dataset...")
    with open(input_path) as f:
        data = json.load(f)

    # Create instance lookup
    inst_map = {inst['instance_id']: inst for inst in data}

    print("\n=== Analyzing Issues ===\n")

    for instance_id in target_instances:
        if instance_id not in inst_map:
            print(f"{instance_id}: NOT FOUND in dataset")
            continue

        inst = inst_map[instance_id]
        patch = inst.get('patch', '')

        print(f"\n{instance_id}:")

        # Analyze patch for issues
        issues = analyze_patch_compilation_issues(patch, '')
        for issue in issues[:3]:  # Show first 3 issues
            print(f"  - {issue}")

    print("\n" + "="*60)
    print("SUMMARY OF FIXES NEEDED:")
    print("="*60)
    print("""
1. 6 instances have CustomerDeleteResponse import errors
   - The bug patches remove customer types that other files import
   - FIX: Need to keep those types or update importing files

2. 1 instance has non-exhaustive match pattern
   - pr_10814: removed match arms for BankTransferData
   - FIX: Add wildcard arm or keep the arms

3. 1 instance has linker error
   - pr_10924: ARM64 relocation error
   - FIX: Use --release mode or adjust linker settings

4. 2 instances timed out
   - pr_10952, pr_10937: patch context issues
   - FIX: Regenerate patches with correct context
    """)

    print("\nTo properly fix these, I need to:")
    print("1. Inspect each PR Mirror patch and understand what it changes")
    print("2. Either fix the patches or select different instances")
    print("3. Modify validation to use --release mode")


if __name__ == '__main__':
    main()
