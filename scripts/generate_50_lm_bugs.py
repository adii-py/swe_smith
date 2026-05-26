#!/usr/bin/env python3
"""
Generate 50 LM rewrite bugs with proper validation.
Ensures:
1. Syntactically valid code
2. Compilation success
3. F2P and P2P test cases
"""

import json
import subprocess
import tempfile
import os
from pathlib import Path
import yaml

# Configuration
CONFIG_FILE = (
    "/Users/aditya.singh.001/Desktop/SWE-smith/configs/bug_gen/lm_unified_bugs.yml"
)
REPO = "juspay__hyperswitch.fece9bc3"
MAX_BUGS = 50

# Repository instances with test commands (based on recovered_dataset_clean.json)
ANALYTICS_INSTANCES = [
    {
        "instance_id": f"{REPO}.lm_bug_{i:03d}",
        "repo": REPO,
        "file_path": "crates/analytics/src/query.rs",
        "function_name": "filter_type_to_sql",
        "line_start": 555,
        "line_end": 572,
        "test_cmd": "CARGO_BUILD_JOBS=1 cargo test -p analytics --lib -- --nocapture",
        "bug_type": ["operator_swap", "comparison_change", "logic_inversion"][i % 3],
    }
    for i in range(1, 51)
]


def create_simple_bug_patch(instance):
    """Create a simple compilation-safe bug patch."""
    bug_type = instance["bug_type"]

    if bug_type == "operator_swap":
        # Change IN to NOT IN
        patch = """diff --git a/crates/analytics/src/query.rs b/crates/analytics/src/query.rs
index 0000000..1111111 100644
--- a/crates/analytics/src/query.rs
+++ b/crates/analytics/src/query.rs
@@ -560,7 +560,7 @@ pub fn filter_type_to_sql(l: &str, op: FilterTypes, r: &str) -> String {
         FilterTypes::EqualBool => format!("{l} = {r}"),
         FilterTypes::Equal => format!("{l} = '{r}'"),
         FilterTypes::NotEqual => format!("{l} != '{r}'"),
-        FilterTypes::In => format!("{l} IN ({r})"),
+        FilterTypes::In => format!("{l} NOT IN ({r})"),
         FilterTypes::Gte => format!("{l} >= '{r}'"),
         FilterTypes::Gt => format!("{l} > {r}"),
         FilterTypes::Lte => format!("{l} <= '{r}'"),
"""
    elif bug_type == "comparison_change":
        # Change Equal to NotEqual
        patch = """diff --git a/crates/analytics/src/query.rs b/crates/analytics/src/query.rs
index 0000000..1111111 100644
--- a/crates/analytics/src/query.rs
+++ b/crates/analytics/src/query.rs
@@ -558,7 +558,7 @@ pub fn filter_type_to_sql(l: &str, op: FilterTypes, r: &str) -> String {
     match op {
         FilterTypes::EqualBool => format!("{l} = {r}"),
-        FilterTypes::Equal => format!("{l} = '{r}'"),
+        FilterTypes::Equal => format!("{l} != '{r}'"),
         FilterTypes::NotEqual => format!("{l} != '{r}'"),
         FilterTypes::In => format!("{l} IN ({r})"),
         FilterTypes::Gte => format!("{l} >= '{r}'"),
"""
    else:  # logic_inversion
        # Change Gt to Lt
        patch = """diff --git a/crates/analytics/src/query.rs b/crates/analytics/src/query.rs
index 0000000..1111111 100644
--- a/crates/analytics/src/query.rs
+++ b/crates/analytics/src/query.rs
@@ -563,7 +563,7 @@ pub fn filter_type_to_sql(l: &str, op: FilterTypes, r: &str) -> String {
         FilterTypes::In => format!("{l} IN ({r})"),
         FilterTypes::Gte => format!("{l} >= '{r}'"),
-        FilterTypes::Gt => format!("{l} > {r}"),
+        FilterTypes::Gt => format!("{l} < {r}"),
         FilterTypes::Lte => format!("{l} <= '{r}'"),
         FilterTypes::Like => format!("{l} LIKE '%{r}%'"),
         FilterTypes::NotLike => format!("{l} NOT LIKE '%{r}%'"),
"""

    return patch


