#!/usr/bin/env python3
"""Create final instances JSON by reading actual patch files."""

import json

# Read the patch files
with open('/tmp/bug1.patch') as f:
    bug1 = f.read()

with open('/tmp/test1.patch') as f:
    test1 = f.read()

with open('/tmp/bug2.patch') as f:
    bug2 = f.read()

with open('/tmp/test2.patch') as f:
    test2 = f.read()

instances = [
    {
        "instance_id": "juspay__hyperswitch.fece9bc3.validate_id_max_length",
        "repo": "juspay/hyperswitch",
        "base_commit": "fece9bc38b9890a1a40912ce2a95037842362e27",
        "version": "fece9bc38b9890a1a40912ce2a95037842362e27",
        "language": "rust",
        "patch": bug1,
        "test_patch": test1,
        "problem_statement": "The validate_id function in crates/router/src/core/utils.rs has a bug where the maximum ID length validation was incorrectly changed from consts::MAX_ID_LENGTH (64) to 100. This allows IDs longer than the intended 64-character limit to pass validation, which can cause downstream issues with database storage and API compatibility. Fix the validation logic to reject IDs longer than 64 characters.",
        "hints_text": "Look for the validate_id function in crates/router/src/core/utils.rs around line 730. The validation logic checks id.len() against a threshold value.",
        "FAIL_TO_PASS": [
            "router::core::utils::validate_id_regression_tests::test_validate_id_uses_max_id_length_constant",
            "router::core::utils::validate_id_regression_tests::test_validate_id_rejects_65_char_id"
        ],
        "PASS_TO_PASS": [
            "router::core::utils::tests::test_generate_id",
            "router::core::utils::tests::test_filter_objects_based_on_profile_id_list"
        ],
        "test_cmd": "CARGO_BUILD_JOBS=1 cargo test --release -p router --lib --no-fail-fast -- --nocapture"
    },
    {
        "instance_id": "juspay__hyperswitch.fece9bc3.max_merchant_name_length",
        "repo": "juspay/hyperswitch",
        "base_commit": "fece9bc38b9890a1a40912ce2a95037842362e27",
        "version": "fece9bc38b9890a1a40912ce2a95037842362e27",
        "language": "rust",
        "patch": bug2,
        "test_patch": test2,
        "problem_statement": "The MAX_ALLOWED_MERCHANT_NAME_LENGTH constant in crates/common_utils/src/consts.rs was incorrectly changed from 64 to 128. This affects validation logic for merchant names and can allow overly long merchant names to be accepted, causing issues with database constraints.",
        "hints_text": "Look for MAX_ALLOWED_MERCHANT_NAME_LENGTH in crates/common_utils/src/consts.rs around line 128. The value should be 64 but may have been changed.",
        "FAIL_TO_PASS": [
            "common_utils::id_type::merchant_name_length_regression::test_max_allowed_merchant_name_length_is_64"
        ],
        "PASS_TO_PASS": [
            "common_utils::id_type::merchant_reference_id_tests::test_valid_reference_id"
        ],
        "test_cmd": "CARGO_BUILD_JOBS=1 cargo test --release -p common_utils --lib --no-fail-fast -- --nocapture"
    }
]

with open('final_instances.json', 'w') as f:
    json.dump(instances, f, indent=2)

print(f"Created final_instances.json with {len(instances)} instances")
print("Patches loaded from actual files:")
print(f"  - bug1.patch: {len(bug1)} chars")
print(f"  - test1.patch: {len(test1)} chars")
print(f"  - bug2.patch: {len(bug2)} chars")
print(f"  - test2.patch: {len(test2)} chars")
