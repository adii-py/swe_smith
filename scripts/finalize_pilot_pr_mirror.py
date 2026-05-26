#!/usr/bin/env python3
"""Finalize pilot PR-mirror instances to match hyperswitch_instance.json (pr_12234) quality."""
import json
from pathlib import Path

from swesmith.bug_gen.patch_inverter import invert_unified_diff, validate_patch_applies

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT / "juspay__hyperswitch.fece9bc3"
REPO_PROFILE = "juspay__hyperswitch.fece9bc3"
BASE = "fece9bc38b9890a1a40912ce2a95037842362e27"
# Full common_utils lib (pr_12234 pattern) for F2P + P2P; skip external integration tests
TEST_CMD = (
    "CARGO_BUILD_JOBS=1 cargo test -p common_utils --lib --no-fail-fast "
    "-- --nocapture --skip redis --skip postgres --skip db --skip database --skip integration"
)
OUT = ROOT / "logs/bug_gen" / REPO_PROFILE / "pilot_2x2" / "pilot_instances.json"
BATCH = ROOT / "logs/bug_gen" / REPO_PROFILE / "pr_mirror" / "batch1_small.jsonl"
LIB_RS = "crates/common_utils/src/lib.rs"


def strip_cosmetic_eof_hunk(patch: str) -> str:
    """Remove trailing newline-only hunks that break git apply in Docker."""
    for marker in ("@@ -3496,4 +3497,4 @@", "@@ -3496,4 +3496,4 @@"):
        if marker in patch:
            patch = patch[: patch.index(marker)].rstrip() + "\n"
    return patch


def fix_mollie_indent(patch: str) -> str:
    """Fix known LLM recovery corruption on Connector::Mollie line."""
    return patch.replace(
        "(\nConnector::Mollie,",
        "(\n            Connector::Mollie,",
    )


def _read_workspace_file(rel_path: str) -> str:
    path = REPO / rel_path
    return path.read_text()


def _make_new_file_diff(file_path: str, content: str) -> str:
    lines = content.strip().split("\n")
    diff = [
        f"diff --git a/{file_path} b/{file_path}",
        "new file mode 100644",
        "--- /dev/null",
        f"+++ b/{file_path}",
        f"@@ -0,0 +1,{len(lines)} @@",
    ]
    diff.extend("+" + ln for ln in lines)
    return "\n".join(diff) + "\n"


def _append_lib_test_mod(mod_name: str) -> str:
    content = _read_workspace_file(LIB_RS)
    needle = f"mod {mod_name};"
    if needle in content:
        return ""
    lines = content.split("\n")
    insert_at = len(lines)
    for i in range(len(lines) - 1, -1, -1):
        if lines[i].strip():
            insert_at = i + 1
            break
    addition = ["", "#[cfg(test)]", f"mod {mod_name};"]
    context_start = max(0, insert_at - 2)
    before = lines[context_start:insert_at]
    old_start = context_start + 1
    old_count = len(before)
    new_count = old_count + len(addition)
    diff = [
        f"diff --git a/{LIB_RS} b/{LIB_RS}",
        f"--- a/{LIB_RS}",
        f"+++ b/{LIB_RS}",
        f"@@ -{old_start},{old_count} +{old_start},{new_count} @@",
    ]
    for ln in before:
        diff.append(" " + ln)
    for ln in addition:
        diff.append("+" + ln)
    return "\n".join(diff) + "\n"


def create_test_patch_12167() -> tuple[str, list[str]]:
    """Source-analysis tests in common_utils (Docker-validated pattern)."""
    mod_name = "pilot_pr_12167_tests"
    file_path = f"crates/common_utils/src/{mod_name}.rs"
    body = r'''#[cfg(test)]
mod tests {
    const WORLDPAY: &str = concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/../hyperswitch_connectors/src/connectors/worldpayxml/transformers.rs"
    );

    fn read_source() -> String {
        std::fs::read_to_string(WORLDPAY).expect("worldpayxml transformers source")
    }

    #[test]
    fn test_autocapture_serializes_off_as_uppercase() {
        let src = read_source();
        let idx = src.find("enum AutoCapture").expect("AutoCapture enum");
        let header = &src[idx.saturating_sub(120)..idx];
        assert!(
            header.contains("#[serde(rename_all = \"UPPERCASE\")]"),
            "AutoCapture enum should use UPPERCASE serde rename, got: {header}"
        );
    }

    #[test]
    fn test_auto_capture_authorised_returns_charged() {
        let src = read_source();
        assert!(
            src.contains(
                "if is_auto_capture {\n                Ok(common_enums::AttemptStatus::Charged)"
            ),
            "Authorised + auto_capture should return Charged"
        );
    }
}
'''
    f2p = [
        f"common_utils::{mod_name}::tests::test_autocapture_serializes_off_as_uppercase",
        f"common_utils::{mod_name}::tests::test_auto_capture_authorised_returns_charged",
    ]
    patch = _make_new_file_diff(file_path, body) + _append_lib_test_mod(mod_name)
    return patch, f2p