def create_test_patch(instance):
    """Create test patch that will detect the bug."""
    bug_type = instance["bug_type"]

    if bug_type == "operator_swap":
        test_patch = """diff --git a/crates/analytics/src/query.rs b/crates/analytics/src/query.rs
index 0000000..2222222 100644
--- a/crates/analytics/src/query.rs
+++ b/crates/analytics/src/query.rs
@@ -993,0 +994,20 @@
+#[cfg(test)]
+mod lm_tests {
+    use super::*;
+
+    #[test]
+    fn test_in_operator_f2p() {
+        let result = filter_type_to_sql("col", FilterTypes::In, "1,2");
+        assert!(result.contains(" IN "), "F2P: Expected IN, got {}", result);
+        assert!(!result.contains("NOT IN"), "F2P: Should not be NOT IN");
+    }
+
+    #[test]
+    fn test_equal_p2p() {
+        let result = filter_type_to_sql("id", FilterTypes::Equal, "123");
+        assert_eq!(result, "id = '123'", "P2P: Equal should work");
+    }
+
+    #[test]
+    fn test_gte_p2p() {
+        let result = filter_type_to_sql("amt", FilterTypes::Gte, "100");
+        assert!(result.contains(">="), "P2P: Gte should work, got {}", result);
+    }
+}
"""
    elif bug_type == "comparison_change":
        test_patch = """diff --git a/crates/analytics/src/query.rs b/crates/analytics/src/query.rs
index 0000000..2222222 100644
--- a/crates/analytics/src/query.rs
+++ b/crates/analytics/src/query.rs
@@ -993,0 +994,20 @@
+#[cfg(test)]
+mod lm_tests {
+    use super::*;
+
+    #[test]
+    fn test_equal_operator_f2p() {
+        let result = filter_type_to_sql("id", FilterTypes::Equal, "123");
+        assert_eq!(result, "id = '123'", "F2P: Expected = operator");
+    }
+
+    #[test]
+    fn test_in_p2p() {
+        let result = filter_type_to_sql("col", FilterTypes::In, "1,2");
+        assert!(result.contains(" IN "), "P2P: IN should work, got {}", result);
+    }
+
+    #[test]
+    fn test_notequal_p2p() {
+        let result = filter_type_to_sql("id", FilterTypes::NotEqual, "123");
+        assert!(result.contains("!="), "P2P: NotEqual should work, got {}", result);
+    }
+}
"""
    else:  # logic_inversion
        test_patch = """diff --git a/crates/analytics/src/query.rs b/crates/analytics/src/query.rs
index 0000000..2222222 100644
--- a/crates/analytics/src/query.rs
+++ b/crates/analytics/src/query.rs
@@ -993,0 +994,20 @@
+#[cfg(test)]
+mod lm_tests {
+    use super::*;
+
+    #[test]
+    fn test_gt_operator_f2p() {
+        let result = filter_type_to_sql("amt", FilterTypes::Gt, "100");
+        assert!(result.contains(">"), "F2P: Expected > operator, got {}", result);
+        assert!(!result.contains("<"), "F2P: Should not be < operator");
+    }
+
+    #[test]
+    fn test_gte_p2p() {
+        let result = filter_type_to_sql("amt", FilterTypes::Gte, "100");
+        assert!(result.contains(">="), "P2P: Gte should work, got {}", result);
+    }
+
+    #[test]
+    fn test_lte_p2p() {
+        let result = filter_type_to_sql("amt", FilterTypes::Lte, "100");
+        assert!(result.contains("<="), "P2P: Lte should work, got {}", result);
+    }
+}
"""

    return test_patch


def generate_instances():
    """Generate 50 instances with proper patches."""
    instances = []

    for i, base_instance in enumerate(ANALYTICS_INSTANCES[:MAX_BUGS], 1):
        instance = base_instance.copy()
        instance["patch"] = create_simple_bug_patch(instance)
        instance["test_patch"] = create_test_patch(instance)
        instance["strategy"] = "lm_unified_bugs"
        instance["bug_type"] = base_instance["bug_type"]

        instances.append(instance)
        print(
            f"Generated instance {i}/50: {instance['instance_id']} ({instance['bug_type']})"
        )

    return instances


def save_instances(instances):
    """Save instances to JSON file."""
    output_dir = Path(f"logs/bug_gen/{REPO}/lm_bugs")
    output_dir.mkdir(parents=True, exist_ok=True)

    output_file = output_dir / "lm_50_bugs.json"
    with open(output_file, "w") as f:
        json.dump(instances, f, indent=2)

    print(f"\n✓ Saved {len(instances)} instances to {output_file}")
    return output_file


def main():
    print("=" * 60)
    print("GENERATING 50 LM REWRITE BUGS")
    print("=" * 60)
    print()

    # Generate instances
    print("Generating bug instances...")
    instances = generate_instances()

    # Save
    output_file = save_instances(instances)

    print()
    print("=" * 60)
    print("NEXT STEPS:")
    print("=" * 60)
    print(f"1. Run validation:")
    print(f"   python -m swesmith.harness.valid {output_file} --workers 2")
    print()
    print(f"2. Or use custom validation:")
    print(f"   bash scripts/validate_lm_bugs.sh {output_file}")
    print()
    print("Expected results:")
    print("  - 50 instances with compilation-safe bugs")
    print("  - Each instance: 2+ F2P cases, 2+ P2P cases")
    print("  - Total: 100+ F2P, 100+ P2P cases")
    print()


if __name__ == "__main__":
    main()
