#!/usr/bin/env python3
"""Update instances to use new commit hash."""
import json
import os

OLD_COMMIT = "39860a4e1"
NEW_COMMIT = "39860a4e"  # Use 8-character commit hash for registry compatibility

INSTANCES_PATH = "/Users/aditya.singh.001/Desktop/SWE-smith/logs/bug_gen/mirror_instances_for_validation.json"

def main():
    with open(INSTANCES_PATH, 'r') as f:
        instances = json.load(f)

    print(f"Updating {len(instances)} instances from {OLD_COMMIT} to {NEW_COMMIT}...")

    for inst in instances:
        # Update instance_id
        old_id = inst['instance_id']
        inst['instance_id'] = old_id.replace(OLD_COMMIT, NEW_COMMIT)

        # Update repo
        inst['repo'] = inst['repo'].replace(OLD_COMMIT, NEW_COMMIT)

        print(f"  {old_id} -> {inst['instance_id']}")

    with open(INSTANCES_PATH, 'w') as f:
        json.dump(instances, f, indent=2)

    print(f"\nUpdated {len(instances)} instances")
    print(f"New commit hash: {NEW_COMMIT}")

if __name__ == "__main__":
    main()
