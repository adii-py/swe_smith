#!/usr/bin/env python3
"""Filter PRs that change .rs files."""
import json
import os
from urllib.request import Request, urlopen
from urllib.error import HTTPError

TOKEN = os.environ.get("GITHUB_TOKEN", "").strip()
OWNER = "juspay"
REPO = "hyperswitch"


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
        print(f"  HTTP Error {e.code}: {e.reason}")
        return None
    except Exception as e:
        print(f"  Error: {e}")
        return None


def get_pr_files(number):
    """List files changed in a PR."""
    return api_call(f"/repos/{OWNER}/{REPO}/pulls/{number}/files?per_page=100") or []


def main():
    in_path = "logs/bug_gen/juspay__hyperswitch.fece9bc3/pr_mirror/merged_complex_prs.jsonl"
    out_path = "logs/bug_gen/juspay__hyperswitch.fece9bc3/pr_mirror/rust_prs.jsonl"

    rust_prs = []
    for line in open(in_path):
        pr = json.loads(line)
        num = pr["number"]
        title = pr["title"]
        print(f"Checking PR #{num}: {title[:50]}...", end=" ")

        files = get_pr_files(num)
        rs_files = [f for f in files if f["filename"].endswith(".rs")]
        non_rs_files = [f for f in files if not f["filename"].endswith(".rs")]

        if rs_files:
            print(f"YES ({len(rs_files)} .rs files, {len(non_rs_files)} other)")
            pr["rs_files"] = [f["filename"] for f in rs_files]
            pr["rs_additions"] = sum(f["additions"] for f in rs_files)
            pr["rs_deletions"] = sum(f["deletions"] for f in rs_files)
            rust_prs.append(pr)
        else:
            print(f"NO ({len(non_rs_files)} non-.rs files)")

    print(f"\nFound {len(rust_prs)} PRs with .rs file changes")

    with open(out_path, "w") as f:
        for pr in rust_prs:
            f.write(json.dumps(pr) + "\n")
    print(f"Saved to {out_path}")


if __name__ == "__main__":
    main()
