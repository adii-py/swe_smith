#!/usr/bin/env python3
"""
Create REAL working Hyperswitch bugs based on actual code analysis.
"""

import json
from pathlib import Path

REPO = "juspay/hyperswitch"
BASE_COMMIT = "fece9bc38b9890a1a40912ce2a95037842362e27"

def create_validate_id_bug():
    """
    Create a real bug that changes consts::MAX_ID_LENGTH to 100 in validate_id function.
    Based on actual code at crates/router/src/core/utils.rs line 730.
    """

    # Real bug patch - change consts::MAX_ID_LENGTH to 100
    bug_patch = '''diff --git a/crates/router/src/core/utils.rs b/crates/router/src/core/utils.rs
index abc123..def456 100644
--- a/crates/router/src/core/utils.rs
+++ b/crates/router/src/core/utils.rs
@@ -728,7 +728,7 @@ pub fn generate_id(length: usize, prefix: &str) -> String {
 }

 pub fn validate_id(id: String, key: &str) -> Result<String, errors::ApiErrorResponse> {
-    if id.len() > consts::MAX_ID_LENGTH {
+    if id.len() > 100 {
         Err(invalid_id_format_error(key))
     } else {
         Ok(id)
'''

    # Test patch - add tests to EXISTING test module in the SAME file
    # Looking at line 941 where tests exist
    test_patch = '''diff --git a/crates/router/src/core/utils.rs b/crates/router/src/core/utils.rs
--- a/crates/router/src/core/utils.rs
+++ b/crates/router/src/core/utils.rs
@@ -955,4 +955,22 @@ mod tests {
         let result = validate_id(payment_id.clone(), "payment_id");
         assert!(result.is_ok());
     }
+
+    #[test]
+    fn test_validate_id_uses_max_id_length_constant() {
+        // Source-code analysis test: verify validate_id uses MAX_ID_LENGTH constant
+        // This test will fail if the bug is present (using hardcoded 100 instead)
+        let utils_source = include_str!("../utils.rs");
+        assert!(
+            utils_source.contains("if id.len() > consts::MAX_ID_LENGTH"),
+            "validate_id should use consts::MAX_ID_LENGTH for validation, not hardcoded value"
+        );
+    }
+
+    #[test]
+    fn test_validate_id_rejects_65_char_id() {
+        // Test that 65 character ID is rejected (MAX_ID_LENGTH is 64)
+        let long_id = "a".repeat(65);
+        let result = validate_id(long_id, "payment_id");
+        assert!(result.is_err(), "65 char ID should be rejected when MAX_ID_LENGTH is 64");
+    }
 }
'''

    return {
        "instance_id": "juspay__hyperswitch.fece9bc3.validate_id_max_length",
        "repo": REPO,
        "base_commit": BASE_COMMIT,
        "patch": bug_patch,
        "test_patch": test_patch,
        "problem_statement": "The validate_id function in crates/router/src/core/utils.rs has a bug where the maximum ID length validation was incorrectly changed from consts::MAX_ID_LENGTH (64) to 100. This allows IDs longer than the intended 64-character limit to pass validation.",
        "hints_text": "Look for the validate_id function in crates/router/src/core/utils.rs around line 730. The validation checks id.len() against a threshold value.",
        "version": BASE_COMMIT,
        "language": "rust",
        "FAIL_TO_PASS": [
            "router::core::utils::tests::test_validate_id_uses_max_id_length_constant",
            "router::core::utils::tests::test_validate_id_rejects_65_char_id"
        ],
        "PASS_TO_PASS": [
            "router::core::utils::tests::validate_id_length_constraint",
            "router::core::utils::tests::validate_id_proper_response",
            "router::core::utils::tests::test_generate_id"
        ],
        "test_cmd": "CARGO_BUILD_JOBS=1 cargo test --release -p router --lib core::utils::tests --no-fail-fast -- --nocapture"
    }

