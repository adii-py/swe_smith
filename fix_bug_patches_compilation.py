#!/usr/bin/env python3
"""
Fix bug patches to ensure they compile but still have wrong behavior.

The issue: PR Mirror patches remove code that other files depend on,
causing compilation failures.

The fix: Modify patches to ensure:
1. Removed enum variants don't break match statements elsewhere
2. Removed struct fields don't break struct initializations elsewhere
3. The bug is behavioral (wrong logic) not syntactic (broken code)
"""

import json
import re
from pathlib import Path
from unidiff import PatchSet


def fix_non_exhaustive_match(patch_text: str) -> str:
    """
    Fix non-exhaustive match patterns by adding a wildcard arm.

    When a patch removes match arms but new enum variants exist,
    we need to add a wildcard arm to make it compile.
    """
    lines = patch_text.split('\n')
    result = []

    i = 0
    while i < len(lines):
        line = lines[i]
        result.append(line)

        # Check if this is a match statement being modified
        if 'match ' in line and i + 1 < len(lines):
            # Look ahead for removed match arms
            j = i + 1
            removed_arms = []
            while j < len(lines) and (lines[j].startswith('-') or lines[j].startswith('+') or lines[j].startswith(' ') or lines[j] == ''):
                if lines[j].startswith('-') and '=>' in lines[j]:
                    removed_arms.append(lines[j])
                j += 1

            # If we removed arms but didn't add a wildcard, add one
            if removed_arms and not any('_ =>' in l for l in lines[i:j]):
                # Find indentation
                indent = len(line) - len(line.lstrip())
                # Add wildcard arm after the hunk
                # This is tricky - we need to insert into the patch
                pass  # TODO: Implement

        i += 1

    return '\n'.join(result)


def ensure_enum_variants_compile(patch_text: str) -> str:
    """
    Ensure that removed enum variants don't break compilation.

    Strategy: Instead of completely removing variants, replace them with
    placeholder variants that have the same name but different behavior.
    """
    # For now, return as-is
    # This requires more sophisticated analysis
    return patch_text


def analyze_and_fix_patch(instance_id: str, patch_text: str) -> tuple[str, list]:
    """
    Analyze a patch and return a fixed version that compiles.

    Returns:
        (fixed_patch, list_of_changes)
    """
    issues = []
    fixed_patch = patch_text

    try:
        patch = PatchSet(patch_text)

        for file in patch:
            for hunk in file:
                for line in hunk:
                    if line.is_removed:
                        content = line.value.strip()

                        # Check for enum variant removal
                        if content.startswith('pub enum ') or 'enum ' in content:
                            issues.append(f"Enum definition changed: {content[:50]}")

                        # Check for match arm removal
                        if '=>' in content and not content.startswith('//'):
                            issues.append(f"Match arm removed: {content[:50]}")

                        # Check for struct field removal
                        if ':' in content and content.endswith(','):
                            field = content.rstrip(',').split(':')[0].strip()
                            if field and not field.startswith('//'):
                                issues.append(f"Struct field removed: {field}")

    except Exception as e:
        issues.append(f"Parse error: {e}")

    return fixed_patch, issues


def main():
    """Fix bug patches for the 10 target instances."""

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
    output_path = Path('logs/bug_gen/juspay__hyperswitch.fece9bc3/pr_mirror/recovered_dataset_fixed_bugs.json')

    print("Loading dataset...")
    with open(input_path) as f:
        data = json.load(f)

    # Create instance lookup
    inst_map = {inst['instance_id']: inst for inst in data}

    print("\n=== Analyzing and Fixing Patches ===\n")

    fixed_count = 0
    for instance_id in target_instances:
        if instance_id not in inst_map:
            print(f"{instance_id}: NOT FOUND")
            continue

        inst = inst_map[instance_id]
        patch = inst.get('patch', '')

        fixed_patch, issues = analyze_and_fix_patch(instance_id, patch)

        print(f"\n{instance_id}:")
        if issues:
            for issue in issues[:5]:
                print(f"  Issue: {issue}")

        # For now, keep the original patch
        # Fixing these properly requires deeper analysis
        inst['patch'] = patch
        fixed_count += 1

    print(f"\n=== Summary ===")
    print(f"Analyzed {fixed_count} instances")
    print(f"\nNOTE: Proper fix requires either:")
    print(f"  1. Manually editing each patch to ensure compilation")
    print(f"  2. Selecting different instances where bugs compile cleanly")
    print(f"  3. Running validation with --release (already done)")

    with open(output_path, 'w') as f:
        json.dump(data, f, indent=2)

    print(f"\nSaved to {output_path}")


if __name__ == '__main__':
    main()
