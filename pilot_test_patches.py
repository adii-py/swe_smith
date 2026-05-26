#!/usr/bin/env python3
"""Pilot test - generate test patches for 3 instances to verify approach."""

import json
import sys
sys.path.insert(0, '/Users/aditya.singh.001/Desktop/SWE-smith')

from generate_test_patches_for_instances import generate_test_patch, REPO_PATH, DATASET_PATH, OUTPUT_PATH

# Test instances
TEST_INSTANCES = [
    "juspay__hyperswitch.fece9bc3.pr_12317",
    "juspay__hyperswitch.fece9bc3.pr_12315",
    "juspay__hyperswitch.fece9bc3.pr_11219",
]

def main():
    # Load dataset
    with open(DATASET_PATH) as f:
        data = json.load(f)

    instances_by_id = {inst["instance_id"]: inst for inst in data}

    # Load analysis
    analysis_path = Path("/Users/aditya.singh.001/Desktop/SWE-smith/logs/bug_gen/juspay__hyperswitch.fece9bc3/pr_mirror/test_coverage_analysis.json")
    with open(analysis_path) as f:
        analysis_list = json.load(f)
        analysis_by_id = {a["instance_id"]: a for a in analysis_list}

    print("PILOT TEST - Generating test patches for 3 instances")
    print("="*80)

    for iid in TEST_INSTANCES:
        if iid not in instances_by_id:
            print(f"{iid}: NOT FOUND")
            continue

        instance = instances_by_id[iid]
        analysis = analysis_by_id.get(iid, {})

        print(f"\n{iid}:")
        print(f"  Title: {instance['title']}")
        print(f"  Crates: {analysis.get('crates_affected', [])}")
        print(f"  Functions: {analysis.get('changed_functions', [])}")
        print(f"  Existing tests: {len(analysis.get('existing_tests_found', []))}")
        print()

        test_patch = generate_test_patch(instance, analysis)

        if test_patch:
            print(f"  SUCCESS!")
            print(f"  Test patch length: {len(test_patch)} chars")
            print(f"  Preview:")
            print("  " + "\n  ".join(test_patch.split("\n")[:15]))
            print("  ...")

            # Save to instance
            instance["test_patch"] = test_patch
        else:
            print(f"  FAILED to generate test patch")

    # Save updated dataset
    pilot_output = OUTPUT_PATH.parent / "recovered_dataset_pilot_test.json"
    with open(pilot_output, "w") as f:
        json.dump(data, f, indent=2)

    print(f"\n{'='*80}")
    print(f"Pilot test complete. Output saved to: {pilot_output}")

if __name__ == "__main__":
    from pathlib import Path
    main()
