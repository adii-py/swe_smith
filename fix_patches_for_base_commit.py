#!/usr/bin/env python3
"""
Fix bug patches to match the base_commit (fece9bc3) content.

The issue: PR patches were fetched from GitHub, but they are based on the PR's base commit,
not the fece9bc3 commit used in the Docker image. The files have diverged.

Solution: For each PR, we need to:
1. Get the file content at fece9bc3 (base)
2. Get the file content at (fece9bc3 + PR changes)
3. Generate a proper diff

Since we can't easily get "fece9bc3 + PR changes", we'll use a different approach:
- Fetch the diff between fece9bc3 and the merge commit
- This gives us the changes that would need to be applied on top of fece9bc3
"""

import json
import re
import requests
from pathlib import Path
from unidiff import PatchSet


def get_file_at_commit(repo: str, commit: str, file_path: str) -> str:
    """Fetch file content from GitHub at a specific commit."""
    url = f"https://raw.githubusercontent.com/{repo}/{commit}/{file_path}"
    try:
        resp = requests.get(url, timeout=30)
        if resp.status_code == 200:
            return resp.text
    except Exception as e:
        print(f"  Error fetching file: {e}")
    return None


def get_pr_files(repo: str, pr_number: int) -> list:
    """Get list of files changed in a PR."""
    url = f"https://api.github.com/repos/{repo}/pulls/{pr_number}/files"
    try:
        resp = requests.get(url, timeout=30)
        if resp.status_code == 200:
            return resp.json()
    except Exception as e:
        print(f"  Error fetching PR files: {e}")
    return []


def generate_diff(old_content: str, new_content: str, file_path: str) -> str:
    """Generate a unified diff between two file contents."""
    import difflib

    old_lines = old_content.splitlines(keepends=True)
    new_lines = new_content.splitlines(keepends=True)

    # Ensure lines end with newline for proper diff
    if old_lines and not old_lines[-1].endswith('\n'):
        old_lines[-1] += '\n'
    if new_lines and not new_lines[-1].endswith('\n'):
        new_lines[-1] += '\n'

    diff = difflib.unified_diff(
        old_lines,
        new_lines,
        fromfile=f'a/{file_path}',
        tofile=f'b/{file_path}',
    )

    return ''.join(diff)


def process_instance(inst: dict, repo: str = 'juspay/hyperswitch') -> str:
    """Process a single instance and generate correct patch."""
    instance_id = inst.get('instance_id', '')
    pr_match = re.search(r'\.pr_(\d+)$', instance_id)
    if not pr_match:
        return None

    pr_number = int(pr_match.group(1))
    base_commit = inst.get('base_commit', 'fece9bc3')

    print(f"Processing {instance_id} (PR #{pr_number})...")

    # Get PR files
    files = get_pr_files(repo, pr_number)
    if not files:
        print(f"  No files found for PR #{pr_number}")
        return None

    # Find the main file (not test file)
    main_file = None
    for f in files:
        filename = f.get('filename', '')
        if filename.startswith('crates/') and not 'test' in filename.lower():
            main_file = f
            break

    if not main_file:
        print(f"  No suitable main file found")
        return None

    file_path = main_file['filename']
    print(f"  File: {file_path}")

    # Get file content at base_commit
    old_content = get_file_at_commit(repo, base_commit, file_path)
    if not old_content:
        print(f"  Could not fetch file at {base_commit}")
        return None

    # Get the patch from GitHub for this file
    github_patch = main_file.get('patch', '')
    if not github_patch:
        print(f"  No patch available from GitHub")
        return None

    # Apply the patch to old_content to get new_content
    # This is tricky - we'd need to actually apply the patch
    # For now, let's try to fetch from the PR head commit
    pr_head = main_file.get('blob_url', '').split('/')[-1][:40]  # Extract sha from blob_url

    # Alternative: fetch from merge commit
    # Let's try fetching the file from the merge commit if available
    # Actually, let's just try the raw patch approach

    # Since we can't easily reconstruct the new content, let's just return the
    # GitHub patch but we'll need to be aware it might not apply cleanly
    print(f"  Using GitHub patch (may not match base_commit)")
    return main_file.get('patch', '')


def main():
    """Fix patches for all instances."""
    input_path = Path('logs/bug_gen/juspay__hyperswitch.fece9bc3/pr_mirror/recovered_dataset_correct_base.json')
    output_path = Path('logs/bug_gen/juspay__hyperswitch.fece9bc3/pr_mirror/recovered_dataset_attempted_fix.json')

    print(f"Loading: {input_path}")
    with open(input_path) as f:
        data = json.load(f)

    # Process only the 10 specific instances
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

    success = 0

    for inst in data:
        if inst['instance_id'] in target_instances:
            # Try to process this instance
            # For now, just keep the existing patch since the proper fix
            # requires complex git operations
            print(f"Skipping complex patch fix for {inst['instance_id']}")
            print(f"  (Would need to reconstruct file at base_commit + PR changes)")

    with open(output_path, 'w') as f:
        json.dump(data, f, indent=2)

    print(f"\nNote: Proper patch fixing requires cloning the repo and using git operations")
    print(f"The fundamental issue is that PR patches don't match the fece9bc3 base commit")


if __name__ == '__main__':
    main()
