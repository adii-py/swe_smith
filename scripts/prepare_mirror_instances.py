#!/usr/bin/env python3
"""
Convert raw PR data from print_pulls to instance format for mirror generate.py.
Fetches actual diff content from GitHub for each PR.
"""
import json
import sys
import urllib.request

INPUT_FILE = "logs/bug_gen/juspay__hyperswitch.fece9bc3/pr_mirror/hyperswitch-prs.jsonl"
OUTPUT_FILE = "logs/bug_gen/juspay__hyperswitch.fece9bc3/pr_mirror/all_51_instances.jsonl"
BASE_COMMIT = "fece9bc38b9890a1a40912ce2a95037842362e27"

def fetch_diff(url):
    """Fetch diff content from GitHub."""
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            return resp.read().decode('utf-8', errors='replace')
    except Exception as e:
        print(f"  Failed to fetch diff: {e}")
        return None

def main():
    instances = []
    with open(INPUT_FILE) as f:
        for line in f:
            pr = json.loads(line)
            pull_number = pr.get('number', pr.get('pull_number'))
            title = pr.get('title', '')
            patch_url = pr.get('patch_url') or pr.get('diff_url') or f"https://github.com/juspay/hyperswitch/pull/{pull_number}.diff"

            print(f"Processing PR #{pull_number}: {title[:60]}...")
            patch = fetch_diff(patch_url)
            if patch is None:
                continue

            instance = {
                "instance_id": f"juspay__hyperswitch.fece9bc3.pr_{pull_number}",
                "repo": "juspay/hyperswitch",
                "pull_number": pull_number,
                "patch": patch,
                "title": title,
                "base_commit": BASE_COMMIT,
            }
            instances.append(instance)
            print(f"  -> Added ({len(patch)} chars diff)")

    print(f"\nTotal instances prepared: {len(instances)}")

    with open(OUTPUT_FILE, 'w') as f:
        for inst in instances:
            f.write(json.dumps(inst) + '\n')

    print(f"Saved to {OUTPUT_FILE}")

if __name__ == "__main__":
    main()
