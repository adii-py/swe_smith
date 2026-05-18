#!/usr/bin/env python3
"""
Generate F2P/P2P tests for the 2 PR mirror bugs using existing repo tests.
"""

import json
from pathlib import Path

# The 2 successful PR bugs
PR_BUGS = [
    {
        "instance_id": "juspay__hyperswitch.fece9bc3.pr_12008",
        "pr_number": 12008,
        "title": "feat(connector): [iMerchant Solutions] Retrieve Webhook_Object",
        "files_changed": [
            "crates/hyperswitch_connectors/src/connectors/imerchantsolutions.rs"
        ],
        "bug_patch_file": "logs/bug_gen/juspay__hyperswitch.fece9bc3/pr_mirror/juspay__hyperswitch.fece9bc3.pr_12008/bug__pr_12008.diff",
        "metadata_file": "logs/bug_gen/juspay__hyperswitch.fece9bc3/pr_mirror/juspay__hyperswitch.fece9bc3.pr_12008/metadata__pr_12008.json",
    },
    {
        "instance_id": "juspay__hyperswitch.fece9bc3.pr_12234",
        "pr_number": 12234,
        "title": "feat(core): Added profileId to payments client secret",
        "files_changed": [
            "crates/common_utils/src/id_type/payment.rs",
            "crates/common_utils/src/lib.rs"
        ],
        "bug_patch_file": "logs/bug_gen/juspay__hyperswitch.fece9bc3/pr_mirror/juspay__hyperswitch.fece9bc3.pr_12234/bug__pr_12234.diff",
        "metadata_file": "logs/bug_gen/juspay__hyperswitch.fece9bc3/pr_mirror/juspay__hyperswitch.fece9bc3.pr_12234/metadata__pr_12234.json",
    },
]


def generate_test_patch_pr_12008():
    """
    Generate test patch for pr_12008.
    The bug changes auth header building - removing merchant_id handling.
    """
    test_code = '''
#[cfg(test)]
mod pr_12008_tests {
    use super::*;

    #[test]
    fn test_auth_headers_with_merchant_id() {
        // This test should FAIL on buggy code (merchant_id removed)
        // and PASS on fixed code (merchant_id included)
        let auth = ImerchantsolutionsAuthType {
            api_key: "test_key".into(),
            merchant_id: Some("merchant_123".into()),
        };

        let headers = Imerchantsolutions.get_auth_header(&auth.into()).unwrap();

        // Check that merchant_id header is present in correct implementation
        let has_merchant_id = headers.iter().any(|(k, _)| k == "X-Merchant-Id");
        assert!(has_merchant_id, "Auth headers should include X-Merchant-Id when merchant_id is provided");
    }

    #[test]
    fn test_auth_headers_basic() {
        // Basic test - should work in both cases
        let auth = ImerchantsolutionsAuthType {
            api_key: "test_key".into(),
            merchant_id: None,
        };

        let headers = Imerchantsolutions.get_auth_header(&auth.into()).unwrap();
        assert!(!headers.is_empty(), "Auth headers should not be empty");
    }
}
'''

    test_patch = '''--- a/crates/hyperswitch_connectors/src/connectors/imerchantsolutions.rs
+++ b/crates/hyperswitch_connectors/src/connectors/imerchantsolutions.rs
@@ -650,3 +650,4 @@ impl ConnectorIntegration<RSync, RefundsData, RefundsResponseData> for Imerchant
         types::RefreshTokenRouterData,
         types::RevokeMandateRouterData,
     );
+{test_code}
}}'''.format(test_code=test_code)

    f2p_tests = [
        "hyperswitch_connectors::connectors::imerchantsolutions::pr_12008_tests::test_auth_headers_with_merchant_id",
    ]

    p2p_tests = [
        "hyperswitch_connectors::connectors::imerchantsolutions::pr_12008_tests::test_auth_headers_basic",
    ]

    return test_patch, f2p_tests, p2p_tests


