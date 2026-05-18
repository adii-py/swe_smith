#!/usr/bin/env python3
"""
Fetch complex PRs with multi-file/cross-file dependencies for Hyperswitch.
Filters for PRs with:
- Multiple files changed (>= 3)
- Cross-crate dependencies (changes in multiple crates/)
- Recent merged PRs
"""

import requests
import json
from pathlib import Path

REPO = "juspay/hyperswitch"
COMMIT = "fece9bc38b9890a1a40912ce2a95037842362e27"
OUTPUT_FILE = "logs/bug_gen/juspay__hyperswitch.fece9bc3/pr_mirror/complex_prs.jsonl"

def fetch_complex_prs(min_files=2, max_files=8, max_prs=10):
    """Fetch PRs with multiple file changes."""

    print(f"Fetching complex PRs from {REPO}...")
    print(f"Criteria: >= {min_files} files changed")

    # Get list of merged PRs
    url = f"https://api.github.com/repos/{REPO}/pulls"
    params = {
        "state": "closed",
        "sort": "updated",
        "direction": "desc",
        "per_page": 100
    }

    headers = {}
    # Use GitHub token if available
    import os
    github_token = os.getenv("GITHUB_TOKEN")
    if github_token:
        headers["Authorization"] = f"token {github_token}"

    response = requests.get(url, params=params, headers=headers)
    prs = response.json()

    complex_prs = []

    for pr in prs:
        if not pr.get("merged_at"):
            continue

        pr_number = pr["number"]

        # Get PR details including files changed
        files_url = f"https://api.github.com/repos/{REPO}/pulls/{pr_number}/files"
        files_response = requests.get(files_url, headers=headers)

        if files_response.status_code != 200:
            continue

        files = files_response.json()
        num_files = len(files)

        # Check for cross-crate changes
        crates_changed = set()
        for f in files:
            path = f.get("filename", "")
            if path.startswith("crates/"):
                crate = path.split("/")[1]
                crates_changed.add(crate)

        # Filter criteria: 2-8 files changed, at least 1 crate
        if min_files <= num_files <= max_files and len(crates_changed) >= 1:
            # Get the actual diff
            diff_url = f"https://github.com/{REPO}/pull/{pr_number}.diff"
            diff_response = requests.get(diff_url, headers=headers)
            patch_content = diff_response.text if diff_response.status_code == 200 else ""

            complex_prs.append({
                "instance_id": f"juspay__hyperswitch.fece9bc3.pr_{pr_number}",
                "repo": REPO,
                "pull_number": pr_number,
                "title": pr["title"],
                "body": pr.get("body", ""),
                "num_files": num_files,
                "crates_changed": list(crates_changed),
                "cross_crate": len(crates_changed) > 1,
                "patch_url": diff_url,
                "patch": patch_content,
                "base_commit": COMMIT,
                "merge_commit_sha": pr.get("merge_commit_sha", ""),
            })

            print(f"  PR #{pr_number}: {num_files} files, {len(crates_changed)} crates - {pr['title'][:60]}")

            if len(complex_prs) >= max_prs:
                break

    # Save results
    Path(OUTPUT_FILE).parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_FILE, "w") as f:
        for pr in complex_prs:
            f.write(json.dumps(pr) + "\n")

    print(f"\nSaved {len(complex_prs)} complex PRs to {OUTPUT_FILE}")
    return complex_prs


if __name__ == "__main__":
    fetch_complex_prs(min_files=3, max_prs=10)
