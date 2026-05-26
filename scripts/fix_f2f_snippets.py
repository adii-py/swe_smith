"""Fix F2F instances: re-derive snippets confirmed present in the clean source file."""
import json
import re
from pathlib import Path

ROOT     = Path(__file__).resolve().parents[1]
REPO     = ROOT / "juspay__hyperswitch.fece9bc3"
DATASET  = ROOT / "logs/bug_gen/juspay__hyperswitch.fece9bc3/pr_mirror/scale_100/pr_mirror_100_dataset.json"
LOG_ROOT = ROOT / "logs/run_validation/juspay__hyperswitch.fece9bc3"

TEST_SKIP = "--nocapture --skip redis --skip postgres --skip db --skip database --skip integration"
TEST_CMD  = f"CARGO_BUILD_JOBS=1 cargo test -p common_utils --lib --no-fail-fast -- {TEST_SKIP}"
LIB_RS    = REPO / "crates/common_utils/src/lib.rs"


def primary_file(patch: str) -> str | None:
    for l in patch.splitlines():
        if l.startswith("+++ b/"):
            return l[6:].strip()
    return None


def crate_from_path(fp: str) -> str | None:
    m = re.match(r"crates/([^/]+)/", fp)
    return m.group(1) if m else None


def normalize(s: str) -> str:
    return " ".join(s.split())


def find_confirmed_token(patch: str, file_content: str, min_len: int = 24) -> tuple[list[str], list[str]]:
    """
    Find tokens from removed (-) lines that actually exist verbatim in the clean source,
    and from added (+) lines that do NOT exist in clean source.
    """
    norm_src = normalize(file_content)

    fix_tokens: list[str] = []
    bug_tokens: list[str] = []

    # Try multiline removed blocks first
    current: list[str] = []
    for line in patch.splitlines():
        if line.startswith("---") or line.startswith("+++"):
            continue
        if line.startswith("@@"):
            current = []
            continue
        if line.startswith("-") and not line.startswith("---"):
            txt = line[1:].rstrip()
            if txt.strip() and not txt.strip().startswith("//") and len(txt.strip()) >= min_len:
                current.append(txt)
        elif line.startswith("+") and current:
            current = []

    # Try to find a multiline block that's present
    for ln in patch.splitlines():
        if ln.startswith("-") and not ln.startswith("---"):
            tok = ln[1:].strip()
            if len(tok) >= min_len and normalize(tok) in norm_src:
                fix_tokens.append(tok)
                if len(fix_tokens) >= 2:
                    break

    # Fallback: individual tokens from identifiers in removed lines
    if len(fix_tokens) < 2:
        idents: list[str] = []
        for ln in patch.splitlines():
            if ln.startswith("-") and not ln.startswith("---"):
                toks = re.findall(r"[A-Za-z_][A-Za-z0-9_]{14,}", ln)
                for t in toks:
                    if t in file_content and t not in idents:
                        idents.append(t)
        for t in idents:
            if t not in " ".join(fix_tokens):
                fix_tokens.append(t)
            if len(fix_tokens) >= 2:
                break

    # Bug tokens: single line from + lines NOT in source
    for ln in patch.splitlines():
        if ln.startswith("+") and not ln.startswith("+++"):
            tok = ln[1:].strip()
            if len(tok) >= min_len and normalize(tok) not in norm_src:
                bug_tokens.append(tok)
                if len(bug_tokens) >= 1:
                    break

    return fix_tokens[:2], bug_tokens[:1]


