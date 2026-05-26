#!/usr/bin/env python3
"""
Regenerate bug patches from GitHub PRs with correct context.

This script:
1. Reads the dataset with PR information
2. Fetches the actual PR diff from GitHub
3. Extracts the bug fix changes (excluding test files)
4. Ensures patches match file content at base_commit
5. Updates dataset with corrected patches
"""

import json
import os
import re
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from unidiff import PatchSet
import requests


def fetch_pr_diff_from_github(repo: str, pull_number: int) -> Optional[str]:
    """Fetch PR diff directly from GitHub API."""
    # Convert repo format from "owner__repo.hash" to "owner/repo"
    if '.' in repo:
        repo_clean = repo.rsplit('.', 1)[0].replace('__', '/')
    else:
        repo_clean = repo.replace('__', '/')

    url = f"https://github.com/{repo_clean}/pull/{pull_number}.diff"

    try:
        response = requests.get(url, timeout=60)
        if response.status_code == 200:
            return response.text
        else:
            print(f"  Failed to fetch PR diff: {response.status_code}")
            return None
    except Exception as e:
        print(f"  Error fetching PR diff: {e}")
        return None


def fetch_file_at_commit(repo: str, commit: str, file_path: str) -> Optional[str]:
    """Fetch file content at specific commit."""
    if '.' in repo:
        repo_clean = repo.rsplit('.', 1)[0].replace('__', '/')
    else:
        repo_clean = repo.replace('__', '/')

    url = f"https://raw.githubusercontent.com/{repo_clean}/{commit}/{file_path}"

    try:
        response = requests.get(url, timeout=30)
        if response.status_code == 200:
            return response.text
        else:
            return None
    except Exception as e:
        print(f"  Error fetching file: {e}")
        return None


def is_test_file(file_path: str) -> bool:
    """Check if file is a test file."""
    test_patterns = [
        r'/tests?/',
        r'_test\.rs$',
        r'\.test\.',
        r'#\[cfg\(test\)\]',
        r'#\[test\]'
    ]
    for pattern in test_patterns:
        if re.search(pattern, file_path):
            return True
    return False


def extract_bug_fix_changes(diff_text: str) -> Optional[str]:
    """
    Extract only the bug fix changes from a PR diff.
    Exclude test files and test-only changes.
    """
    if not diff_text:
        return None

    try:
        patch_set = PatchSet(diff_text)
        filtered_patches = []

        for patched_file in patch_set:
            file_path = patched_file.path

            # Skip test files
            if is_test_file(file_path):
                print(f"  Skipping test file: {file_path}")
                continue

            # Skip if file is deleted
            if patched_file.is_removed_file:
                continue

            # Reconstruct the diff for this file
            file_diff = f"diff --git a/{file_path} b/{file_path}\n"

            if patched_file.is_rename:
                file_diff += f"rename from {patched_file.source_file}\n"
                file_diff += f"rename to {patched_file.target_file}\n"
            else:
                if patched_file.source_file != patched_file.target_file:
                    file_diff += f"--- a/{patched_file.source_file}\n"
                    file_diff += f"+++ b/{patched_file.target_file}\n"
                else:
                    file_diff += f"--- a/{file_path}\n"
                    file_diff += f"+++ b/{file_path}\n"

            for hunk in patched_file:
                file_diff += f"@@ -{hunk.source_start},{hunk.source_length} +{hunk.target_start},{hunk.target_length} @@\n"
                for line in hunk:
                    if line.is_added:
                        file_diff += f"+{line.value}"
                    elif line.is_removed:
                        file_diff += f"-{line.value}"
                    else:
                        file_diff += f" {line.value}"

            filtered_patches.append(file_diff)

        if not filtered_patches:
            print("  No non-test changes found in PR")
            return None

        return '\n'.join(filtered_patches)

    except Exception as e:
        print(f"  Error parsing diff: {e}")
        return None


