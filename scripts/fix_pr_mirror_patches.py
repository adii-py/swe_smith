#!/usr/bin/env python3
"""
Fix PR mirror patches to generate F2P and P2P cases.

Strategy:
1. Keep the original PR mirror bug patches (they compile on their own)
2. Add test patches that test the modified functions
3. The tests should:
   - Pass before the bug is applied (testing original behavior)
   - Fail after the bug is applied (F2P)
   - Some should pass both times (P2P)
"""

import json
from pathlib import Path


def create_test_patch_for_analytics():
    """Create test patch for analytics query.rs bugs."""
    return """diff --git a/crates/analytics/src/query.rs b/crates/analytics/src/query.rs
index f8453eca395..aaaaaaa1111 100644
--- a/crates/analytics/src/query.rs
+++ b/crates/analytics/src/query.rs
@@ -994,0 +995,35 @@
+#[cfg(test)]
mod pr_mirror_tests {
    use super::*;

    #[test]
    fn test_filter_type_to_sql_equal() {
        // F2P test: This tests the Equal operator behavior
        let result = filter_type_to_sql("col", FilterTypes::Equal, "val");
        assert!(result.contains(" = "), "Expected = operator, got: {}", result);
        assert!(!result.contains("!=") || result == "col != 'val'", "Should not be != operator");
    }

    #[test]  
    fn test_filter_type_to_sql_not_equal() {
        // F2P test: Tests NotEqual operator
        let result = filter_type_to_sql("col", FilterTypes::NotEqual, "val");
        assert!(result.contains("!="), "Expected != operator, got: {}", result);
    }

    #[test]
    fn test_filter_type_to_sql_in() {
        // F2P test: Tests In operator
        let result = filter_type_to_sql("col", FilterTypes::In, "1,2,3");
        assert!(result.contains(" IN "), "Expected IN operator, got: {}", result);
        assert!(!result.contains("NOT IN"), "Should not contain NOT IN");
    }

    #[test]
    fn test_filter_type_to_sql_gt() {
        // P2P test: Tests Gt operator (unchanged by bug)
        let result = filter_type_to_sql("col", FilterTypes::Gt, "100");
        assert!(result.contains(">"), "Expected > operator, got: {}", result);
        assert!(!result.contains("<"), "Should not be < operator");
    }

    #[test]
    fn test_filter_type_to_sql_gte() {
        // P2P test: Tests Gte operator
        let result = filter_type_to_sql("col", FilterTypes::Gte, "100");
        assert!(result.contains(">="), "Expected >= operator, got: {}", result);
    }
}
"""


def create_fixed_instances():
    """Create fixed instances with proper test patches."""

    # Read the original clean dataset
    with open(
        "logs/bug_gen/juspay__hyperswitch.fece9bc3/pr_mirror/recovered_dataset_clean.json",
        "r",
    ) as f:
        original_data = json.load(f)

    # Select analytics instances (these have the filter_type_to_sql function)
    analytics_instances = [
        inst
        for inst in original_data
        if "analytics/src/query.rs" in inst.get("patch", "")
    ][:5]  # Take first 5 analytics instances

    print(f"Selected {len(analytics_instances)} analytics instances")

    fixed_instances = []
    test_patch = create_test_patch_for_analytics()

    for i, inst in enumerate(analytics_instances):
        fixed_inst = inst.copy()

        # Add test patch
        fixed_inst["test_patch"] = test_patch

        # Mark that this instance has both bug and test patches
        fixed_inst["has_test_patch"] = True

        fixed_instances.append(fixed_inst)
        print(f"  {i + 1}. {inst['instance_id']} - test patch added")

    # Save
    output_file = "logs/bug_gen/juspay__hyperswitch.fece9bc3/pr_mirror/pr_mirror_fixed_with_tests.json"
    with open(output_file, "w") as f:
        json.dump(fixed_instances, f, indent=2)

    print(f"\nSaved {len(fixed_instances)} fixed instances to: {output_file}")
    return output_file


if __name__ == "__main__":
    create_fixed_instances()