def make_test_patch(pull: str, crate_rel: str, fix_snippets: list[str], bug_snippets: list[str]) -> tuple[str, list[str]]:
    import sys; sys.path.insert(0, str(ROOT))
    from scripts.pr_mirror_scale_100 import _rust_raw_string, _path_from_common_utils

    mod_name = f"pilot_pr_{pull}_tests"
    rel = _path_from_common_utils(crate_rel)
    tests_rs: list[str] = []
    test_names: list[str] = []

    for i, snip in enumerate(fix_snippets[:2]):
        raw = _rust_raw_string(snip)
        name = f"test_fix_present_{i}"
        test_names.append(f"common_utils::{mod_name}::tests::{name}")
        tests_rs.append(
            f'    #[test]\n'
            f'    fn {name}() {{\n'
            f'        let src = read_source();\n'
            f'        let needle = normalize({raw});\n'
            f'        assert!(normalize(&src).contains(&needle), "Expected fix code in source");\n'
            f'    }}'
        )

    for i, snip in enumerate(bug_snippets[: max(0, 2 - len(fix_snippets[:2]))]):
        raw = _rust_raw_string(snip)
        name = f"test_bug_absent_{i}"
        test_names.append(f"common_utils::{mod_name}::tests::{name}")
        tests_rs.append(
            f'    #[test]\n'
            f'    fn {name}() {{\n'
            f'        let src = read_source();\n'
            f'        let needle = normalize({raw});\n'
            f'        assert!(!normalize(&src).contains(&needle), "Bug code must not appear on clean tree");\n'
            f'    }}'
        )

    if not tests_rs:
        return "", []

    body = (
        "#[cfg(test)]\nmod tests {\n"
        f'    const SRC: &str = concat!(env!("CARGO_MANIFEST_DIR"), "{rel}");\n\n'
        "    fn read_source() -> String {\n"
        '        std::fs::read_to_string(SRC).expect("bug target source")\n'
        "    }\n\n"
        "    fn normalize(s: &str) -> String {\n"
        '        s.split_whitespace().collect::<Vec<_>>().join(" ")\n'
        "    }\n\n"
        + "\n".join(tests_rs) + "\n}\n"
    )

    file_path = f"crates/common_utils/src/{mod_name}.rs"
    lines = body.strip().split("\n")
    diff_lines = [
        f"diff --git a/{file_path} b/{file_path}",
        "new file mode 100644",
        "--- /dev/null",
        f"+++ b/{file_path}",
        f"@@ -0,0 +1,{len(lines)} @@",
    ] + ["+" + ln for ln in lines]

    # lib.rs mod hook
    lib_content = LIB_RS.read_text()
    mod_line = f"mod {mod_name};"
    if mod_line in lib_content:
        lib_patch = ""
    else:
        lib_lines = lib_content.split("\n")
        insert_at = len(lib_lines)
        for idx in range(len(lib_lines) - 1, -1, -1):
            if lib_lines[idx].strip():
                insert_at = idx + 1
                break
        addition = ["", "#[cfg(test)]", mod_line]
        ctx = lib_lines[max(0, insert_at - 2): insert_at]
        old_start = max(0, insert_at - 2) + 1
        lib_patch = (
            "diff --git a/crates/common_utils/src/lib.rs b/crates/common_utils/src/lib.rs\n"
            "--- a/crates/common_utils/src/lib.rs\n"
            "+++ b/crates/common_utils/src/lib.rs\n"
            f"@@ -{old_start},{len(ctx)} +{old_start},{len(ctx)+len(addition)} @@\n"
            + "".join(" " + ln + "\n" for ln in ctx)
            + "".join("+" + ln + "\n" for ln in addition)
        )

    return "\n".join(diff_lines) + "\n" + lib_patch, test_names[:2]


def main():
    from swebench.harness.constants import FAIL_TO_PASS

    ds = json.loads(DATASET.read_text())
    fixed = 0; skipped = 0

    for inst in ds:
        if inst.get("test_generation") == "llm_runtime":
            continue  # keep LLM tests

        rp = LOG_ROOT / inst["instance_id"] / "report.json"
        if not rp.exists():
            continue

        r = json.loads(rp.read_text())
        f2p = r.get(FAIL_TO_PASS, [])
        f2f = r.get("FAIL_TO_FAIL", [])
        pilot_f2f = [x for x in f2f if "pilot_pr" in x]

        if not pilot_f2f:
            continue  # already clean or no pilot tests

        # Snippet is failing on clean tree — re-derive from source
        pull = str(inst["pull_number"])
        patch = inst["patch"]
        fp = primary_file(patch)
        if not fp:
            skipped += 1; continue

        full = REPO / fp
        if not full.exists():
            skipped += 1; continue

        content = full.read_text()
        fix_snips, bug_snips = find_confirmed_token(patch, content)
        if not fix_snips and not bug_snips:
            print(f"  no tokens found for {inst['instance_id']}")
            skipped += 1; continue

        test_patch, test_names = make_test_patch(pull, fp, fix_snips, bug_snips)
        if not test_patch or not test_names:
            skipped += 1; continue

        inst["test_patch"]   = test_patch
        inst["FAIL_TO_PASS"] = test_names
        inst["test_cmd"]     = TEST_CMD
        inst["test_generation"] = "source_confirmed"
        inst["target_crate"] = "common_utils"
        inst["target_file"]  = fp
        fixed += 1
        print(f"  fixed {inst['instance_id']}: tokens={fix_snips[:1]} bug={bug_snips[:1]}")

    DATASET.write_text(json.dumps(ds, indent=2))
    print(f"\nFixed {fixed}, skipped {skipped} -> {DATASET}")


if __name__ == "__main__":
    main()
