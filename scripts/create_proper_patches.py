#!/usr/bin/env python3
"""
Create properly formatted patches with exact context.
"""

import json

# The exact line numbers from query.rs:
# Line 560:         FilterTypes::In => format!("{l} IN ({r})"),
# Line 558:         FilterTypes::Equal => format!("{l} = '{r}'"),
# Line 563:         FilterTypes::Gte => format!("{l} >= '{r}'"),
# Line 564:         FilterTypes::Gt => format!("{l} > {r}"),

# Create patches with minimal context (just 1 line before and after)
patches = [
    {
        "instance_id": "juspay__hyperswitch.fece9bc3.pr_12317",
        "repo": "juspay__hyperswitch.fece9bc3",
        "patch": """diff --git a/crates/analytics/src/query.rs b/crates/analytics/src/query.rs
index 0000000..1111111 100644
--- a/crates/analytics/src/query.rs
+++ b/crates/analytics/src/query.rs
@@ -559,3 +559,3 @@ pub fn filter_type_to_sql(l: &str, op: FilterTypes, r: &str) -> String {
         FilterTypes::NotEqual => format!("{l} != '{r}'"),
-        FilterTypes::In => format!("{l} IN ({r})"),
+        FilterTypes::In => format!("{l} NOT IN ({r})"),
         FilterTypes::Gte => format!("{l} >= '{r}'"),
""",
        "test_patch": """diff --git a/crates/analytics/src/query.rs b/crates/analytics/src/query.rs
index 0000000..2222222 100644
--- a/crates/analytics/src/query.rs
+++ b/crates/analytics/src/query.rs
@@ -993,0 +994,20 @@
+#[cfg(test)]
+mod f2p_tests {
+    use super::*;
+
+    #[test]
+    fn test_in_f2p() {
+        let result = filter_type_to_sql("col", FilterTypes::In, "1,2");
+        assert!(result.contains(" IN "), "Got: {}", result);
+    }
+
+    #[test]
+    fn test_equal_p2p() {
+        let result = filter_type_to_sql("id", FilterTypes::Equal, "123");
+        assert_eq!(result, "id = '123'");
+    }
+}
""",
    },
    {
        "instance_id": "juspay__hyperswitch.fece9bc3.pr_12315",
        "repo": "juspay__hyperswitch.fece9bc3",
        "patch": """diff --git a/crates/analytics/src/query.rs b/crates/analytics/src/query.rs
index 0000000..1111111 100644
--- a/crates/analytics/src/query.rs
+++ b/crates/analytics/src/query.rs
@@ -557,3 +557,3 @@ pub fn filter_type_to_sql(l: &str, op: FilterTypes, r: &str) -> String {
     match op {
-        FilterTypes::Equal => format!("{l} = '{r}'"),
+        FilterTypes::Equal => format!("{l} != '{r}'"),
         FilterTypes::NotEqual => format!("{l} != '{r}'"),
""",
        "test_patch": """diff --git a/crates/analytics/src/query.rs b/crates/analytics/src/query.rs
index 0000000..2222222 100644
--- a/crates/analytics/src/query.rs
+++ b/crates/analytics/src/query.rs
@@ -993,0 +994,20 @@
+#[cfg(test)]
+mod f2p_tests {
+    use super::*;
+
+    #[test]
+    fn test_equal_f2p() {
+        let result = filter_type_to_sql("id", FilterTypes::Equal, "123");
+        assert_eq!(result, "id = '123'");
+    }
+
+    #[test]
+    fn test_in_p2p() {
+        let result = filter_type_to_sql("col", FilterTypes::In, "1,2");
+        assert!(result.contains(" IN "));
+    }
+}
""",
    },
    {
        "instance_id": "juspay__hyperswitch.fece9bc3.pr_12316",
        "repo": "juspay__hyperswitch.fece9bc3",
        "patch": """diff --git a/crates/analytics/src/query.rs b/crates/analytics/src/query.rs
index 0000000..1111111 100644
--- a/crates/analytics/src/query.rs
+++ b/crates/analytics/src/query.rs
@@ -562,3 +562,3 @@ pub fn filter_type_to_sql(l: &str, op: FilterTypes, r: &str) -> String {
         FilterTypes::Gte => format!("{l} >= '{r}'"),
-        FilterTypes::Gt => format!("{l} > {r}"),
+        FilterTypes::Gt => format!("{l} < {r}"),
         FilterTypes::Lte => format!("{l} <= '{r}'"),
""",
        "test_patch": """diff --git a/crates/analytics/src/query.rs b/crates/analytics/src/query.rs
index 0000000..2222222 100644
--- a/crates/analytics/src/query.rs
+++ b/crates/analytics/src/query.rs
@@ -993,0 +994,15 @@
+#[cfg(test)]
+mod f2p_tests {
+    use super::*;
+
+    #[test]
+    fn test_gt_f2p() {
+        let result = filter_type_to_sql("amt", FilterTypes::Gt, "100");
+        assert!(result.contains(">"), "Got: {}", result);
+    }
+
+    #[test]
+    fn test_gte_p2p() {
+        let result = filter_type_to_sql("amt", FilterTypes::Gte, "100");
+        assert!(result.contains(">="));
+    }
+}
""",
    },
]

# Save
with open(
    "logs/bug_gen/juspay__hyperswitch.fece9bc3/pr_mirror/proper_patches.json", "w"
) as f:
    json.dump(patches, f, indent=2)

print(f"Created {len(patches)} proper patches")
print("Saved to: proper_patches.json")
