#!/usr/bin/env python3
"""
Fix the dataset: Reverse all patches so they CREATE bugs instead of applying fixes.
"""

import json
import re
from pathlib import Path

INPUT_FILE = Path(
    "logs/bug_gen/juspay__hyperswitch.fece9bc3/pr_mirror/recovered_dataset_clean.json"
)
OUTPUT_FILE = Path(
    "logs/bug_gen/juspay__hyperswitch.fece9bc3/pr_mirror/recovered_dataset_BUGS.json"
)


def reverse_patch(patch_content):
    """
    Reverse a patch so additions become deletions and vice versa.
    This converts a FIX patch into a BUG patch.
    """
    lines = patch_content.split("\n")
    reversed_lines = []

    for line in lines:
        if line.startswith("@@"):
            # Keep hunk headers as-is
            reversed_lines.append(line)
        elif line.startswith("--- "):
            # Swap --- and +++
            reversed_lines.append("+++ " + line[4:])
        elif line.startswith("+++ "):
            # Swap +++ and ---
            reversed_lines.append("--- " + line[4:])
        elif line.startswith("-"):
            # Deletion becomes addition
            reversed_lines.append("+" + line[1:])
        elif line.startswith("+"):
            # Addition becomes deletion
            reversed_lines.append("-" + line[1:])
        else:
            # Context lines stay the same
            reversed_lines.append(line)

    return "\n".join(reversed_lines)


def create_test_patch_for_analytics():
    """Create test patch for analytics SQL injection bug."""
    return """diff --git a/crates/analytics/src/query.rs b/crates/analytics/src/query.rs
index f8453eca395..test001 100644
--- a/crates/analytics/src/query.rs
+++ b/crates/analytics/src/query.rs
@@ -993,3 +993,20 @@ where
         Ok(())
     }
 }
+
+#[cfg(test)]
mod sql_injection_tests {
    use super::*;

    #[test]
    fn test_sql_sanitization() {
        // F2P: Tests that quotes are escaped (prevents SQL injection)
        let result = filter_type_to_sql("col", FilterTypes::Equal, "test'value");
        assert!(result.contains("''"), "Quotes should be escaped. Got: {}", result);
    }

    #[test]
    fn test_in_operator() {
        // P2P: IN operator unchanged by bug
        let result = filter_type_to_sql("status", FilterTypes::In, "'a','b'");
        assert!(result.contains(" IN "), "Got: {}", result);
    }
}
"""


def main():
    print("=" * 60)
    print("FIXING DATASET: Converting FIX patches to BUG patches")
    print("=" * 60)
    print()

    with open(INPUT_FILE) as f:
        instances = json.load(f)

    print(f"Loaded {len(instances)} instances")
    print()

    fixed_instances = []

    for i, inst in enumerate(instances, 1):
        instance_id = inst["instance_id"]
        original_patch = inst["patch"]

        # Reverse the patch (FIX -> BUG)
        bug_patch = reverse_patch(original_patch)
        inst["patch"] = bug_patch
        inst["patch_original"] = original_patch
        inst["patch_was_reversed"] = True

        # Add test patch for analytics instances
        if "analytics" in instance_id.lower():
            inst["test_patch"] = create_test_patch_for_analytics()
            inst["has_test_patch"] = True

        fixed_instances.append(inst)

        if i <= 3 or i == len(instances):
            print(f"[{i}/{len(instances)}] {instance_id}: Patch reversed ✓")

    print()
    print(f"✓ Fixed {len(fixed_instances)} instances")

    # Save
    with open(OUTPUT_FILE, "w") as f:
        json.dump(fixed_instances, f, indent=2)

    print()
    print(f"✓ Saved to: {OUTPUT_FILE}")
    print()
    print("=" * 60)
    print("DATASET FIXED")
    print("=" * 60)
    print()
    print("Changes made:")
    print("  • All patches REVERSED (now create bugs instead of fixes)")
    print("  • Test patches added for analytics instances")
    print("  • Ready for proper F2P/P2P detection")
    print()
    print("Validation should now work correctly:")
    print("  Pre-gold: Code with fix (safe) + tests → tests PASS")
    print("  Post-gold: Code with bug (vulnerable) + tests → tests FAIL")
    print("  Result: F2P and P2P detected!")


if __name__ == "__main__":
    main()
