#!/usr/bin/env python3
"""
Hyperswitch benchmark pipeline (50 PR-mirror + 50 LLM/AST-grounded bugs).

Uses canonical SWE-smith components:
  - PR mirror:     swesmith.bug_gen.mirror.generate
  - LLM rewrite:   swesmith.bug_gen.llm.rust_rewrite (configs/bug_gen/lm_rust_complex_bugs.yml)
  - AST grounded:  swesmith.bug_gen.rust_grounded.pipeline
  - Test patches:  swesmith.bug_gen.rust_grounded.generator.test_patch_generator
  - Profile:       swesmith.profiles.rust.HyperswitchFece9bc3
  - Validation:    swesmith.harness.valid
  - Docker volume: scripts/manage_shared_volumes.py

Usage:
  uv run python scripts/hyperswitch_benchmark_pipeline.py setup-volume
  uv run python scripts/hyperswitch_benchmark_pipeline.py pr-mirror --input logs/.../all_51_instances.jsonl
  uv run python scripts/hyperswitch_benchmark_pipeline.py lm-grounded --repo-path /path/to/hyperswitch
  uv run python scripts/hyperswitch_benchmark_pipeline.py add-tests --dataset bugs.json
  uv run python scripts/hyperswitch_benchmark_pipeline.py validate --dataset final.json --workers 2
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

REPO_PROFILE = "juspay__hyperswitch.fece9bc3"
BASE_COMMIT = "fece9bc38b9890a1a40912ce2a95037842362e27"
LOG_ROOT = Path("logs/bug_gen") / REPO_PROFILE
PR_MIRROR_DIR = LOG_ROOT / "pr_mirror"
LM_DIR = LOG_ROOT / "lm_grounded"

# HyperswitchFece9bc3 test_cmd (unit tests only, skip external infra)
DEFAULT_TEST_CMD = (
    "CARGO_BUILD_JOBS=1 cargo test --release -p common_utils -p router "
    "-p analytics --lib --all-features --no-fail-fast -- "
    "--nocapture --skip redis --skip postgres --skip db --skip database --skip integration"
)


def run_cmd(cmd: list[str], check: bool = True) -> int:
    print(f"$ {' '.join(cmd)}")
    result = subprocess.run(cmd)
    if check and result.returncode != 0:
        sys.exit(result.returncode)
    return result.returncode


def cmd_setup_volume(_args: argparse.Namespace) -> None:
    """Create shared Docker target volume for faster Rust rebuilds."""
    run_cmd(
        ["uv", "run", "python", "scripts/manage_shared_volumes.py", "create", "juspay/hyperswitch"]
    )


def cmd_pr_mirror(args: argparse.Namespace) -> None:
    """Run PR-mirror bug generation (programmatic invert + LLM recovery fallback)."""
    PR_MIRROR_DIR.mkdir(parents=True, exist_ok=True)
    run_cmd(
        [
            "uv", "run", "python", "-m", "swesmith.bug_gen.mirror.generate",
            args.input,
            "--model", args.model,
            "-n", str(args.workers),
            *(["--redo_existing"] if args.redo else []),
        ]
    )
    print(f"PR mirror output: {PR_MIRROR_DIR}")


def cmd_lm_grounded(args: argparse.Namespace) -> None:
    """Run AST-grounded LLM bug pipeline."""
    LM_DIR.mkdir(parents=True, exist_ok=True)
    out = args.output or str(LM_DIR / "grounded_bugs.json")
    run_cmd(
        [
            "uv", "run", "python", "-m", "swesmith.bug_gen.rust_grounded.pipeline",
            "--repo", args.repo_path,
            "--model", args.model,
            "--max-bugs", str(args.max_bugs),
            "--min-difficulty", args.difficulty,
            "--output", out,
        ]
    )


def cmd_lm_rewrite(args: argparse.Namespace) -> None:
    """Run legacy rust_rewrite with complex-bugs config."""
    config = args.config or "configs/bug_gen/lm_rust_complex_bugs.yml"
    run_cmd(
        [
            "uv", "run", "python", "-m", "swesmith.bug_gen.llm.rust_rewrite",
            "--repo", REPO_PROFILE,
            "--config", config,
            "--model", args.model,
            "--max-bugs", str(args.max_bugs),
        ]
    )


def cmd_collect_pr_mirror(args: argparse.Namespace) -> None:
    """Collect PR mirror bug patches into SWE-bench JSON dataset."""
    instances = []
    pr_dir = PR_MIRROR_DIR
    for meta in pr_dir.glob("**/metadata__pr_*.json"):
        data = json.loads(meta.read_text())
        bug_diff = meta.parent / meta.name.replace("metadata", "bug").replace(".json", ".diff")
        if not bug_diff.exists():
            continue
        ref = data.get("instance_ref") or {}
        inst = {
            "instance_id": ref.get("instance_id", meta.parent.name),
            "repo": REPO_PROFILE,
            "base_commit": BASE_COMMIT,
            "patch": bug_diff.read_text(),
            "test_patch": "",
            "problem_statement": ref.get("title", "PR mirror bug"),
            "FAIL_TO_PASS": [],
            "PASS_TO_PASS": [],
            "test_cmd": DEFAULT_TEST_CMD,
            "invert_method": data.get("invert_method", "unknown"),
        }
        instances.append(inst)

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w") as f:
        json.dump(instances[: args.limit], f, indent=2)
    print(f"Collected {len(instances[:args.limit])} PR-mirror instances -> {out}")


def cmd_add_tests(args: argparse.Namespace) -> None:
    """Add test patches to instances using TestPatchGenerator."""
    from swesmith.bug_gen.rust_grounded.generator.test_patch_generator import TestPatchGenerator

    with open(args.dataset) as f:
        instances = json.load(f)

    gen = TestPatchGenerator(model=args.model)
    repo_path = Path(args.repo_path)
    updated = []

    for inst in instances:
        if inst.get("test_patch") and not args.force:
            updated.append(inst)
            continue
        patch = inst.get("patch", "")
        if not patch or "crates/" not in patch:
            updated.append(inst)
            continue
        file_path = patch.split("+++ b/")[1].split("\n")[0] if "+++ b/" in patch else ""
        if not file_path:
            updated.append(inst)
            continue
        full = repo_path / file_path
        if not full.exists():
            updated.append(inst)
            continue
        content = full.read_text()
        test_patch, test_names = gen.generate_test_patch(patch, file_path, content)
        if test_patch and test_names:
            inst["test_patch"] = test_patch
            inst["FAIL_TO_PASS"] = [f"regression_tests::{n}" for n in test_names]
            if len(test_names) < 2:
                print(f"  Warning: {inst['instance_id']} has <2 F2P tests")
        updated.append(inst)

    out = Path(args.output or args.dataset)
    with open(out, "w") as f:
        json.dump(updated, f, indent=2)
    print(f"Updated {len(updated)} instances -> {out}")


def cmd_validate(args: argparse.Namespace) -> None:
    """Run Docker validation harness."""
    run_cmd(
        [
            "uv", "run", "python", "-m", "swesmith.harness.valid",
            args.dataset,
            "-w", str(args.workers),
            *(["--redo_existing"] if args.redo else []),
        ]
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Hyperswitch 50+50 benchmark pipeline")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("setup-volume", help="Create shared Docker target volume")
    p.set_defaults(func=cmd_setup_volume)

    p = sub.add_parser("pr-mirror", help="Generate PR-mirror bugs")
    p.add_argument("--input", required=True, help="JSONL/JSON of PR instances with fix patches")
    p.add_argument("--model", default="private-large")
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--redo", action="store_true")
    p.set_defaults(func=cmd_pr_mirror)

    p = sub.add_parser("collect-pr-mirror", help="Collect bug diffs into dataset JSON")
    p.add_argument("--output", default=str(PR_MIRROR_DIR / "pr_mirror_bugs.json"))
    p.add_argument("--limit", type=int, default=50)
    p.set_defaults(func=cmd_collect_pr_mirror)

    p = sub.add_parser("lm-grounded", help="AST-grounded bug generation")
    p.add_argument("--repo-path", required=True, help="Local hyperswitch checkout at base commit")
    p.add_argument("--model", default="private-large")
    p.add_argument("--max-bugs", type=int, default=50)
    p.add_argument("--difficulty", default="hard", choices=["easy", "medium", "hard"])
    p.add_argument("--output", default=None)
    p.set_defaults(func=cmd_lm_grounded)

    p = sub.add_parser("lm-rewrite", help="rust_rewrite complex bugs")
    p.add_argument("--config", default=None)
    p.add_argument("--model", default="private-large")
    p.add_argument("--max-bugs", type=int, default=50)
    p.set_defaults(func=cmd_lm_rewrite)

    p = sub.add_parser("add-tests", help="Generate test patches for instances")
    p.add_argument("--dataset", required=True)
    p.add_argument("--repo-path", required=True)
    p.add_argument("--model", default="private-large")
    p.add_argument("--output", default=None)
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_add_tests)

    p = sub.add_parser("validate", help="Run harness validation")
    p.add_argument("--dataset", required=True)
    p.add_argument("--workers", type=int, default=2)
    p.add_argument("--redo", action="store_true")
    p.set_defaults(func=cmd_validate)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