def create_test_patch_12191() -> tuple[str, list[str]]:
    mod_name = "pilot_pr_12191_tests"
    file_path = f"crates/common_utils/src/{mod_name}.rs"
    body = r'''#[cfg(test)]
mod tests {
    const PM_FIELDS: &str = concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/../payment_methods/src/configs/payment_connector_required_fields.rs"
    );

    fn read_source() -> String {
        std::fs::read_to_string(PM_FIELDS).expect("payment_connector_required_fields source")
    }

    fn truelayer_block<'a>(src: &'a str) -> &'a str {
        let start = src.find("Connector::Truelayer").expect("Truelayer connector");
        &src[start..start.saturating_add(600)]
    }

    fn trustly_block<'a>(src: &'a str) -> &'a str {
        let start = src.find("Connector::Trustly,").expect("Trustly connector");
        &src[start..start.saturating_add(800)]
    }

    #[test]
    fn test_billing_first_name_field_used_in_truelayer_block() {
        let src = read_source();
        let block = truelayer_block(&src);
        assert!(
            block.contains("RequiredField::BillingFirstName("),
            "Truelayer block should require BillingFirstName"
        );
    }

    #[test]
    fn test_billing_last_name_field_used_in_trustly_block() {
        let src = read_source();
        let block = trustly_block(&src);
        assert!(
            block.contains("RequiredField::BillingLastName("),
            "Trustly block should require BillingLastName"
        );
    }
}
'''
    f2p = [
        f"common_utils::{mod_name}::tests::test_billing_first_name_field_used_in_truelayer_block",
        f"common_utils::{mod_name}::tests::test_billing_last_name_field_used_in_trustly_block",
    ]
    patch = _make_new_file_diff(file_path, body) + _append_lib_test_mod(mod_name)
    return patch, f2p


def load_bug_from_batch(pull: int) -> str:
    for line in open(BATCH):
        inst = json.loads(line)
        if inst["pull_number"] == pull:
            return invert_unified_diff(inst["patch"])
    raise ValueError(f"PR {pull} not in batch")


def main():
    bug_12167 = strip_cosmetic_eof_hunk(
        (ROOT / "logs/bug_gen" / REPO_PROFILE / "pr_mirror"
         / "juspay__hyperswitch.fece9bc3.pr_12167" / "bug__pr_12167.diff").read_text()
    )
    bug_12191 = fix_mollie_indent(load_bug_from_batch(12191))

    for name, patch in [("12167", bug_12167), ("12191", bug_12191)]:
        r = validate_patch_applies(REPO, patch)
        print(f"PR {name} apply_check: {r.success}")
        if not r.success:
            print(f"  {r.message[:300]}")

    tp67, f2p67 = create_test_patch_12167()
    tp91, f2p91 = create_test_patch_12191()

    instances = [
        {
            "instance_id": f"{REPO_PROFILE}.pr_12167",
            "repo": REPO_PROFILE,
            "base_commit": BASE,
            "patch": bug_12167,
            "test_patch": tp67,
            "FAIL_TO_PASS": f2p67,
            "PASS_TO_PASS": [],
            "test_cmd": TEST_CMD,
            "problem_statement": (
                "Auto-capture payment status and serde casing regression in worldpayxml connector"
            ),
            "language": "rust",
            "bug_type": "pr_mirror",
        },
        {
            "instance_id": f"{REPO_PROFILE}.pr_12191",
            "repo": REPO_PROFILE,
            "base_commit": BASE,
            "patch": bug_12191,
            "test_patch": tp91,
            "FAIL_TO_PASS": f2p91,
            "PASS_TO_PASS": [],
            "test_cmd": TEST_CMD_TEMPLATE.format(filter="pilot_pr_12191"),
            "problem_statement": (
                "Bank redirect required billing name fields reverted to legacy enum variants"
            ),
            "language": "rust",
            "bug_type": "pr_mirror",
        },
    ]

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(instances, indent=2))
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    main()
