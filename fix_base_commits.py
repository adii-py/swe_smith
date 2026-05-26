#!/usr/bin/env python3
"""
Fix base commits by fetching actual PR base SHA from GitHub API.

This script:
1. Reads the dataset with PR numbers
2. Fetches PR info from GitHub API to get actual base SHA
3. Updates base_commit for each instance
4. Regenerates bug patches from PR diffs (which will now match)
"""

import json
import time
from pathlib import Path
import requests


def get_pr_info(repo: str, pull_number: int) -> dict:
    """Fetch PR information from GitHub API."""
    # Convert repo format
    if '.' in repo:
        repo_clean = repo.rsplit('.', 1)[0].replace('__', '/')
    else:
        repo_clean = repo.replace('__', '/')

    url = f"https://api.github.com/repos/{repo_clean}/pulls/{pull_number}"

    try:
        response = requests.get(url, timeout=30)
        if response.status_code == 200:
            return response.json()
        else:
            print(f"  Failed to fetch PR info: {response.status_code}")
            return None
    except Exception as e:
        print(f"  Error: {e}")
        return None


def fetch_pr_diff(repo: str, pull_number: int) -> str:
    """Fetch PR diff from GitHub."""
    if '.' in repo:
        repo_clean = repo.rsplit('.', 1)[0].replace('__', '/')
    else:
        repo_clean = repo.replace('__', '/')

    url = f"https://github.com/{repo_clean}/pull/{pull_number}.diff"

    try:
        response = requests.get(url, timeout=60)
        if response.status_code == 200:
            return response.text
        return None
    except:
        return None


def main():
    """Fix base commits and regenerate patches."""

    input_path = Path('logs/bug_gen/juspay__hyperswitch.fece9bc3/pr_mirror/recovered_dataset_final.json')
    output_path = Path('logs/bug_gen/juspay__hyperswitch.fece9bc3/pr_mirror/recovered_dataset_correct_base.json')

    print(f"Loading: {input_path}")
    with open(input_path) as f:
        data = json.load(f)

    print(f"Processing {len(data)} instances...\n")

    updated = 0
    failed = 0

    for i, inst in enumerate(data):
        instance_id = inst.get('instance_id', '')
        repo = inst.get('repo', '')
        pull_number = inst.get('pull_number')
        current_base = inst.get('base_commit', '')

        print(f"[{i+1}/{len(data)}] {instance_id}")
        print(f"  Current base: {current_base[:12]}...")

        if not pull_number:
            print(f"  No PR number, skipping")
            failed += 1
            continue

        # Fetch PR info
        print(f"  Fetching PR #{pull_number} info...")
        pr_info = get_pr_info(repo, pull_number)

        if not pr_info:
            print(f"  Failed to fetch PR info")
            failed += 1
            continue

        # Get base SHA
        base_sha = pr_info.get('base', {}).get('sha', '')
        head_sha = pr_info.get('head', {}).get('sha', '')

        if not base_sha:
            print(f"  No base SHA found")
            failed += 1
            continue

        print(f"  PR base SHA: {base_sha[:12]}...")
        print(f"  PR head SHA: {head_sha[:12]}...")

        # Update base_commit
        if base_sha != current_base:
            print(f"  Updating base_commit: {current_base[:12]}... -> {base_sha[:12]}...")
            inst['base_commit'] = base_sha
            inst['_base_updated'] = True

        # Also fetch fresh PR diff with correct base
        print(f"  Fetching PR diff...")
        pr_diff = fetch_pr_diff(repo, pull_number)
        if pr_diff:
            inst['patch'] = pr_diff
            inst['_patch_updated'] = True
            print(f"  ✓ Updated patch ({len(pr_diff)} chars)")
            updated += 1
        else:
            print(f"  ✗ Failed to fetch diff")
            failed += 1

        # Rate limiting
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
    print(f"Updated: {updated}")
    print(f"Failed: {failed}")
    print(f"Saved to: {output_path}")
    print(f"\nNext: Run validation with correct base commits")


if __name__ == '__main__':
    main()