def generate_test_patch_pr_12234():
    """
    Generate test patch for pr_12234.
    The bug removes profile_id parameter from generate_client_secret.
    """
    test_code = '''
#[cfg(test)]
mod pr_12234_tests {
    use super::*;

    #[test]
    fn test_generate_client_secret_with_profile() {
        // This test should FAIL on buggy code (no profile_id param)
        // and PASS on fixed code (profile_id included)
        let payment_id = PaymentId::try_from("pay_test123".to_string()).unwrap();
        let profile_id = "prof_test456";

        let secret = payment_id.generate_client_secret(profile_id);

        // Secret should contain profile reference
        assert!(secret.contains("profile="), "Client secret should include profile_id");
        assert!(secret.contains(profile_id), "Client secret should contain the profile_id value");
    }

    #[test]
    fn test_generate_client_secret_format() {
        // Basic format test - should work in both cases
        let payment_id = PaymentId::try_from("pay_test123".to_string()).unwrap();
        let secret = payment_id.generate_client_secret("prof_test");

        assert!(secret.starts_with("pay_test123_secret_"), "Secret should have correct prefix");
        assert!(secret.len() > 30, "Secret should be sufficiently long");
    }
}
'''

    test_patch = '''--- a/crates/common_utils/src/id_type/payment.rs
+++ b/crates/common_utils/src/id_type/payment.rs
@@ -115,3 +115,4 @@ impl std::str::FromStr for PaymentResourceId {
         Self::try_from(cow_string)
     }
 }}
+{test_code}
}}'''.format(test_code=test_code)

    f2p_tests = [
        "common_utils::id_type::payment::pr_12234_tests::test_generate_client_secret_with_profile",
    ]

    p2p_tests = [
        "common_utils::id_type::payment::pr_12234_tests::test_generate_client_secret_format",
    ]

    return test_patch, f2p_tests, p2p_tests


def main():
    print("=" * 80)
    print("GENERATING F2P/P2P TESTS FOR 2 PR MIRROR BUGS")
    print("=" * 80)

    output_dir = Path("logs/bug_gen/juspay__hyperswitch.fece9bc3/pr_mirror_validation")
    output_dir.mkdir(parents=True, exist_ok=True)

    instances = []

    for bug in PR_BUGS:
        instance_id = bug["instance_id"]
        print(f"\nProcessing {instance_id}...")

        # Read bug patch
        bug_patch = open(bug["bug_patch_file"]).read()

        # Generate test patch based on PR
        if bug["pr_number"] == 12008:
            test_patch, f2p, p2p = generate_test_patch_pr_12008()
        elif bug["pr_number"] == 12234:
            test_patch, f2p, p2p = generate_test_patch_pr_12234()
        else:
            continue

        # Create instance
        instance = {
            "instance_id": instance_id,
            "repo": "juspay__hyperswitch.fece9bc3",
            "patch": bug_patch,
            "test_patch": test_patch,
            "FAIL_TO_PASS": f2p,
            "PASS_TO_PASS": p2p,
            "base_commit": "fece9bc38b9890a1a40912ce2a95037842362e27",
        }
        instances.append(instance)

        # Save individual instance
        inst_file = output_dir / f"{instance_id}.json"
        with open(inst_file, "w") as f:
            json.dump(instance, f, indent=2)

        print(f"  ✅ Generated tests")
        print(f"     F2P: {len(f2p)} tests")
        print(f"     P2P: {len(p2p)} tests")

    # Save combined dataset
    dataset_file = output_dir / "2pr_bugs_dataset.json"
    with open(dataset_file, "w") as f:
        json.dump(instances, f, indent=2)

    print()
    print("=" * 80)
    print("COMPLETED")
    print("=" * 80)
    print(f"Instances: {len(instances)}")
    print(f"Output: {dataset_file}")
    print()
    print("Next: Run validation")


if __name__ == "__main__":
    main()