def verify_patch_applies(patch_text: str, repo: str, base_commit: str) -> Tuple[bool, str]:
    """
    Verify that a patch can apply to the file at base_commit.
    Returns (applies, error_message).
    """
    try:
        patch_set = PatchSet(patch_text)

        for patched_file in patch_set:
            if patched_file.is_removed_file or patched_file.is_rename:
                continue

            file_path = patched_file.path
            file_content = fetch_file_at_commit(repo, base_commit, file_path)

            if not file_content:
                return False, f"Could not fetch file: {file_path}"

            lines = file_content.split('\n')

            for hunk in patched_file:
                # Check context lines match
                expected_line = hunk.source_start
                for line in hunk:
                    if line.is_context:
                        # Line should exist in source
                        line_idx = expected_line - 1  # 0-indexed
                        if line_idx >= len(lines):
                            return False, f"Line {expected_line} out of range in {file_path}"

                        expected_content = line.value.rstrip('\n')
                        actual_content = lines[line_idx].rstrip('\n')

                        if expected_content != actual_content:
                            return False, f"Context mismatch at {file_path}:{expected_line}\nExpected: {expected_content[:60]}...\nActual: {actual_content[:60]}..."

                        expected_line += 1
                    elif line.is_removed:
                        expected_line += 1

        return True, "Patch context matches"

    except Exception as e:
        return False, f"Verification error: {e}"


def regenerate_patch_for_instance(instance: dict) -> Optional[str]:
    """Regenerate bug patch for a single instance."""
    instance_id = instance.get('instance_id', '')
    repo = instance.get('repo', '')
    pull_number = instance.get('pull_number')
    base_commit = instance.get('base_commit', '')

    if not pull_number:
        print(f"  No pull_number found")
        return None

    print(f"  Fetching PR #{pull_number} diff...")
    pr_diff = fetch_pr_diff_from_github(repo, pull_number)

    if not pr_diff:
        print(f"  Failed to fetch PR diff")
        return None

    print(f"  Extracting bug fix changes...")
    bug_patch = extract_bug_fix_changes(pr_diff)

    if not bug_patch:
        print(f"  No bug fix changes found")
        return None

    print(f"  Verifying patch applies to base_commit...")
    applies, message = verify_patch_applies(bug_patch, repo, base_commit)

    if applies:
        print(f"  ✓ Patch verified - context matches")
        return bug_patch
    else:
        print(f"  ✗ Patch verification failed: {message}")
        return None


def main():
    """Regenerate bug patches for all instances."""

    dataset_path = Path('logs/bug_gen/juspay__hyperswitch.fece9bc3/pr_mirror/recovered_dataset_with_test_patches.json')
    output_path = Path('logs/bug_gen/juspay__hyperswitch.fece9bc3/pr_mirror/recovered_dataset_regenerated.json')

    print(f"Loading dataset: {dataset_path}")
    with open(dataset_path) as f:
        data = json.load(f)

    print(f"Regenerating bug patches for {len(data)} instances...\n")

    success_count = 0
    fail_count = 0
    skip_count = 0

    for i, inst in enumerate(data):
        instance_id = inst.get('instance_id', '')

        print(f"[{i+1}/{len(data)}] {instance_id}")

        # Check if already has a verified patch
        if inst.get('_patch_verified'):
            print(f"  Skipping (already verified)")
            skip_count += 1
            continue

        try:
            new_patch = regenerate_patch_for_instance(inst)
            if new_patch:
                inst['patch'] = new_patch
                inst['_patch_regenerated'] = True
                inst['_patch_verified'] = True
                print(f"  ✓ Bug patch regenerated successfully")
                success_count += 1
            else:
                print(f"  ✗ Failed to regenerate")
                fail_count += 1
        except Exception as e:
            print(f"  ✗ Error: {e}")
            fail_count += 1

        # Rate limiting - be nice to GitHub
        time.sleep(1)

        # Save progress every 10
        if (i + 1) % 10 == 0:
            with open(output_path, 'w') as f:
                json.dump(data, f, indent=2)
            print(f"  (Progress saved)\n")

    # Final save
    with open(output_path, 'w') as f:
        json.dump(data, f, indent=2)

    print(f"\n{'='*60}")
    print(f"Results:")
    print(f"  Success:  {success_count}")
    print(f"  Failed:   {fail_count}")
    print(f"  Skipped:  {skip_count}")
    print(f"  Total:    {len(data)}")
    print(f"\nSaved to: {output_path}")
    print(f"\nNext step: Run validation with regenerated patches")


if __name__ == '__main__':
    main()
