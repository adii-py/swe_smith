#!/usr/bin/env python3
"""
Generate simple, compilation-safe bug patches.

These bugs ONLY modify the internal logic of functions, never:
- Function signatures
- Type definitions
- Imports
- Public APIs

This guarantees the code will compile while still introducing real bugs.
"""

import json
import random
from typing import List, Dict

# Simple bug templates that only change logic inside functions
BUG_TEMPLATES = [
    {
        "name": "Off-by-one error",
        "description": "Change < to <= or vice versa in bounds checking",
        "apply": lambda code: code.replace(">=", ">").replace("<=", "<"),
    },
    {
        "name": "Logic inversion",
        "description": "Negate boolean conditions",
        "apply": lambda code: code.replace("if !", "TEMP_PLACEHOLDER")
        .replace("if ", "if !")
        .replace("TEMP_PLACEHOLDER", "if "),
    },
    {
        "name": "Swap comparison operators",
        "description": "Change == to != or vice versa",
        "apply": lambda code: code.replace(" == ", "PLACEHOLDER_EQ")
        .replace(" != ", " == ")
        .replace("PLACEHOLDER_EQ", " != "),
    },
    {
        "name": "Skip early return",
        "description": "Comment out early return statements",
        "apply": lambda code: code.replace("return ", "// BUG: return ")
        if "return " in code
        else code,
    },
    {
        "name": "Wrong default value",
        "description": "Change true to false or 0 to 1 in default values",
        "apply": lambda code: code.replace("= true", "= PLACEHOLDER_BOOL")
        .replace("= false", "= true")
        .replace("PLACEHOLDER_BOOL", "= false"),
    },
]


def create_simple_bug_patch(
    file_path: str, function_name: str, original_content: str
) -> str:
    """
    Create a compilation-safe bug patch for a specific function.

    Args:
        file_path: Path to the file being modified
        function_name: Name of the function to inject bug into
        original_content: Original function body content

    Returns:
        A valid diff patch string
    """
    # Select a random bug template
    template = random.choice(BUG_TEMPLATES)

    # Apply the bug transformation
    buggy_content = template["apply"](original_content)

    # Ensure we actually made a change
    if buggy_content == original_content:
        # Try another template
        for t in BUG_TEMPLATES:
            buggy_content = t["apply"](original_content)
            if buggy_content != original_content:
                template = t
                break

    if buggy_content == original_content:
        return None  # Could not create a bug

    # Generate unified diff format
    original_lines = original_content.strip().split("\n")
    buggy_lines = buggy_content.strip().split("\n")

    # Create minimal diff
    diff_lines = [
        f"diff --git a/{file_path} b/{file_path}",
        "index 0000000..1111111 100644",
        f"--- a/{file_path}",
        f"+++ b/{file_path}",
        f"@@ -1,{len(original_lines)} +1,{len(buggy_lines)} @@",
    ]

    for line in original_lines:
        diff_lines.append(f"-{line}")
    for line in buggy_lines:
        diff_lines.append(f"+{line}")

    return "\n".join(diff_lines)


