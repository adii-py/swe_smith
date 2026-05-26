#!/usr/bin/env python3
"""
Validate PR mirror bugs using UNIT TESTS ONLY (--lib flag)
Skips integration tests that require external dependencies (redis, postgres)
"""

import json
import subprocess
import os
from pathlib import Path


def update_test_commands_to_unit_tests_only():
    """Update all test commands to use --lib for unit tests only."""

    # Load the cleaned dataset
    input_file = Path(
        "logs/bug_gen/juspay__hyperswitch.fece9bc3/pr_mirror/recovered_dataset_clean.json"
    )

    with open(input_file, "r") as f:
        instances = json.load(f)

    print(f"Loaded {len(instances)} instances")
    print()

    updated_instances = []

    for inst in instances:
        # Update test command to use --lib for unit tests only
        original_cmd = inst.get("test_cmd", "")

        # Replace --no-fail-fast with --lib for unit tests
        if "cargo test" in original_cmd:
            # Extract the package name
            parts = original_cmd.split()
            if "-p" in parts:
                pkg_idx = parts.index("-p")
                if pkg_idx + 1 < len(parts):
                    pkg_name = parts[pkg_idx + 1]
                    # Create new command with --lib flag
                    new_cmd = f"CARGO_BUILD_JOBS=1 cargo test -p {pkg_name} --lib --no-fail-fast -- --nocapture"
                    inst["test_cmd"] = new_cmd
                    inst["test_cmd_original"] = original_cmd

        updated_instances.append(inst)

    # Save updated dataset
    output_file = Path(
        "logs/bug_gen/juspay__hyperswitch.fece9bc3/pr_mirror/pr_mirror_unit_tests.json"
    )
    with open(output_file, "w") as f:
        json.dump(updated_instances, f, indent=2)

    print(f"✓ Updated {len(updated_instances)} instances")
    print(f"✓ Saved to: {output_file}")
    print()

    # Show sample
    print("Sample updated test commands:")
    for i, inst in enumerate(updated_instances[:3]):
        print(f"  {i + 1}. {inst['instance_id']}")
        print(f"     Original: {inst.get('test_cmd_original', 'N/A')[:60]}...")
        print(f"     Updated:  {inst['test_cmd'][:60]}...")
        print()

    return output_file


def add_test_patches_for_analytics(instances):
    """Add test patches for analytics instances to detect bugs."""

    test_patch_template = """diff --git a/crates/analytics/src/query.rs b/crates/analytics/src/query.rs
index 0000000..2222222 100644
--- a/crates/analytics/src/query.rs
+++ b/crates/analytics/src/query.rs
@@ -993,0 +994,30 @@
+#[cfg(test)]
+mod pr_mirror_validation_tests {
+    use super::*;
+
+    #[test]
+    fn test_filter_equal_operator() {
+        // F2P: Tests Equal operator behavior
+        let result = filter_type_to_sql("id", FilterTypes::Equal, "123");
+        assert!(result.contains(" = "), "Expected = operator, got: {}", result);
+    }
+
+    #[test]
+    fn test_filter_not_equal_operator() {
+        // P2P: Tests NotEqual operator (should work before and after)
+        let result = filter_type_to_sql("id", FilterTypes::NotEqual, "123");
+        assert!(result.contains("!="), "Expected != operator, got: {}", result);
+    }
+
+    #[test]
+    fn test_filter_in_operator() {
+        // Tests IN operator
+        let result = filter_type_to_sql("status", FilterTypes::In, "'a','b'");
+        assert!(result.contains(" IN "), "Expected IN operator, got: {}", result);
+    }
+
+    #[test]
+    fn test_filter_gt_operator() {
+        // P2P: Tests Gt operator
+        let result = filter_type_to_sql("amount", FilterTypes::Gt, "100");
+        assert!(result.contains(">"), "Expected > operator, got: {}", result);
+    }
+}
"""

    enhanced_instances = []

    for inst in instances:
        # Check if this is an analytics instance
        if "analytics" in inst.get("test_cmd", "").lower():
            inst["test_patch"] = test_patch_template
            inst["has_test_patch"] = True

        enhanced_instances.append(inst)

    return enhanced_instances


