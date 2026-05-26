#!/usr/bin/env python3
"""
Simple validation with Redis tests skipped.
Uses existing tests only, no custom patches.
"""

import json
import subprocess
from pathlib import Path

REPO = "juspay__hyperswitch.fece9bc3"


def update_test_cmd(cmd):
    """Add skip filters for Redis tests."""
    redis_tests = [
        "api_keys_cache",
        "mockdb_api_key",
        "mockdb_dispute",
        "mockdb_event",
        "mockdb_locker",
        "mockdb_merchant_key_store",
        "redis_lock",
        "concurrent_webhook",
        "connector_profile_id_cache",
        "find_payment_attempt",
        "payment_attempt_insert",
        "payment_attempt_mandate",
    ]

    skip_str = " ".join([f"--skip {t}" for t in redis_tests])

    if "--nocapture" in cmd:
        return cmd.replace("--nocapture", f"{skip_str} --nocapture")
    return f"{cmd} {skip_str}"


def main():
    print("Preparing filtered validation...")
    print()

    # Load original
    with open(f"logs/bug_gen/{REPO}/pr_mirror/recovered_dataset_clean.json") as f:
        instances = json.load(f)

    print(f"Loaded {len(instances)} instances")

    # Update commands
    for inst in instances:
        orig_cmd = inst.get("test_cmd", "")
        filtered = update_test_cmd(orig_cmd)
        inst["test_cmd"] = filtered
        inst["test_cmd_original"] = orig_cmd

    # Save
    output = f"logs/bug_gen/{REPO}/pr_mirror/pr_mirror_simple_filtered.json"
    with open(output, "w") as f:
        json.dump(instances, f, indent=2)

    print(f"✓ Saved to: {output}")
    print()
    print("Sample commands:")
    for i in range(min(3, len(instances))):
        print(f"\n{i + 1}. {instances[i]['instance_id']}")
        print(f"   Filtered: {instances[i]['test_cmd'][:80]}...")

    print()
    print("Ready to validate!")


if __name__ == "__main__":
    main()
