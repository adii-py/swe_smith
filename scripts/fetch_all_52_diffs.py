#!/usr/bin/env python3
"""Fetch diffs for all 52 Rust PRs and create comprehensive generation input."""
import json
import os
from urllib.request import Request, urlopen

TOKEN = os.environ.get("GITHUB_TOKEN", "").strip()
BASE_COMMIT = "fece9bc38b9890a1a40912ce2a95037842362e27"
OUT_PATH = "/Users/aditya.singh.001/Desktop/SWE-smith/logs/bug_gen/juspay__hyperswitch.fece9bc3/pr_mirror/all_52_rust_prs.jsonl"


def fetch_diff(pr_number):
    url = f"https://github.com/juspay/hyperswitch/pull/{pr_number}.diff"
    headers = {"Accept": "application/vnd.github.v3.diff"}
    if TOKEN:
        headers["Authorization"] = f"Bearer {TOKEN}"
    req = Request(url, headers=headers)
    try:
        with urlopen(req, timeout=60) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        print(f"  Error: {e}")
        return None


def load_existing_diffs():
    """Load diffs already fetched from other files."""
    diffs = {}
    base = "/Users/aditya.singh.001/Desktop/SWE-smith/logs/bug_gen/juspay__hyperswitch.fece9bc3/pr_mirror"

    for fname in ["merged_instances.jsonl", "new_rust_candidates_with_diffs.jsonl"]:
        path = os.path.join(base, fname)
        if os.path.exists(path):
            with open(path) as f:
                for line in f:
                    d = json.loads(line)
                    pn = d.get("pull_number")
                    patch = d.get("patch", "")
                    if pn and patch.startswith("diff "):
                        diffs[pn] = patch
                        print(f"  Reusing diff from {fname} for PR #{pn}")
    return diffs


def main():
    rust_prs_path = "/Users/aditya.singh.001/Desktop/SWE-smith/logs/bug_gen/juspay__hyperswitch.fece9bc3/pr_mirror/rust_prs.jsonl"
    with open(rust_prs_path) as f:
        prs = [json.loads(line) for line in f]

    existing_diffs = load_existing_diffs()
    instances = []
    failed = []

    for pr in prs:
        num = pr["number"]
        title = pr["title"]
        print(f"PR #{num}: {title[:50]}...", end=" ")

        if num in existing_diffs:
            diff = existing_diffs[num]
            print(f"REUSED ({len(diff)} chars)")
        else:
            diff = fetch_diff(num)
            if diff:
                print(f"FETCHED ({len(diff)} chars)")
            else:
                print("FAILED")
                failed.append(num)
                continue

        instances.append({
            "instance_id": f"juspay__hyperswitch.fece9bc3.pr_{num}",
            "repo": "juspay/hyperswitch",
            "pull_number": num,
            "title": title,
            "base_commit": BASE_COMMIT,
            "patch": diff,
        })

    with open(OUT_PATH, "w") as f:
        for inst in instances:
            f.write(json.dumps(inst) + "\n")

    print(f"\nTotal: {len(instances)} instances with diffs")
    if failed:
        print(f"Failed to fetch: {failed}")
    print(f"Saved to {OUT_PATH}")


if __name__ == "__main__":
    main()
