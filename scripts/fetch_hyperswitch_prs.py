#!/usr/bin/env python3
"""Fetch merged PRs from Hyperswitch with detailed file counts."""
import json
import os
import sys
from urllib.request import Request, urlopen
from urllib.error import HTTPError

TOKEN = os.environ.get("GITHUB_TOKEN", "").strip()
OWNER = "juspay"
REPO = "hyperswitch"
MIN_FILES = 2
MAX_FILES = 8
MAX_PULLS = 100


def api_call(endpoint):
    url = f"https://api.github.com{endpoint}"
    headers = {"Accept": "application/vnd.github.v3+json"}
    if TOKEN:
        headers["Authorization"] = f"Bearer {TOKEN}"
    req = Request(url, headers=headers)
    try:
        with urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except HTTPError as e:
        print(f"HTTP Error {e.code}: {e.reason} for {url}")
        return None
    except Exception as e:
        print(f"Error: {e} for {url}")
        return None


def get_merged_prs(page=1, per_page=100):
    """List merged PRs."""
    return api_call(
        f"/repos/{OWNER}/{REPO}/pulls?state=closed&sort=updated&direction=desc&per_page={per_page}&page={page}"
    ) or []


def get_pr_detail(number):
    """Get detailed PR info including changed_files."""
    return api_call(f"/repos/{OWNER}/{REPO}/pulls/{number}")


def main():
    if not TOKEN:
        print("ERROR: Set GITHUB_TOKEN environment variable")
        sys.exit(1)

    print(f"Fetching merged PRs from {OWNER}/{REPO}...")

    candidates = []
    page = 1
    while len(candidates) < MAX_PULLS:
        prs = get_merged_prs(page=page)
        if not prs:
            break

        for pr in prs:
            if not pr.get("merged_at"):
                continue
            number = pr["number"]
            title = pr.get("title", "")

            # Get detailed PR info for file count
            detail = get_pr_detail(number)
            if not detail:
                continue

            files = detail.get("changed_files", 0)
            additions = detail.get("additions", 0)
            deletions = detail.get("deletions", 0)

            if MIN_FILES <= files <= MAX_FILES:
                candidates.append({
                    "number": number,
                    "title": title,
                    "files": files,
                    "additions": additions,
                    "deletions": deletions,
                    "merged_at": pr.get("merged_at"),
                    "base_commit": detail["base"]["sha"],
                })
                print(
                    f"  PR #{number}: {title[:60]:60s} | "
                    f"Files: {files:2d} | +{additions:4d}/-{deletions:4d}"
                )

            if len(candidates) >= MAX_PULLS:
                break

        page += 1
        if len(prs) < 100:
            break

    print(f"\nFound {len(candidates)} merged PRs with {MIN_FILES}-{MAX_FILES} files")

    # Save to JSONL
    out_path = "logs/bug_gen/juspay__hyperswitch.fece9bc3/pr_mirror/merged_complex_prs.jsonl"
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w") as f:
        for c in candidates:
            f.write(json.dumps(c) + "\n")
    print(f"Saved to {out_path}")


if __name__ == "__main__":
    main()