def run_validation(dataset_file, workers=2):
    """Run validation on the dataset."""

    print("=" * 60)
    print("RUNNING VALIDATION")
    print("=" * 60)
    print()

    # Use Docker to run validation
    cmd = [
        "docker",
        "run",
        "--rm",
        "-v",
        f"{Path.cwd()}/logs:/workspace/logs",
        "-v",
        f"{Path.cwd()}/swesmith:/workspace/swesmith",
        "-v",
        "/var/run/docker.sock:/var/run/docker.sock",
        "-e",
        "PYTHONUNBUFFERED=1",
        "swesmith-validation:latest",
        "bash",
        "-c",
        f"cd /workspace && python3 -m swesmith.harness.valid {dataset_file} --workers {workers} --redo_existing",
    ]

    print("Command:")
    print(" ".join(cmd))
    print()
    print("Running validation (this may take 1-2 hours)...")
    print()

    result = subprocess.run(cmd, capture_output=True, text=True)

    print("STDOUT:")
    print(result.stdout)
    print()
    print("STDERR:")
    print(result.stderr)
    print()
    print(f"Return code: {result.returncode}")

    return result.returncode == 0


def check_results():
    """Check validation results."""

    results_dir = Path("logs/run_validation/juspay__hyperswitch.fece9bc3")

    if not results_dir.exists():
        print("No results directory found")
        return

    reports_found = 0
    f2p_total = 0
    p2p_total = 0
    instances_with_f2p = 0

    for report_file in results_dir.rglob("report.json"):
        reports_found += 1
        try:
            with open(report_file) as f:
                report = json.load(f)

            f2p = len(report.get("FAIL_TO_PASS", []))
            p2p = len(report.get("PASS_TO_PASS", []))

            f2p_total += f2p
            p2p_total += p2p

            if f2p > 0:
                instances_with_f2p += 1

        except Exception as e:
            print(f"Error reading {report_file}: {e}")

    print("=" * 60)
    print("VALIDATION RESULTS SUMMARY")
    print("=" * 60)
    print()
    print(f"Total instances processed: {reports_found}")
    print(f"Instances with F2P cases: {instances_with_f2p}")
    print(f"Total F2P cases: {f2p_total}")
    print(f"Total P2P cases: {p2p_total}")
    print()

    if reports_found > 0:
        success_rate = (instances_with_f2p / reports_found) * 100
        print(f"Success rate: {success_rate:.1f}%")
        print()

        if instances_with_f2p > 0:
            avg_f2p = f2p_total / instances_with_f2p
            avg_p2p = p2p_total / reports_found
            print(f"Average F2P per instance: {avg_f2p:.1f}")
            print(f"Average P2P per instance: {avg_p2p:.1f}")

    print()
    print("=" * 60)


def main():
    print("=" * 60)
    print("PR MIRROR VALIDATION - UNIT TESTS ONLY")
    print("=" * 60)
    print()

    # Step 1: Update test commands
    print("Step 1: Updating test commands to use --lib...")
    dataset_file = update_test_commands_to_unit_tests_only()

    # Step 2: Load and enhance dataset
    print("Step 2: Loading dataset and adding test patches...")
    with open(dataset_file, "r") as f:
        instances = json.load(f)

    enhanced_instances = add_test_patches_for_analytics(instances)

    # Save enhanced dataset
    enhanced_file = Path(
        "logs/bug_gen/juspay__hyperswitch.fece9bc3/pr_mirror/pr_mirror_enhanced.json"
    )
    with open(enhanced_file, "w") as f:
        json.dump(enhanced_instances, f, indent=2)

    print(f"✓ Enhanced dataset saved to: {enhanced_file}")
    print(f"✓ Total instances: {len(enhanced_instances)}")
    print()

    # Step 3: Run validation
    print("Step 3: Running validation...")
    print()

    success = run_validation(
        "logs/bug_gen/juspay__hyperswitch.fece9bc3/pr_mirror/pr_mirror_enhanced.json",
        workers=2,
    )

    # Step 4: Check results
    print()
    print("Step 4: Checking results...")
    check_results()

    if success:
        print("✓ Validation completed successfully!")
    else:
        print("⚠ Validation completed with some errors")
        print("Check logs for details")


if __name__ == "__main__":
    main()
