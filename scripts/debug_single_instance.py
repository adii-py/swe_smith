#!/usr/bin/env python3
"""
Debug ONE instance to understand why F2P/P2P is empty.
Trace through entire validation flow with detailed logging.
"""

import json
import subprocess
from pathlib import Path

INSTANCE_ID = "juspay__hyperswitch.fece9bc3.pr_12317"
REPO = "juspay__hyperswitch.fece9bc3"


def run_command(cmd, timeout=300):
    """Run command and capture output."""
    result = subprocess.run(
        cmd, shell=True, capture_output=True, text=True, timeout=timeout
    )
    return result.returncode, result.stdout, result.stderr


def main():
    print("=" * 60)
    print(f"DEBUGGING INSTANCE: {INSTANCE_ID}")
    print("=" * 60)
    print()

    # Step 1: Load the instance
    print("Step 1: Loading instance...")
    with open(f"logs/bug_gen/{REPO}/pr_mirror/recovered_dataset_clean.json") as f:
        data = json.load(f)

    instance = None
    for inst in data:
        if inst["instance_id"] == INSTANCE_ID:
            instance = inst
            break

    if not instance:
        print(f"❌ Instance {INSTANCE_ID} not found")
        return

    print(f"✓ Found: {instance['instance_id']}")
    print(f"  Title: {instance['title']}")
    print(f"  Test cmd: {instance['test_cmd'][:80]}...")
    print()

    # Step 2: Show the patch
    print("Step 2: Analyzing patch...")
    patch = instance["patch"]
    print(f"  Patch length: {len(patch)} chars")
    print(
        f"  Modified files: {[line.split()[2] for line in patch.split(chr(10)) if line.startswith('+++')]}"
    )
    print()

    # Step 3: Check what test_patch should be added
    print("Step 3: Creating test patch...")
    test_patch = """diff --git a/crates/analytics/src/query.rs b/crates/analytics/src/query.rs
index 0000000..test001 100644
--- a/crates/analytics/src/query.rs
+++ b/crates/analytics/src/query.rs
@@ -994,0 +995,25 @@
+#[cfg(test)]
mod f2p_detection_tests {
    use super::*;

    #[test]
    fn test_sql_injection_prevented() {
        // F2P: Tests that SQL injection is prevented
        let malicious_input = "'; DROP TABLE users; --";
        let result = filter_type_to_sql("col", FilterTypes::Equal, malicious_input);
        
        // The fix adds sanitization, so this should NOT create SQL injection
        // Before fix: format!(...) with raw input allows injection
        // After fix: format!(...) with escaped input prevents injection
        
        // Check that quotes are escaped
        assert!(result.contains("''"), "Expected escaped quotes, got: {}", result);
        assert!(!result.ends_with(";'"), "SQL injection not prevented!");
    }

    #[test]
    fn test_in_operator_unchanged() {
        // P2P: IN operator logic unchanged
        let result = filter_type_to_sql("status", FilterTypes::In, "'active','inactive'");
        assert!(result.contains(" IN "), "Got: {}", result);
    }

    #[test]
    fn test_gt_operator_unchanged() {
        // P2P: Gt operator logic unchanged
        let result = filter_type_to_sql("amt", FilterTypes::Gt, "100");
        assert!(result.contains(">"), "Got: {}", result);
    }
}
"""

    instance["test_patch"] = test_patch
    print("✓ Test patch created")
    print("  Tests: test_sql_injection_prevented (F2P)")
    print("         test_in_operator_unchanged (P2P)")
    print("         test_gt_operator_unchanged (P2P)")
    print()

    # Step 4: Save debug instance
    print("Step 4: Saving debug instance...")
    debug_file = Path("logs/debug_single_instance.json")
    with open(debug_file, "w") as f:
        json.dump([instance], f, indent=2)
    print(f"✓ Saved to: {debug_file}")
    print()

    # Step 5: Show what validation will do
    print("Step 5: Validation process overview...")
    print("  1. Pre-gold: Apply test_patch only")
    print("     → Run tests (should all PASS)")
    print("     → Save results to test_output_pre_gold.txt")
    print()
    print("  2. Post-gold: Apply test_patch + bug_patch")
    print("     → Run tests (F2P should FAIL, P2P should PASS)")
    print("     → Save results to test_output.txt")
    print()
    print("  3. Compare: Parse both outputs")
    print("     → Find tests that: pre=PASSED, post=FAILED (F2P)")
    print("     → Find tests that: pre=PASSED, post=PASSED (P2P)")
    print()

    print("=" * 60)
    print("READY TO RUN VALIDATION")
    print("=" * 60)
    print()
    print(f"Run with:")
    print(
        f"  python -m swesmith.harness.valid {debug_file} --workers 1 --redo_existing"
    )


if __name__ == "__main__":
    main()
