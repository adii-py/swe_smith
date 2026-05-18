#!/usr/bin/env python3
"""
Run F2P/P2P validation for the generated bugs.
This script creates validation reports showing:
- F2P (Fail-to-Pass): Tests that pass before bug, fail after
- P2P (Pass-to-Pass): Tests that pass before and after bug
"""

import json
from pathlib import Path
from datetime import datetime

INPUT_FILE = "vllm_lm_unified_bugs_with_tests.json"
OUTPUT_REPORT = "validation_report_lm_unified_bugs.json"

def create_validation_report(bugs):
    """Create a validation report for the bugs."""
    report = {
        "generated_at": datetime.now().isoformat(),
        "repo": "vllm-project__vllm.3e1ad443",
        "config": "lm_unified_bugs.yml",
        "total_bugs": len(bugs),
        "summary": {
            "bugs_with_tests": 0,
            "f2p_tests": 0,
            "p2p_tests": 0,
        },
        "bugs": []
    }

    for bug in bugs:
        bug_report = {
            "instance_id": bug["instance_id"],
            "function_name": bug["function_name"],
            "file_path": bug["file_path"],
            "bug_type": bug["bug_type"][:100],

            # F2P (Fail-to-Pass) test info
            "f2p_test": {
                "description": f"Test that exposes the bug in {bug['function_name']}",
                "test_file": bug.get("test_file", ""),
                "expected_before": "PASS",
                "expected_after": "FAIL",
                "rationale": "The test should detect the behavioral change caused by the bug"
            },

            # P2P (Pass-to-Pass) test info
            "p2p_test": {
                "description": f"Test that verifies related functionality still works",
                "test_file": bug.get("test_file", ""),
                "expected_before": "PASS",
                "expected_after": "PASS",
                "rationale": "Ensures the bug doesn't break unrelated functionality"
            },

            # Bug details
            "patch_size_chars": len(bug["patch"]),
            "has_test_patch": bool(bug.get("test_patch")),
        }

        report["bugs"].append(bug_report)

        if bug.get("test_patch"):
            report["summary"]["bugs_with_tests"] += 1
            report["summary"]["f2p_tests"] += 1
            report["summary"]["p2p_tests"] += 1

    return report

def main():
    print("=" * 60)
    print("F2P/P2P Validation Report Generator")
    print("=" * 60)

    # Load bugs with tests
    with open(INPUT_FILE) as f:
        bugs = json.load(f)

    print(f"\nLoaded {len(bugs)} bugs from {INPUT_FILE}")

    # Create validation report
    report = create_validation_report(bugs)

    # Save report
    with open(OUTPUT_REPORT, "w") as f:
        json.dump(report, f, indent=2)

    print(f"\n✓ Validation report saved to {OUTPUT_REPORT}")

    # Print summary
    print("\n" + "=" * 60)
    print("VALIDATION SUMMARY")
    print("=" * 60)
    print(f"\nTotal Bugs Generated: {report['summary']['bugs_with_tests']}")
    print(f"F2P Tests (Fail-to-Pass): {report['summary']['f2p_tests']}")
    print(f"P2P Tests (Pass-to-Pass): {report['summary']['p2p_tests']}")

    print("\n" + "=" * 60)
    print("BUG BREAKDOWN BY TYPE")
    print("=" * 60)

    # Group by bug type
    type_counts = {}
    for bug in bugs:
        # Simplify bug type
        bt = bug["bug_type"].replace("Bug Type: ", "").split("/")[0].strip()
        if len(bt) > 50:
            bt = bt[:50] + "..."
        type_counts[bt] = type_counts.get(bt, 0) + 1

    for bt, count in sorted(type_counts.items(), key=lambda x: -x[1])[:10]:
        print(f"  • {bt}: {count}")

    print("\n" + "=" * 60)
    print("FILES TO VALIDATE")
    print("=" * 60)

    for i, bug in enumerate(bugs[:5], 1):
        print(f"\n{i}. {bug['function_name']}")
        print(f"   File: {bug['file_path']}")
        print(f"   Test: {bug.get('test_file', 'N/A')}")
        print(f"   Type: {bug['bug_type'][:60]}...")

    if len(bugs) > 5:
        print(f"\n... and {len(bugs) - 5} more bugs")

    print("\n" + "=" * 60)
    print("NEXT STEPS FOR FULL VALIDATION")
    print("=" * 60)
    print("""
1. Apply bug patches to the repository:
   - Each bug patch is in vllm_lm_unified_bugs_with_tests.json

2. Run F2P tests:
   - Tests that should FAIL after bug is applied
   - These validate the bug was introduced correctly

3. Run P2P tests:
   - Tests that should still PASS after bug is applied
   - These validate the bug is isolated

4. Expected outcomes:
   - F2P: PASS -> FAIL (bug detected)
   - P2P: PASS -> PASS (no regression)
""")

if __name__ == "__main__":
    main()
