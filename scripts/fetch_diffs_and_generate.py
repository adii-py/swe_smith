#!/usr/bin/env python3
"""Fetch actual diff content for PRs and create generation input."""
import json
import os
from urllib.request import Request, urlopen
from urllib.error import HTTPError

TOKEN = os.environ.get("GITHUB_TOKEN", "").strip()


def fetch_diff(pr_number):
    """Fetch the raw diff for a PR."""
    url = f"https://github.com/juspay/hyperswitch/pull/{pr_number}.diff"
    headers = {"Accept": "application/vnd.github.v3.diff"}
    if TOKEN:
        headers["Authorization"] = f"Bearer {TOKEN}"
    req = Request(url, headers=headers)
    try:
        with urlopen(req, timeout=60) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except Exception as e:
        print(f"  Error fetching diff for PR #{pr_number}: {e}")
        return None


def main():
    # Selected PRs
    selected = [11726, 11782, 11518, 11746, 11817, 10182, 11762, 11729, 11483, 132]

    base_commit = "fece9bc38b9890a1a40912ce2a95037842362e27"
    instances = []

    for num in selected:
        print(f"Fetching diff for PR #{num}...", end=" ")
        diff = fetch_diff(num)
        if diff:
            print(f"OK ({len(diff)} chars)")
            instances.append({
                "instance_id": f"juspay__hyperswitch.fece9bc3.pr_{num}",
                "repo": "juspay/hyperswitch",
                "pull_number": num,
                "base_commit": base_commit,
                "patch": diff,
            })
        else:
            print("FAILED")

    out_path = "/Users/aditya.singh.001/Desktop/SWE-smith/logs/bug_gen/juspay__hyperswitch.fece9bc3/pr_mirror/new_rust_candidates_with_diffs.jsonl"
    with open(out_path, "w") as f:
        for inst in instances:
            f.write(json.dumps(inst) + "\n")
    print(f"\nSaved {len(instances)} instances with diffs to {out_path}")


if __name__ == "__main__":
    main()
