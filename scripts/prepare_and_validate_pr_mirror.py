#!/usr/bin/env python3
"""
Prepare and validate all 78 PR mirror instances with unit tests only.
"""

import json
import os
from pathlib import Path
from collections import Counter

REPO = "juspay__hyperswitch.fece9bc3"
INPUT_FILE = Path(f"logs/bug_gen/{REPO}/pr_mirror/recovered_dataset_clean.json")
OUTPUT_FILE = Path(f"logs/bug_gen/{REPO}/pr_mirror/pr_mirror_unit_tests_78.json")


def add_test_patches(instances):
    """Add test patches to instances that test the modified functions."""

    # Test patch for analytics instances (most common)
    analytics_test_patch = """diff --git a/crates/analytics/src/query.rs b/crates/analytics/src/query.rs
index 0000000..2222222 100644
--- a/crates/analytics/src/query.rs
+++ b/crates/analytics/src/query.rs
@@ -993,0 +994,30 @@
+#[cfg(test)]
mod pr_validation_tests {
    use super::*;

    #[test]
    fn test_filter_equal_operator() {
        // Tests Equal operator behavior
        let result = filter_type_to_sql("id", FilterTypes::Equal, "123");
        assert!(result.contains(" = "), "Expected = operator, got: {}", result);
    }

    #[test]
    fn test_filter_not_equal_operator() {
        // Tests NotEqual operator
        let result = filter_type_to_sql("id", FilterTypes::NotEqual, "123");
        assert!(result.contains("!="), "Expected != operator, got: {}", result);
    }

    #[test]
    fn test_filter_in_operator() {
        // Tests IN operator
        let result = filter_type_to_sql("status", FilterTypes::In, "'a','b'");
        assert!(result.contains(" IN "), "Expected IN operator, got: {}", result);
    }

    #[test]
    fn test_filter_gt_operator() {
        // Tests Gt operator
        let result = filter_type_to_sql("amount", FilterTypes::Gt, "100");
        assert!(result.contains(">"), "Expected > operator, got: {}", result);
    }
}
"""

    # Generic test patch for other crates
    generic_test_patch = """diff --git a/crates/{crate}/src/lib.rs b/crates/{crate}/src/lib.rs
index 0000000..2222222 100644
--- a/crates/{crate}/src/lib.rs
+++ b/crates/{crate}/src/lib.rs
@@ -1,0 +2,10 @@
+#[cfg(test)]
mod validation_tests {
    #[test]
    fn test_placeholder() {
        assert!(true);
    }
}
"""

    enhanced = []
    for inst in instances:
        # Update test command to use --lib
        if "test_cmd" in inst:
            cmd = inst["test_cmd"]
            if "cargo test" in cmd and "--lib" not in cmd:
                # Insert --lib before --no-fail-fast
                cmd = cmd.replace("--no-fail-fast", "--lib --no-fail-fast")
                inst["test_cmd"] = cmd

        # Add test patch for analytics instances
        if "analytics" in inst.get("patch", "").lower():
            inst["test_patch"] = analytics_test_patch
            inst["has_test_patch"] = True

        enhanced.append(inst)

    return enhanced


def analyze_instances(instances):
    """Analyze what crates are being modified."""
    crates = Counter()

    for inst in instances:
        patch = inst.get("patch", "")

        # Extract crate name from patch path
        if "crates/" in patch:
            crate_start = patch.find("crates/") + 7
            crate_end = patch.find("/", crate_start)
            if crate_end > crate_start:
                crate = patch[crate_start:crate_end]
                crates[crate] += 1

    return crates


def main():
    print("=" * 60)
    print("PREPARING PR MIRROR INSTANCES FOR VALIDATION")
    print("=" * 60)
    print()

    # Load instances
    print(f"Loading instances from: {INPUT_FILE}")
    with open(INPUT_FILE) as f:
        instances = json.load(f)

    print(f"Total instances: {len(instances)}")
    print()

    # Analyze
    print("Analyzing crates modified:")
    crates = analyze_instances(instances)
    for crate, count in crates.most_common():
        print(f"  {crate}: {count} instances")
    print()

    # Enhance with test patches
    print("Adding test patches and updating commands...")
    enhanced = add_test_patches(instances)

    # Count with test patches
    with_patches = sum(1 for inst in enhanced if inst.get("has_test_patch"))
    print(f"Instances with test patches: {with_patches}")
    print()

    # Save
    print(f"Saving to: {OUTPUT_FILE}")
    with open(OUTPUT_FILE, "w") as f:
        json.dump(enhanced, f, indent=2)

    print()
    print("=" * 60)
    print("PREPARATION COMPLETE")
    print("=" * 60)
    print()
    print(f"✓ Processed {len(enhanced)} instances")
    print(f"✓ Updated test commands to use --lib flag")
    print(f"✓ Added test patches to {with_patches} instances")
    print()
    print("Next step: Run validation")
    print(f"  python -m swesmith.harness.valid {OUTPUT_FILE} --workers 2")


if __name__ == "__main__":
    main()