def create_constant_change_bug():
    """
    Create a simple constant change bug in common_utils consts.rs
    Based on the earlier successful pattern.
    """

    # Bug patch - change MAX_ALLOWED_MERCHANT_NAME_LENGTH from 64 to 128
    bug_patch = '''diff --git a/crates/common_utils/src/consts.rs b/crates/common_utils/src/consts.rs
index abc123..def456 100644
--- a/crates/common_utils/src/consts.rs
+++ b/crates/common_utils/src/consts.rs
@@ -125,7 +125,7 @@ pub const WILDCARD_DOMAIN_REGEX: &str = r"^((\\*|https?)?://)?((\\*\\.|[A-Za-z0-9][-A-Za-z0-9]*\\.)*[A-Za-z0-9][-A-Za-z0-9]*|((\\d{1,3}|\\*)\\.){3}(\\d{1,3}|\\*)|\\*)(:\\*|:[0-9]{2,4})?(/\\*)?$";

 /// Maximum allowed length for MerchantName
-pub const MAX_ALLOWED_MERCHANT_NAME_LENGTH: usize = 64;
+pub const MAX_ALLOWED_MERCHANT_NAME_LENGTH: usize = 128;

 /// Maximum allowed length for CardIssuerName
 pub const MAX_ALLOWED_CARD_ISSUER_NAME_LENGTH: usize = 255;
'''

    # Test patch - add to id_type.rs which imports consts
    test_patch = '''diff --git a/crates/common_utils/src/id_type.rs b/crates/common_utils/src/id_type.rs
--- a/crates/common_utils/src/id_type.rs
+++ b/crates/common_utils/src/id_type.rs
@@ -374,4 +374,16 @@ mod tests {
             ))
         );
     }
+
+    /// Test that MAX_ALLOWED_MERCHANT_NAME_LENGTH has correct value (64)
+    /// Uses const evaluation to verify the constant value
+    #[test]
+    fn test_max_merchant_name_length_value() {
+        // The actual constant value
+        const EXPECTED_LENGTH: usize = 64;
+        // Compare with the actual constant - this will fail at compile time if different
+        // But we use assert_eq for runtime check
+        let actual = super::super::MAX_ALLOWED_MERCHANT_NAME_LENGTH;
+        assert_eq!(actual, EXPECTED_LENGTH, "MAX_ALLOWED_MERCHANT_NAME_LENGTH should be 64 but was {}", actual);
+    }
 }
'''

    return {
        "instance_id": "juspay__hyperswitch.fece9bc3.max_merchant_name_length",
        "repo": REPO,
        "base_commit": BASE_COMMIT,
        "patch": bug_patch,
        "test_patch": test_patch,
        "problem_statement": "The MAX_ALLOWED_MERCHANT_NAME_LENGTH constant in crates/common_utils/src/consts.rs was incorrectly changed from 64 to 128. This affects validation logic for merchant names.",
        "hints_text": "Look for MAX_ALLOWED_MERCHANT_NAME_LENGTH in crates/common_utils/src/consts.rs around line 128. The value should be 64.",
        "version": BASE_COMMIT,
        "language": "rust",
        "FAIL_TO_PASS": [
            "common_utils::id_type::tests::test_max_merchant_name_length_value"
        ],
        "PASS_TO_PASS": [],
        "test_cmd": "CARGO_BUILD_JOBS=1 cargo test --release -p common_utils --lib id_type::tests --no-fail-fast -- --nocapture"
    }

def main():
    print("Creating REAL working Hyperswitch bugs...")
    print()

    instances = [
        create_validate_id_bug(),
        create_constant_change_bug(),
    ]

    output_file = Path('logs/bug_gen/juspay__hyperswitch.fece9bc3/real_bugs.json')
    output_file.parent.mkdir(parents=True, exist_ok=True)

    with open(output_file, 'w') as f:
        json.dump(instances, f, indent=2)

    print(f"Created {len(instances)} REAL bug instances")
    print(f"Saved to: {output_file}")
    print()
    print("Instances:")
    for inst in instances:
        print(f"  - {inst['instance_id']}")
        print(f"    F2P tests: {len(inst['FAIL_TO_PASS'])}")
        print(f"    P2P tests: {len(inst['PASS_TO_PASS'])}")

if __name__ == '__main__':
    main()
