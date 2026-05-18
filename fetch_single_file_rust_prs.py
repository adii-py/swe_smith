#!/usr/bin/env python3
"""Fetch 100 merged Hyperswitch PRs with exactly 1 Rust file and <=100 line changes."""
import json
import os
import sys
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError
import time

TOKEN = os.environ.get("GITHUB_TOKEN", "").strip()
OWNER = "juspay"
REPO = "hyperswitch"
TARGET_COUNT = 100
PER_PAGE = 100


def api_call(endpoint):
    url = f"https://api.github.com{endpoint}"
    headers = {"Accept": "application/vnd.github.v3+json", "User-Agent": "swesmith-fetch"}
    if TOKEN:
        headers["Authorization"] = f"Bearer {TOKEN}"
    req = Request(url, headers=headers)
    try:
        with urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except HTTPError as e:
        if e.code == 403:
            return "rate_limited"
        print(f"HTTP Error {e.code}: {e.reason} for {url}")
        return None
    except Exception as e:
        print(f"Error: {e} for {url}")
        return None


def get_closed_prs(page=1, per_page=100):
    return api_call(
        f"/repos/{OWNER}/{REPO}/pulls?state=closed&sort=updated&direction=desc&per_page={per_page}&page={page}"
    )


def get_pr_files(number):
    return api_call(f"/repos/{OWNER}/{REPO}/pulls/{number}/files")


def get_patch(diff_url):
    headers = {"User-Agent": "swesmith-fetch"}
    if TOKEN:
        headers["Authorization"] = f"Bearer {TOKEN}"
    req = Request(diff_url, headers=headers)
    try:
        with urlopen(req, timeout=30) as resp:
            return resp.read().decode()
    except Exception as e:
        print(f"Error fetching patch: {e}")
        return None


def main():
    if not TOKEN:
        print("ERROR: Set GITHUB_TOKEN environment variable")
        sys.exit(1)

    candidates = []
    page = 1
    checked = 0

    while len(candidates) < TARGET_COUNT:
        print(f"Fetching page {page}...")
        prs = get_closed_prs(page=page, per_page=PER_PAGE)
        if prs == "rate_limited":
            print("Rate limited, waiting 60s...")
            time.sleep(60)
            continue
        if not prs:
            print("No more PRs or error")
            break

        for pr in prs:
            if not pr.get("merged_at"):
                continue

            number = pr["number"]
            checked += 1

            # Get file details
            files = get_pr_files(number)
            if files == "rate_limited":
                print("Rate limited on files, waiting 60s...")
                time.sleep(60)
                files = get_pr_files(number)

            if not files or len(files) != 1:
                continue

            f = files[0]
            fname = f["filename"]
            if not fname.endswith(".rs"):
                continue

            total_changes = f.get("additions", 0) + f.get("deletions", 0)
            if total_changes > 100:
                continue

            # Fetch patch
            patch = get_patch(pr["diff_url"])
            if not patch:
                continue

            instance = {
                "instance_id": f"juspay__hyperswitch.fece9bc3.pr_{number}",
                "repo": f"{OWNER}/{REPO}",
                "pull_number": number,
                "title": pr.get("title", ""),
                "base_commit": pr["base"]["sha"],
                "patch": patch,
            }
            candidates.append(instance)
            print(
                f"  OK PR #{number}: {pr['title'][:45]:45s} | {fname.split('/')[-1]:35s} | +{f['additions']:3d}/-{f['deletions']:3d}"
            )

            if len(candidates) >= TARGET_COUNT:
                break

        if len(prs) < PER_PAGE:
            print("Reached end of PR list")
            break

        page += 1

    print(f"\nFound {len(candidates)} candidates out of {checked} checked")

    out_path = Path(
        "/Users/aditya.singh.001/Desktop/SWE-smith/logs/bug_gen/juspay__hyperswitch.fece9bc3/pr_mirror/single_file_rust_prs.jsonl"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        for c in candidates:
            f.write(json.dumps(c) + "\n")
    print(f"Saved to {out_path}")
    return out_path


if __name__ == "__main__":
    main()
