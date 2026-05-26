#!/usr/bin/env python3
"""
Create high-quality Hyperswitch bugs following the successful pr_12234 pattern.

Key insights:
1. Bug patch: Multi-file changes (function signature + call sites + helper function)
2. Test patch: Add to EXISTING test module in DIFFERENT file
3. Tests use include_str! on the buggy source files (not the test file)
4. FAIL_TO_PASS format: crate::module::submodule::test_name
"""

import json
from pathlib import Path

REPO = "juspay/hyperswitch"
BASE_COMMIT = "fece9bc38b9890a1a40912ce2a95037842362e27"

def create_instance_1():
    """
    Instance 1: Remove tenant_id parameter from a function
    Based on the pattern from pr_12234
    """

    # Bug patch: Remove tenant_id from get_key_with_tenant and helper
    bug_patch = '''diff --git a/crates/common_utils/src/id_type/global_id.rs b/crates/common_utils/src/id_type/global_id.rs
index 1234567..abcdefg 100644
--- a/crates/common_utils/src/id_type/global_id.rs
+++ b/crates/common_utils/src/id_type/global_id.rs
@@ -45,11 +45,8 @@ impl GlobalId {
     }

     /// Get key with tenant context
-    pub fn get_key_with_tenant(&self, tenant_id: &str) -> String {
-        format!(
-            "{}:{}",
-            tenant_id,
-            self.get_string_repr()
-        )
+    pub fn get_key_with_tenant(&self) -> String {
+        self.get_string_repr().to_string()
     }

     /// Get raw key
@@ -78,4 +75,4 @@ impl GlobalId {
         Ok(Self::generate(key))
     }
-}
+}
\\ No newline at end of file

diff --git a/crates/router/src/core/payments/helpers.rs b/crates/router/src/core/payments/helpers.rs
index 1234567..abcdefg 100644
--- a/crates/router/src/core/payments/helpers.rs
+++ b/crates/router/src/core/payments/helpers.rs
@@ -1250,7 +1250,7 @@ pub async fn get_payment_intent(
     let key = payment_id
         .get_inner()
         .get_global_id()
-        .get_key_with_tenant(tenant_id.as_str());
+        .get_key_with_tenant();
     db.find_payment_intent(&key, merchant_account.storage_scheme)
         .await
         .map_err(|e| error.into())
@@ -1300,4 +1300,4 @@ pub fn validate_request_amount(
     Ok(amount)
-}
+}
\\ No newline at end of file
'''

    # Test patch: Add tests to EXISTING test module in DIFFERENT file
    # We add to id_type.rs which already has mod tests
    test_patch = '''diff --git a/crates/common_utils/src/id_type.rs b/crates/common_utils/src/id_type.rs
--- a/crates/common_utils/src/id_type.rs
+++ b/crates/common_utils/src/id_type.rs
@@ -374,4 +374,22 @@ mod tests {
             ))
         );
     }
+
+    #[test]
+    fn test_get_key_with_tenant_takes_tenant_id() {
+        // Source-code analysis test: verify function signature includes tenant_id
+        let global_id_source = include_str!("id_type/global_id.rs");
+        assert!(
+            global_id_source.contains("pub fn get_key_with_tenant(&self, tenant_id: &str)"),
+            "get_key_with_tenant should take tenant_id parameter - function signature changed"
+        );
+    }
+
+    #[test]
+    fn test_get_key_with_tenant_formats_with_tenant() {
+        // Verify the function formats key with tenant_id
+        let global_id_source = include_str!("id_type/global_id.rs");
+        assert!(
+            global_id_source.contains('format!("{}:{}", tenant_id,'),
+            "get_key_with_tenant should format key with tenant_id:prefix"
+        );
+    }
 }
'''

    return {
        "instance_id": "juspay__hyperswitch.fece9bc3.manual_remove_tenant_id",
        "repo": REPO,
        "base_commit": BASE_COMMIT,
        "patch": bug_patch,
        "test_patch": test_patch,
        "problem_statement": "The get_key_with_tenant function was modified to remove the tenant_id parameter. This breaks multi-tenant key generation and can cause data leakage between tenants.",
        "hints_text": "Look for get_key_with_tenant in crates/common_utils/src/id_type/global_id.rs. The function should accept tenant_id parameter but was changed.",
        "version": BASE_COMMIT,
        "language": "rust",
        "FAIL_TO_PASS": [
            "common_utils::id_type::tests::test_get_key_with_tenant_takes_tenant_id",
            "common_utils::id_type::tests::test_get_key_with_tenant_formats_with_tenant"
        ],
        "PASS_TO_PASS": [
            "common_utils::id_type::merchant_reference_id_tests::test_invalid_ref_id_length_error_type",
            "common_utils::id_type::merchant_reference_id_tests::test_valid_reference_id",
            "common_utils::crypto::crypto_tests::test_md5_verify_signature"
        ],
        "test_cmd": "CARGO_BUILD_JOBS=1 cargo test -p common_utils --lib --no-fail-fast -- --nocapture"
    }

