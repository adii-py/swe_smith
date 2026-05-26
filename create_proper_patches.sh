#!/bin/bash
# Create proper patches by modifying files and using git diff

set -e

cd /Users/aditya.singh.001/Desktop/SWE-smith/juspay__hyperswitch.fece9bc3

# Ensure we're at the right commit
git checkout fece9bc38b9890a1a40912ce2a95037842362e27

# ============ INSTANCE 1: validate_id bug ============
echo "Creating Instance 1: validate_id bug..."

# Apply the bug - change consts::MAX_ID_LENGTH to 100
sed -i '' 's/if id.len() > consts::MAX_ID_LENGTH {/if id.len() > 100 {/' crates/router/src/core/utils.rs

# Generate bug patch
git diff crates/router/src/core/utils.rs > /tmp/bug1.patch

# Restore original
git checkout crates/router/src/core/utils.rs

# Now apply test patch - add tests to existing test module
cat >> crates/router/src/core/utils.rs << 'TESTEOF'

#[cfg(test)]
mod validate_id_regression_tests {
    use super::*;

    #[test]
    fn test_validate_id_uses_max_id_length_constant() {
        // Source-code analysis test: verify validate_id uses MAX_ID_LENGTH constant
        // This test will fail if the bug is present (using hardcoded 100 instead)
        let utils_source = include_str!("utils.rs");
        assert!(
            utils_source.contains("if id.len() > consts::MAX_ID_LENGTH"),
            "validate_id should use consts::MAX_ID_LENGTH for validation, not hardcoded value"
        );
    }

    #[test]
    fn test_validate_id_rejects_65_char_id() {
        // Test that 65 character ID is rejected (MAX_ID_LENGTH is 64)
        let long_id = "a".repeat(65);
        let result = validate_id(long_id, "payment_id");
        assert!(result.is_err(), "65 char ID should be rejected when MAX_ID_LENGTH is 64");
    }
}
TESTEOF

# Generate test patch
git diff crates/router/src/core/utils.rs > /tmp/test1.patch

# Restore original
git checkout crates/router/src/core/utils.rs

echo "Instance 1 patches created:"
echo "  Bug patch: /tmp/bug1.patch"
echo "  Test patch: /tmp/test1.patch"

# ============ INSTANCE 2: constant change bug ============
echo ""
echo "Creating Instance 2: constant change bug..."

# Apply the bug - change MAX_ALLOWED_MERCHANT_NAME_LENGTH from 64 to 128
sed -i '' 's/pub const MAX_ALLOWED_MERCHANT_NAME_LENGTH: usize = 64;/pub const MAX_ALLOWED_MERCHANT_NAME_LENGTH: usize = 128;/' crates/common_utils/src/consts.rs

# Generate bug patch
git diff crates/common_utils/src/consts.rs > /tmp/bug2.patch

# Restore original
git checkout crates/common_utils/src/consts.rs

# Now add test to id_type.rs
cat >> crates/common_utils/src/id_type.rs << 'TESTEOF2'

#[cfg(test)]
mod merchant_name_length_regression {
    #[test]
    fn test_max_allowed_merchant_name_length_is_64() {
        // Direct constant value check - will fail if bug is present (value = 128)
        assert_eq!(crate::MAX_ALLOWED_MERCHANT_NAME_LENGTH, 64);
    }
}
TESTEOF2

# Generate test patch
git diff crates/common_utils/src/id_type.rs > /tmp/test2.patch

# Restore original
git checkout crates/common_utils/src/id_type.rs

echo "Instance 2 patches created:"
echo "  Bug patch: /tmp/bug2.patch"
echo "  Test patch: /tmp/test2.patch"

echo ""
echo "All patches created successfully!"