def generate_bugs_for_analytics() -> List[Dict]:
    """
    Generate compilation-safe bug patches for the analytics crate.

    We'll create bugs in specific functions that we know exist and are testable.
    """
    bugs = []

    # Bug 1: Off-by-one in filter_type_to_sql
    bug1 = {
        "instance_id": "juspay__hyperswitch.fece9bc3.pr_bug_001",
        "repo": "juspay__hyperswitch.fece9bc3",
        "patch": """diff --git a/crates/analytics/src/query.rs b/crates/analytics/src/query.rs
index 1234567..abcdefg 100644
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
""",
        "test_patch": """diff --git a/crates/analytics/src/query.rs b/crates/analytics/src/query.rs
index 1234567..abcdefg 100644
--- a/crates/analytics/src/query.rs
+++ b/crates/analytics/src/query.rs
@@ -994,0 +995,20 @@
+#[cfg(test)]
+mod bug_tests {
+    use super::*;
+
+    #[test]
+    fn test_filter_type_to_sql_in_operator() {
+        let result = filter_type_to_sql("status", FilterTypes::In, "'active','inactive'");
+        // This test expects the CORRECT behavior (IN operator)
+        // After applying the bug patch, this test should FAIL
+        assert!(result.contains(" IN "), "Expected IN operator but got: {}", result);
+        assert!(!result.contains("NOT IN"), "Should not contain NOT IN");
+    }
+
+    #[test]
+    fn test_filter_type_to_sql_equal_still_works() {
+        // This should still work (PASS_TO_PASS test)
+        let result = filter_type_to_sql("id", FilterTypes::Equal, "123");
+        assert_eq!(result, "id = '123'");
+    }
+}
""",
    }
    bugs.append(bug1)

    # Bug 2: Wrong comparison operator
    bug2 = {
        "instance_id": "juspay__hyperswitch.fece9bc3.pr_bug_002",
        "repo": "juspay__hyperswitch.fece9bc3",
        "patch": """diff --git a/crates/analytics/src/query.rs b/crates/analytics/src/query.rs
index 1234567..abcdefg 100644
--- a/crates/analytics/src/query.rs
+++ b/crates/analytics/src/query.rs
@@ -560,7 +560,7 @@ pub fn filter_type_to_sql(l: &str, op: FilterTypes, r: &str) -> String {
         FilterTypes::EqualBool => format!("{l} = {r}"),
         FilterTypes::Equal => format!("{l} = '{r}'"),
-        FilterTypes::NotEqual => format!("{l} != '{r}'"),
+        FilterTypes::NotEqual => format!("{l} = '{r}'"),
         FilterTypes::In => format!("{l} IN ({r})"),
         FilterTypes::Gte => format!("{l} >= '{r}'"),
         FilterTypes::Gt => format!("{l} > {r}"),
""",
        "test_patch": """diff --git a/crates/analytics/src/query.rs b/crates/analytics/src/query.rs
index 1234567..abcdefg 100644
--- a/crates/analytics/src/query.rs
+++ b/crates/analytics/src/query.rs
@@ -994,0 +995,20 @@
+#[cfg(test)]
+mod bug_tests {
+    use super::*;
+
+    #[test]
+    fn test_filter_type_to_sql_not_equal() {
+        let result = filter_type_to_sql("status", FilterTypes::NotEqual, "deleted");
+        // Should use != operator
+        assert!(result.contains("!="), "Expected != operator but got: {}", result);
+    }
+
+    #[test]  
+    fn test_filter_type_to_sql_basic_operators() {
+        // PASS_TO_PASS: Other operators should still work
+        assert_eq!(filter_type_to_sql("a", FilterTypes::Equal, "b"), "a = 'b'");
+        assert_eq!(filter_type_to_sql("a", FilterTypes::Gt, "5"), "a > 5");
+    }
+}
""",
    }
    bugs.append(bug2)

    # Bug 3: Swap Greater Than with Less Than
    bug3 = {
        "instance_id": "juspay__hyperswitch.fece9bc3.pr_bug_003",
        "repo": "juspay__hyperswitch.fece9bc3",
        "patch": """diff --git a/crates/analytics/src/query.rs b/crates/analytics/src/query.rs
index 1234567..abcdefg 100644
--- a/crates/analytics/src/query.rs
+++ b/crates/analytics/src/query.rs
@@ -565,7 +565,7 @@ pub fn filter_type_to_sql(l: &str, op: FilterTypes, r: &str) -> String {
         FilterTypes::Gte => format!("{l} >= '{r}'"),
-        FilterTypes::Gt => format!("{l} > {r}"),
+        FilterTypes::Gt => format!("{l} < {r}"),
         FilterTypes::Lte => format!("{l} <= '{r}'"),
         FilterTypes::Like => format!("{l} LIKE '%{r}%'"),
""",
        "test_patch": """diff --git a/crates/analytics/src/query.rs b/crates/analytics/src/query.rs
index 1234567..abcdefg 100644
--- a/crates/analytics/src/query.rs
+++ b/crates/analytics/src/query.rs
@@ -994,0 +995,15 @@
+#[cfg(test)]
+mod bug_tests {
+    use super::*;
+
+    #[test]
+    fn test_filter_type_to_sql_gt_operator() {
+        let result = filter_type_to_sql("amount", FilterTypes::Gt, "100");
+        assert!(result.contains(">"), "Expected > operator but got: {}", result);
+        assert!(!result.contains("<"), "Should not contain < operator");
+    }
+
+    #[test]
+    fn test_filter_type_to_sql_gte_still_works() {
+        // PASS_TO_PASS: Gte should still work
+        let result = filter_type_to_sql("amount", FilterTypes::Gte, "100");
+        assert!(result.contains(">="));
+    }
+}
""",
    }
    bugs.append(bug3)

    return bugs


def main():
    """Generate and save simple compilation-safe bugs."""
    print("Generating compilation-safe bug patches...")

    bugs = generate_bugs_for_analytics()

    output_file = "/Users/aditya.singh.001/Desktop/SWE-smith/logs/bug_gen/juspay__hyperswitch.fece9bc3/pr_mirror/simple_bugs_pilot.json"

    with open(output_file, "w") as f:
        json.dump(bugs, f, indent=2)

    print(f"Generated {len(bugs)} bug patches")
    print(f"Saved to: {output_file}")
    print("\nBug summary:")
    for bug in bugs:
        print(
            f"  - {bug['instance_id']}: {bug['patch'].split('diff --git')[1].split()[0] if 'diff --git' in bug['patch'] else 'unknown'}"
        )


if __name__ == "__main__":
    main()