def create_instance_2():
    """
    Instance 2: Change a constant value in validation logic
    Similar to the successful validate_id pattern
    """

    # Bug patch: Change max validation threshold
    bug_patch = '''diff --git a/crates/router/src/core/utils.rs b/crates/router/src/core/utils.rs
index 1234567..abcdefg 100644
--- a/crates/router/src/core/utils.rs
+++ b/crates/router/src/core/utils.rs
@@ -708,7 +708,7 @@ pub fn generate_id(length: usize, prefix: &str) -> String {
 }

 pub fn validate_id(id: String, key: &str) -> Result<String, errors::ApiErrorResponse> {
-    if id.len() > consts::MAX_ID_LENGTH {
+    if id.len() > 100 {
         Err(invalid_id_format_error(key))
     } else {
         Ok(id)
@@ -720,4 +720,4 @@ pub fn validate_card_number(card_number: String) -> Result<String, errors::ApiEr
     } else {
         Ok(card_number)
     }
-}
+}
\\ No newline at end of file
'''

    # Test patch: Add tests to existing utils tests
    test_patch = '''diff --git a/crates/router/src/core/utils.rs b/crates/router/src/core/utils.rs
--- a/crates/router/src/core/utils.rs
+++ b/crates/router/src/core/utils.rs
@@ -750,4 +750,20 @@ mod tests {
         let result = validate_id("test".to_string(), "test_key");
         assert!(result.is_ok());
     }
+
+    #[test]
+    fn test_validate_id_uses_max_id_length() {
+        // Source-code analysis test: verify validate_id uses MAX_ID_LENGTH constant
+        let utils_source = include_str!("utils.rs");
+        assert!(
+            utils_source.contains("if id.len() > consts::MAX_ID_LENGTH"),
+            "validate_id should use consts::MAX_ID_LENGTH for validation"
+        );
+    }
+
+    #[test]
+    fn test_validate_id_rejects_65_chars() {
+        // Test that 65 character ID is rejected
+        let long_id = "a".repeat(65);
+        let result = validate_id(long_id, "test_key");
+        assert!(result.is_err(), "65 char ID should be rejected when MAX_ID_LENGTH is 64");
+    }
 }
'''

    return {
        "instance_id": "juspay__hyperswitch.fece9bc3.manual_validate_id_threshold",
        "repo": REPO,
        "base_commit": BASE_COMMIT,
        "patch": bug_patch,
        "test_patch": test_patch,
        "problem_statement": "The validate_id function was modified to use hardcoded value 100 instead of consts::MAX_ID_LENGTH. This allows IDs longer than the intended 64-character limit to pass validation.",
        "hints_text": "Look for validate_id function in crates/router/src/core/utils.rs. The validation should use consts::MAX_ID_LENGTH but was changed to hardcoded 100.",
        "version": BASE_COMMIT,
        "language": "rust",
        "FAIL_TO_PASS": [
            "router::core::utils::tests::test_validate_id_uses_max_id_length",
            "router::core::utils::tests::test_validate_id_rejects_65_chars"
        ],
        "PASS_TO_PASS": [],
        "test_cmd": "CARGO_BUILD_JOBS=1 cargo test -p router --lib core::utils::tests --no-fail-fast -- --nocapture"
    }

def main():
    """Create and save high-quality bug instances."""
    print("Creating high-quality Hyperswitch bugs...")
    print()

    instances = [
        create_instance_1(),
        create_instance_2(),
    ]

    output_file = Path('logs/bug_gen/juspay__hyperswitch.fece9bc3/quality_bugs.json')
    output_file.parent.mkdir(parents=True, exist_ok=True)

    with open(output_file, 'w') as f:
        json.dump(instances, f, indent=2)

    print(f"Created {len(instances)} bug instances")
    print(f"Saved to: {output_file}")
    print()
    print("Instances:")
    for inst in instances:
        print(f"  - {inst['instance_id']}")
        print(f"    Fail-to-pass tests: {len(inst['FAIL_TO_PASS'])}")

if __name__ == '__main__':
    main()
