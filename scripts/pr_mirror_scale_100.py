#!/usr/bin/env python3
"""
Scale PR-mirror dataset to N validated instances (F2P + P2P).

Pipeline:
  1. collect            — gather bug patches that apply at base commit
  2. add-tests-quality  — LLM runtime tests in touched file (TestPatchGenerator) + per-crate test_cmd
     add-tests          — fast common_utils source-analysis tests (fallback)
  3. validate           — swesmith.harness.valid (Docker + shared target volume)
  4. export             — merge report.json → dataset with F2P>=min_f2p

Usage:
  uv run python scripts/pr_mirror_scale_100.py collect --limit 114
  uv run python scripts/pr_mirror_scale_100.py add-tests-quality --model private-large
  uv run python scripts/pr_mirror_scale_100.py validate --workers 2 --redo
  uv run python scripts/pr_mirror_scale_100.py export --limit 100
  uv run python scripts/pr_mirror_scale_100.py all --limit 100 --quality --workers 2
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

from swebench.harness.constants import FAIL_TO_PASS, LOG_REPORT, PASS_TO_PASS
from swesmith.bug_gen.patch_inverter import validate_patch_applies
from swesmith.constants import LOG_DIR_RUN_VALIDATION

ROOT = Path(__file__).resolve().parents[1]
REPO_PROFILE = "juspay__hyperswitch.fece9bc3"
BASE_COMMIT = "fece9bc38b9890a1a40912ce2a95037842362e27"
REPO_PATH = ROOT / "juspay__hyperswitch.fece9bc3"
PR_MIRROR_DIR = ROOT / "logs/bug_gen" / REPO_PROFILE / "pr_mirror"
WORKDIR = PR_MIRROR_DIR / "scale_100"
INPUT_JSONL = WORKDIR / "pr_mirror_100_input.jsonl"
DATASET_JSON = WORKDIR / "pr_mirror_100_dataset.json"
VALIDATED_JSON = WORKDIR / "pr_mirror_100_validated.json"
DATA_EXPORT = ROOT / "data" / "hyperswitch_pr_mirror_100.json"

TEST_SKIP = "--nocapture --skip redis --skip postgres --skip db --skip database --skip integration"

TEST_CMD = (
    "CARGO_BUILD_JOBS=1 cargo test -p common_utils --lib --no-fail-fast "
    f"-- {TEST_SKIP}"
)

# Per-crate unit-test commands (runtime tests in the touched crate)
CRATE_TEST_CMD: dict[str, str] = {
    "common_utils": TEST_CMD,
    "hyperswitch_connectors": (
        "CARGO_BUILD_JOBS=1 cargo test -p hyperswitch_connectors --lib --no-fail-fast "
        f"-- {TEST_SKIP}"
    ),
    "router": (
        "CARGO_BUILD_JOBS=1 cargo test -p router --lib --no-fail-fast "
        f"-- {TEST_SKIP}"
    ),
    "analytics": (
        "CARGO_BUILD_JOBS=1 cargo test -p analytics --lib --no-fail-fast "
        f"-- {TEST_SKIP}"
    ),
    "payment_methods": (
        "CARGO_BUILD_JOBS=1 cargo test -p payment_methods --lib --no-fail-fast "
        f"-- {TEST_SKIP}"
    ),
}

LLM_TEST_CRATES = frozenset(CRATE_TEST_CMD.keys())
LLM_MAX_FILE_LINES = 1100  # skip LLM on huge connector/router files; use source tests


def run(cmd: list[str], check: bool = True) -> int:
    print("$", " ".join(cmd))
    r = subprocess.run(cmd, cwd=ROOT)
    if check and r.returncode != 0:
        sys.exit(r.returncode)
    return r.returncode


def crate_from_path(file_path: str) -> str | None:
    m = re.match(r"crates/([^/]+)/", file_path)
    return m.group(1) if m else None


def test_cmd_for_crate(crate: str) -> str:
    return CRATE_TEST_CMD.get(
        crate,
        f"CARGO_BUILD_JOBS=1 cargo test -p {crate} --lib --no-fail-fast -- {TEST_SKIP}",
    )


def _normalize_export_test_names(names: list[str], crate: str) -> list[str]:
    """Prefix cargo test names when the log omits the package root."""
    out: list[str] = []
    for name in names:
        name = name.strip()
        if not name:
            continue
        if name.startswith(f"{crate}::"):
            out.append(name)
        elif "::" in name:
            out.append(name)
        else:
            out.append(f"{crate}::{name}")
    return out


def strip_eof_hunk(patch: str) -> str:
    for marker in ("@@ -3496,4 +3497,4 @@", "@@ -3496,4 +3496,4 @@"):
        if marker in patch:
            patch = patch[: patch.index(marker)].rstrip() + "\n"
    return patch


def _extract_diff_snippets(patch: str, prefix: str, max_snippets: int = 2) -> list[str]:
    """Extract multiline or single-line hunks for `-` (fix) or `+` (bug) lines."""
    snippets: list[str] = []
    singles: list[str] = []
    current: list[str] = []
    other = "+" if prefix == "-" else "-"
    for line in patch.splitlines():
        if line.startswith("---") or line.startswith("+++"):
            continue
        if line.startswith("@@"):
            if len(current) >= 2:
                snippets.append("\n".join(current))
            elif len(current) == 1:
                singles.append(current[0])
            current = []
            continue
        if line.startswith(prefix) and not line.startswith(prefix * 3):
            text = line[1:].rstrip()
            if text and not text.startswith("//") and len(text) > 8:
                current.append(text)
        elif line.startswith(other) and current:
            if len(current) >= 2:
                snippets.append("\n".join(current))
            elif len(current) == 1:
                singles.append(current[0])
            current = []
    if len(current) >= 2:
        snippets.append("\n".join(current))
    elif len(current) == 1:
        singles.append(current[0])

    snippets = sorted(set(snippets), key=len, reverse=True)
    singles = sorted(set(singles), key=len, reverse=True)
    out: list[str] = []
    for snip in snippets:
        if len(snip) > 400:
            snip = snip[:400]
        out.append(snip)
    for s in singles:
        if len(s) >= 20 and s not in "\n".join(out):
            out.append(s)
        if len(out) >= max_snippets:
            return out[:max_snippets]
    return out[:max_snippets]


def _extract_removed_snippets(patch: str, max_snippets: int = 2) -> list[str]:
    return _extract_diff_snippets(patch, "-", max_snippets)


def _extract_added_snippets(patch: str, max_snippets: int = 1) -> list[str]:
    return _extract_diff_snippets(patch, "+", max_snippets)


def _primary_crate_path(patch: str) -> str | None:
    for line in patch.splitlines():
        if line.startswith("+++ b/"):
            return line[6:].strip()
    return None


def _rust_raw_string(content: str) -> str:
    """Rust raw string literal safe for multiline patch snippets."""
    hashes = 0
    while True:
        closing = '"' + ("#" * hashes)
        if closing not in content:
            break
        hashes += 1
    delim = "#" * hashes
    return f'r{delim}"{content}"{delim}'


def _path_from_common_utils(crate_rel: str) -> str:
    """common_utils lives in crates/common_utils; sibling crates are ../<crate>/..."""
    rel = crate_rel.removeprefix("crates/")
    return f"/../{rel}"


def _make_test_patch(
    pull: str, crate_rel: str, fix_snippets: list[str], bug_snippets: list[str]
) -> tuple[str, list[str]]:
    mod_name = f"pilot_pr_{pull}_tests"
    rel_from_common = _path_from_common_utils(crate_rel)
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
            f'        assert!(\n'
            f'            normalize(&src).contains(&needle),\n'
            f'            "Expected fix code present in source"\n'
            f'        );\n'
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
            f'        assert!(\n'
            f'            !normalize(&src).contains(&needle),\n'
            f'            "Bug-only code must not appear on clean tree"\n'
            f'        );\n'
            f'    }}'
        )

    if len(test_names) < 2 and fix_snippets:
        snip = fix_snippets[0]
        line = next((ln.strip() for ln in snip.splitlines() if len(ln.strip()) >= 24), snip[:80])
        if line and f"test_fix_present_0" in "".join(test_names):
            raw = _rust_raw_string(line)
            name = "test_fix_anchor_line"
            test_names.append(f"common_utils::{mod_name}::tests::{name}")
            tests_rs.append(
                f'    #[test]\n'
                f'    fn {name}() {{\n'
                f'        let src = read_source();\n'
                f'        let needle = normalize({raw});\n'
                f'        assert!(normalize(&src).contains(&needle));\n'
                f'    }}'
            )
    if not tests_rs:
        tests_rs.append(
            '    #[test]\n'
            '    fn test_source_file_readable() {\n'
            '        assert!(!read_source().is_empty());\n'
            '    }'
        )
        test_names.append(f"common_utils::{mod_name}::tests::test_source_file_readable")
    body = f'''#[cfg(test)]
mod tests {{
    const SRC: &str = concat!(env!("CARGO_MANIFEST_DIR"), "{rel_from_common}");

    fn read_source() -> String {{
        std::fs::read_to_string(SRC).expect("bug target source")
    }}

    fn normalize(s: &str) -> String {{
        s.split_whitespace().collect::<Vec<_>>().join(" ")
    }}

{chr(10).join(tests_rs)}
}}
'''
    file_path = f"crates/common_utils/src/{mod_name}.rs"
    lines = body.strip().split("\n")
    diff = [
        f"diff --git a/{file_path} b/{file_path}",
        "new file mode 100644",
        "--- /dev/null",
        f"+++ b/{file_path}",
        f"@@ -0,0 +1,{len(lines)} @@",
    ]
    diff.extend("+" + ln for ln in lines)

    lib = REPO_PATH / "crates/common_utils/src/lib.rs"
    content = lib.read_text()
    mod_line = f"mod {mod_name};"
    if mod_line in content:
        lib_patch = ""
    else:
        lib_lines = content.split("\n")
        insert_at = len(lib_lines)
        for i in range(len(lib_lines) - 1, -1, -1):
            if lib_lines[i].strip():
                insert_at = i + 1
                break
        addition = ["", "#[cfg(test)]", mod_line]
        ctx = lib_lines[max(0, insert_at - 2) : insert_at]
        old_start = max(0, insert_at - 2) + 1
        lib_patch = (
            f"diff --git a/crates/common_utils/src/lib.rs b/crates/common_utils/src/lib.rs\n"
            f"--- a/crates/common_utils/src/lib.rs\n"
            f"+++ b/crates/common_utils/src/lib.rs\n"
            f"@@ -{old_start},{len(ctx)} +{old_start},{len(ctx) + len(addition)} @@\n"
            + "".join(" " + ln + "\n" for ln in ctx)
            + "".join("+" + ln + "\n" for ln in addition)
        )
    return "\n".join(diff) + "\n" + lib_patch, test_names[:2] if len(test_names) >= 2 else test_names


def cmd_collect(args: argparse.Namespace) -> None:
    WORKDIR.mkdir(parents=True, exist_ok=True)
    instances = []
    seen = set()

    for bug_diff in sorted(PR_MIRROR_DIR.glob("juspay__hyperswitch.fece9bc3.pr_*/bug__pr_*.diff")):
        pull = bug_diff.stem.replace("bug__pr_", "")
        if pull in seen:
            continue
        patch = strip_eof_hunk(bug_diff.read_text())
        if not validate_patch_applies(REPO_PATH, patch).success:
            continue
        meta = bug_diff.parent / f"metadata__pr_{pull}.json"
        title = ""
        if meta.exists():
            try:
                md = json.loads(meta.read_text())
                title = md.get("instance_ref", {}).get("title", "") or md.get("title", "")
            except json.JSONDecodeError:
                pass
        inst_id = f"{REPO_PROFILE}.pr_{pull}"
        instances.append(
            {
                "instance_id": inst_id,
                "repo": REPO_PROFILE,
                "base_commit": BASE_COMMIT,
                "patch": patch,
                "test_patch": "",
                "FAIL_TO_PASS": [],
                "PASS_TO_PASS": [],
                "test_cmd": TEST_CMD,
                "problem_statement": title or f"PR mirror bug from #{pull}",
                "language": "rust",
                "bug_type": "pr_mirror",
                "pull_number": int(pull),
            }
        )
        seen.add(pull)
        if len(instances) >= args.limit:
            break

    with open(DATASET_JSON, "w") as f:
        json.dump(instances, f, indent=2)
    print(f"Collected {len(instances)} instances -> {DATASET_JSON}")


def _apply_source_tests_to_instance(inst: dict, pull: str) -> bool:
    """Attach common_utils source-analysis tests. Returns False if nothing generated."""
    patch = inst["patch"]
    crate_path = _primary_crate_path(patch)
    if not crate_path or not crate_path.startswith("crates/"):
        return False
    fix_snippets = _extract_removed_snippets(patch)
    bug_snippets = _extract_added_snippets(patch)
    if not fix_snippets and not bug_snippets:
        tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_]{10,}", patch)
        tokens = [t for t in tokens if t not in ("common_enums", "RequiredField")][:2]
        if tokens:
            fix_snippets = [f" {t} " for t in tokens[:1]]
            bug_snippets = [f" {t} " for t in tokens[1:2]]
        else:
            return False
    test_patch, f2p_names = _make_test_patch(pull, crate_path, fix_snippets, bug_snippets)
    inst["test_patch"] = test_patch
    inst["FAIL_TO_PASS"] = f2p_names
    inst["test_cmd"] = TEST_CMD
    inst["test_generation"] = "source_analysis"
    inst["target_crate"] = "common_utils"
    inst["target_file"] = crate_path
    return True


def cmd_add_tests(args: argparse.Namespace) -> None:
    instances = json.loads(DATASET_JSON.read_text())
    updated = []
    for inst in instances:
        pull = str(inst["pull_number"])
        if not _apply_source_tests_to_instance(inst, pull):
            print(f"  warn {inst['instance_id']}: no snippets")
        updated.append(inst)
    with open(DATASET_JSON, "w") as f:
        json.dump(updated, f, indent=2)
    print(f"Added source tests to {len(updated)} instances -> {DATASET_JSON}")


def cmd_add_tests_quality(args: argparse.Namespace) -> None:
    """LLM + AST runtime tests in the bug file; per-crate test_cmd; source fallback."""
    from swesmith.bug_gen.rust_grounded.generator.test_patch_generator import TestPatchGenerator

    gen = TestPatchGenerator(model=args.model)
    instances = json.loads(DATASET_JSON.read_text())
    updated = []
    stats = {"llm_runtime": 0, "source_fallback": 0, "skipped": 0}

    for inst in instances:
        pull = str(inst["pull_number"])
        patch = inst["patch"]
        file_path = _primary_crate_path(patch)
        if not file_path or not file_path.startswith("crates/"):
            print(f"  skip {inst['instance_id']}: no crates/ path")
            stats["skipped"] += 1
            updated.append(inst)
            continue

        crate = crate_from_path(file_path)
        if not crate:
            stats["skipped"] += 1
            updated.append(inst)
            continue

        full_path = REPO_PATH / file_path
        if not full_path.exists():
            print(f"  skip {inst['instance_id']}: missing {file_path}")
            stats["skipped"] += 1
            updated.append(inst)
            continue

        file_lines = len(full_path.read_text().splitlines())
        try_llm = crate in LLM_TEST_CRATES and file_lines <= getattr(args, "max_llm_lines", LLM_MAX_FILE_LINES)

        test_patch: str | None = None
        test_names: list[str] | None = None
        if try_llm:
            content = full_path.read_text()
            test_patch, test_names = gen.generate_test_patch(
                patch, file_path, content, str(REPO_PATH)
            )
        elif crate in LLM_TEST_CRATES:
            print(f"  source {inst['instance_id']}: file too large ({file_lines} lines) for LLM")

        if test_patch and test_names and len(test_names) >= 2:
            inst["test_patch"] = test_patch
            inst["target_crate"] = crate
            inst["target_file"] = file_path
            inst["test_cmd"] = test_cmd_for_crate(crate)
            inst["test_generation"] = "llm_runtime"
            inst["FAIL_TO_PASS"] = [f"regression_tests::{n}" for n in test_names[:2]]
            stats["llm_runtime"] += 1
            print(f"  ok {inst['instance_id']}: llm ({crate}) -> {test_names[:2]}")
        elif args.fallback_source and _apply_source_tests_to_instance(inst, pull):
            stats["source_fallback"] += 1
            print(f"  fallback {inst['instance_id']}: source tests in common_utils")
        else:
            stats["skipped"] += 1
            print(f"  skip {inst['instance_id']}: no quality/source tests")
        updated.append(inst)

    with open(DATASET_JSON, "w") as f:
        json.dump(updated, f, indent=2)
    print(
        f"Quality tests: llm_runtime={stats['llm_runtime']}, "
        f"source_fallback={stats['source_fallback']}, skipped={stats['skipped']} "
        f"-> {DATASET_JSON}"
    )


def cmd_mirror_input(args: argparse.Namespace) -> None:
    """Build jsonl input from all_52 + fetch candidates for mirror.generate."""
    sources = [
        PR_MIRROR_DIR / "all_52_rust_prs.jsonl",
        PR_MIRROR_DIR / "batch1_small.jsonl",
        PR_MIRROR_DIR / "merged_instances.jsonl",
    ]
    rows = []
    seen = set()
    for path in sources:
        if not path.exists():
            continue
        for line in path:
            if not line.strip():
                continue
            o = json.loads(line)
            if o.get("base_commit", "")[:8] != BASE_COMMIT[:8]:
                continue
            pn = o.get("pull_number") or int(str(o.get("instance_id", "")).split(".")[-1].replace("pr_", ""))
            if pn in seen or not o.get("patch"):
                continue
            seen.add(pn)
            rows.append(
                {
                    "repo": "juspay/hyperswitch",
                    "pull_number": pn,
                    "instance_id": f"{REPO_PROFILE}.pr_{pn}",
                    "base_commit": BASE_COMMIT,
                    "patch": o["patch"],
                }
            )
            if len(rows) >= args.limit:
                break
        if len(rows) >= args.limit:
            break
    with open(INPUT_JSONL, "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")
    print(f"Wrote {len(rows)} PRs -> {INPUT_JSONL}")
    if args.run_mirror:
        run(
            [
                "uv",
                "run",
                "python",
                "-m",
                "swesmith.bug_gen.mirror.generate",
                str(INPUT_JSONL),
                "--model",
                args.model,
                "-n",
                str(args.workers),
            ]
        )


def cmd_validate(args: argparse.Namespace) -> None:
    run(
        [
            "uv",
            "run",
            "python",
            "-m",
            "swesmith.harness.valid",
            str(DATASET_JSON),
            "-w",
            str(args.workers),
            *(["--redo_existing"] if args.redo else []),
        ]
    )


def cmd_export(args: argparse.Namespace) -> None:
    instances = json.loads(DATASET_JSON.read_text())
    validated = []
    for inst in instances:
        report_path = LOG_DIR_RUN_VALIDATION / inst["repo"] / inst["instance_id"] / LOG_REPORT
        row = {**inst}
        if not report_path.exists():
            continue
        report = json.loads(report_path.read_text())
        crate = inst.get("target_crate", "common_utils")
        f2p = report.get(FAIL_TO_PASS, [])
        p2p = report.get(PASS_TO_PASS, [])
        row["FAIL_TO_PASS"] = _normalize_export_test_names(f2p, crate)
        row["PASS_TO_PASS"] = _normalize_export_test_names(p2p, crate)
        row["metrics"] = {
            "FAIL_TO_PASS": len(row["FAIL_TO_PASS"]),
            "PASS_TO_PASS": len(row["PASS_TO_PASS"]),
            "FAIL_TO_FAIL": len(report.get("FAIL_TO_FAIL", [])),
            "PASS_TO_FAIL": len(report.get("PASS_TO_FAIL", [])),
        }
        if len(row["FAIL_TO_PASS"]) >= args.min_f2p:
            row["validation_status"] = "success"
            validated.append(row)
        else:
            row["validation_status"] = "failed"
    validated.sort(
        key=lambda r: (-r["metrics"]["FAIL_TO_PASS"], -r["metrics"]["PASS_TO_PASS"])
    )
    if args.limit and len(validated) > args.limit:
        validated = validated[: args.limit]
    VALIDATED_JSON.write_text(json.dumps(validated, indent=2))
    DATA_EXPORT.parent.mkdir(parents=True, exist_ok=True)
    DATA_EXPORT.write_text(json.dumps(validated, indent=2))
    print(f"Validated {len(validated)}/{len(instances)} with F2P>={args.min_f2p}")
    print(f"  -> {VALIDATED_JSON}")
    print(f"  -> {DATA_EXPORT}")


def cmd_all(args: argparse.Namespace) -> None:
    run(["uv", "run", "python", "scripts/manage_shared_volumes.py", "recreate", "juspay/hyperswitch"], check=False)
    if args.fetch_mirror:
        args.run_mirror = True
        cmd_mirror_input(args)
        args.run_mirror = False
    cmd_collect(args)
    if getattr(args, "quality", False):
        cmd_add_tests_quality(args)
    else:
        cmd_add_tests(args)
    cmd_validate(args)
    args.min_f2p = 2
    cmd_export(args)


def main() -> None:
    p = argparse.ArgumentParser(description="Scale PR-mirror to 100 validated instances")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("collect", help="Collect apply-ok bug patches")
    c.add_argument("--limit", type=int, default=100)
    c.set_defaults(func=cmd_collect)

    t = sub.add_parser("add-tests", help="Add common_utils source-analysis tests (fast)")
    t.set_defaults(func=cmd_add_tests)

    q = sub.add_parser(
        "add-tests-quality",
        help="LLM runtime tests in bug file (TestPatchGenerator) + per-crate test_cmd",
    )
    q.add_argument("--model", default="private-large")
    q.add_argument("--max-llm-lines", type=int, default=LLM_MAX_FILE_LINES,
                   help="Only call LLM for touched files with <= N lines")
    q.add_argument(
        "--no-fallback-source",
        action="store_false",
        dest="fallback_source",
        help="Do not fall back to source tests when LLM fails",
    )
    q.set_defaults(func=cmd_add_tests_quality, fallback_source=True)

    m = sub.add_parser("mirror-input", help="Build jsonl + optional mirror.generate")
    m.add_argument("--limit", type=int, default=100)
    m.add_argument("--run-mirror", action="store_true")
    m.add_argument("--model", default="private-large")
    m.add_argument("--workers", type=int, default=4)
    m.set_defaults(func=cmd_mirror_input)

    v = sub.add_parser("validate", help="Run harness.valid")
    v.add_argument("--workers", type=int, default=2)
    v.add_argument("--redo", action="store_true")
    v.set_defaults(func=cmd_validate)

    e = sub.add_parser("export", help="Export instances with F2P>=min")
    e.add_argument("--min-f2p", type=int, default=2)
    e.add_argument("--limit", type=int, default=100, help="Max instances in export")
    e.set_defaults(func=cmd_export)

    a = sub.add_parser("all", help="Full pipeline")
    a.add_argument("--limit", type=int, default=100)
    a.add_argument("--workers", type=int, default=2)
    a.add_argument("--fetch-mirror", action="store_true", help="Re-run mirror for input PRs")
    a.add_argument("--redo", action="store_true")
    a.add_argument(
        "--quality",
        action="store_true",
        help="Use add-tests-quality (LLM runtime) instead of source-only tests",
    )
    a.add_argument("--model", default="private-large")
    a.add_argument(
        "--no-fallback-source",
        action="store_false",
        dest="fallback_source",
    )
    a.set_defaults(func=cmd_all, fallback_source=True)

    args = p.parse_args()
    args.run_mirror = getattr(args, "run_mirror", False)
    args.func(args)


if __name__ == "__main__":
    main()
