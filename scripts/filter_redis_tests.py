#!/usr/bin/env python3
"""
Update test commands to skip Redis-dependent tests.
Based on test output analysis, skip tests that require external services.
"""

import json
from pathlib import Path

INPUT_FILE = Path(
    "logs/bug_gen/juspay__hyperswitch.fece9bc3/pr_mirror/pr_mirror_with_tests_77.json"
)
OUTPUT_FILE = Path(
    "logs/bug_gen/juspay__hyperswitch.fece9bc3/pr_mirror/pr_mirror_filtered_tests.json"
)

# Tests that failed due to Redis (from our analysis)
REDIS_DEPENDENT_TESTS = [
    "api_keys_cache",
    "mockdb_api_key_interface",
    "mockdb_dispute_interface",
    "concurrent_webhook_insertion_with_redis_lock",
    "mockdb_event_interface",
    "mockdb_locker_mock_up_interface",
    "test_connector_profile_id_cache",
    "test_mock_db_merchant_key_store_interface",
    "test_find_payment_attempt",
    "test_payment_attempt_insert",
    "test_payment_attempt_mandate_field",
    "cache",
    "redis",
    "storage",
]


def create_filtered_test_cmd(original_cmd, crate_name):
    """Create test command that skips Redis-dependent tests."""

    # Base command with --lib for unit tests only
    if "--lib" not in original_cmd:
        base_cmd = original_cmd.replace("--no-fail-fast", "--lib --no-fail-fast")
    else:
        base_cmd = original_cmd

    # Add skip filters for Redis tests
    skip_filters = " ".join([f"--skip {test}" for test in REDIS_DEPENDENT_TESTS])

    # Insert skip filters before the --nocapture
    if "--nocapture" in base_cmd:
        filtered_cmd = base_cmd.replace("--nocapture", f"{skip_filters} --nocapture")
    else:
        filtered_cmd = f"{base_cmd} {skip_filters}"

    return filtered_cmd


def main():
    print("Filtering Redis-dependent tests from validation...")
    print()

    # Load instances
    with open(INPUT_FILE) as f:
        instances = json.load(f)

    print(f"Loaded {len(instances)} instances")
    print()

    # Update test commands
    updated = []
    for inst in instances:
        original_cmd = inst.get("test_cmd", "")

        # Extract crate name from test command
        crate = "router"  # default
        if "-p " in original_cmd:
            parts = original_cmd.split("-p ")
            if len(parts) > 1:
                crate_part = parts[1].split()[0]
                crate = crate_part

        # Create filtered command
        filtered_cmd = create_filtered_test_cmd(original_cmd, crate)

        inst["test_cmd"] = filtered_cmd
        inst["test_cmd_original"] = original_cmd
        inst["test_cmd_filtered"] = True

        updated.append(inst)

    # Show examples
    print("Sample filtered commands:")
    for i, inst in enumerate(updated[:3]):
        print(f"\n{i + 1}. {inst['instance_id']}")
        print(f"   Original: {inst['test_cmd_original'][:70]}...")
        print(f"   Filtered: {inst['test_cmd'][:70]}...")

    print()
    print(f"Filtered tests: {REDIS_DEPENDENT_TESTS}")
    print()

    # Save
    with open(OUTPUT_FILE, "w") as f:
        json.dump(updated, f, indent=2)

    print(f"✓ Saved {len(updated)} instances to: {OUTPUT_FILE}")
    print()
    print("Next: Re-run validation on 2 test instances")


if __name__ == "__main__":
    main()
