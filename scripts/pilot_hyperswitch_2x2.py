#!/usr/bin/env python3
"""
Pilot: 2 PR-mirror + 2 AST-grounded LM bugs for Hyperswitch, then validate F2P/P2P.

Uses .env: LITE_LLM_URL, LITE_LLM_API_KEY, LITE_LLM_MODEL (or private-large)
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

REPO_PROFILE = "juspay__hyperswitch.fece9bc3"
REPO_PATH = ROOT / "juspay__hyperswitch.fece9bc3"
BASE_COMMIT = "fece9bc38b9890a1a40912ce2a95037842362e27"
PILOT_DIR = ROOT / "logs/bug_gen" / REPO_PROFILE / "pilot_2x2"
MODEL = os.getenv("LITE_LLM_MODEL", "private-large")
API_KEY = os.getenv("LITE_LLM_API_KEY", "")
API_BASE = os.getenv("LITE_LLM_URL", "")

DEFAULT_TEST_CMD = (
    "CARGO_BUILD_JOBS=1 cargo test --release -p common_utils -p router -p analytics "
    "--lib --all-features --no-fail-fast -- "
    "--nocapture --skip redis --skip postgres --skip db --skip database --skip integration"
)


def run(cmd: list[str], cwd: Path | None = None, input_text: str | None = None, timeout: int = 600) -> tuple[int, str]:
    print(f"$ {' '.join(cmd)}")
    r = subprocess.run(
        cmd,
        cwd=cwd or ROOT,
        input=input_text,
        capture_output=True,
        text=True,
        timeout=timeout,
        env={**os.environ, "LITE_LLM_API_KEY": API_KEY, "LITE_LLM_URL": API_BASE},
    )
    out = (r.stdout or "") + (r.stderr or "")
    if r.returncode != 0:
        print(out[-2000:])
    return r.returncode, out


def prepare_pr_input() -> Path:
    """Pick 2 small Rust PR instances from batch1_small.jsonl."""
    src = ROOT / "logs/bug_gen" / REPO_PROFILE / "pr_mirror" / "batch1_small.jsonl"
    out = PILOT_DIR / "pr_mirror_input.jsonl"
    PILOT_DIR.mkdir(parents=True, exist_ok=True)
    instances = []
    with open(src) as f:
        for line in f:
            inst = json.loads(line)
            patch = inst.get("patch", "")
            if ".rs" not in patch:
                continue
            # Prefer smaller patches for pilot
            if patch.count("\n@@") <= 3 and len(patch) < 8000:
                instances.append(inst)
            if len(instances) >= 2:
                break
    if len(instances) < 2:
        raise RuntimeError("Could not find 2 suitable PR instances in batch1_small.jsonl")
    with open(out, "w") as f:
        for inst in instances:
            f.write(json.dumps(inst) + "\n")
    print(f"Prepared {len(instances)} PR instances -> {out}")
    for inst in instances:
        print(f"  - {inst['instance_id']}")
    return out


def run_pr_mirror(pr_input: Path, pilot_ids: set[str]) -> list[dict]:
    """Run mirror.generate on 2 PRs."""
    run(
        [
            "uv", "run", "python", "-m", "swesmith.bug_gen.mirror.generate",
            str(pr_input),
            "--model", f"openai/{MODEL}",
            "-n", "1",
            "--redo_existing",
            "-f", "5",
            "-l", "200",
        ],
        timeout=1800,
    )
    return collect_pr_mirror_bugs(pilot_ids)


def collect_pr_mirror_bugs(pilot_ids: set[str] | None = None) -> list[dict]:
    """Collect bug patches from mirror output."""
    pr_mirror_dir = ROOT / "logs/bug_gen" / REPO_PROFILE / "pr_mirror"
    instances = []
    for meta in sorted(pr_mirror_dir.glob("**/metadata__pr_*.json")):
        data = json.loads(meta.read_text())
        status = data.get("recover_status")
        if status and status != "success":
            continue
        bug_file = meta.parent / meta.name.replace("metadata__pr_", "bug__pr_").replace(".json", ".diff")
        if not bug_file.exists():
            continue
        ref = data.get("instance_ref") or {}
        iid = ref.get("instance_id", meta.parent.name)
        if pilot_ids and iid not in pilot_ids:
            continue
        patch = bug_file.read_text()
        if not patch.strip():
            continue
        instances.append({
            "instance_id": iid,
            "repo": REPO_PROFILE,
            "base_commit": BASE_COMMIT,
            "patch": patch,
            "test_patch": "",
            "problem_statement": ref.get("title", "PR mirror bug"),
            "FAIL_TO_PASS": [],
            "PASS_TO_PASS": [],
            "test_cmd": DEFAULT_TEST_CMD,
            "bug_type": "pr_mirror",
            "invert_method": data.get("invert_method", "llm_recovery"),
        })
    return instances[:2]


def run_lm_grounded() -> list[dict]:
    """Generate 2 AST-grounded bugs."""
    out = PILOT_DIR / "lm_grounded_bugs.json"
    code, _ = run(
        [
            "uv", "run", "python", "-m", "swesmith.bug_gen.rust_grounded.pipeline",
            "--repo", str(REPO_PATH),
            "--model", MODEL,
            "--max-bugs", "2",
            "--min-difficulty", "medium",
            "--output", str(out),
        ],
        timeout=3600,
    )
    if code != 0 or not out.exists():
        return []
    data = json.loads(out.read_text())
    for inst in data:
        inst["repo"] = REPO_PROFILE
        inst["bug_type"] = "lm_grounded"
        if not inst.get("test_cmd"):
            inst["test_cmd"] = DEFAULT_TEST_CMD
    return data[:2]


def add_test_patches(instances: list[dict]) -> list[dict]:
    from swesmith.bug_gen.rust_grounded.generator.test_patch_generator import TestPatchGenerator
    from swesmith.bug_gen.patch_inverter import validate_patch_applies

    gen = TestPatchGenerator(model=MODEL)
    updated = []
    for inst in instances:
        patch = inst.get("patch", "")
        if not patch or "+++ b/" not in patch:
            updated.append(inst)
            continue
        file_path = patch.split("+++ b/")[1].split("\n")[0].strip()
        full = REPO_PATH / file_path
        if not full.exists():
            print(f"  Skip tests: {file_path} not found")
            updated.append(inst)
            continue
        content = full.read_text()
        test_patch, test_names = gen.generate_test_patch(patch, file_path, content)
        if test_patch:
            check = validate_patch_applies(REPO_PATH, test_patch)
            if not check.success:
                print(f"  Test patch apply check failed: {check.message[:200]}")
            else:
                inst["test_patch"] = test_patch
                if test_names:
                    inst["FAIL_TO_PASS"] = [f"regression_tests::{n}" for n in test_names]
        updated.append(inst)
    return updated


def validate_patch_compile(inst: dict) -> dict:
    """Local validation: apply patches + cargo check on affected crate."""
    from swesmith.bug_gen.patch_inverter import validate_patch_applies

    result = {
        "instance_id": inst["instance_id"],
        "bug_apply": False,
        "test_apply": False,
        "compile": False,
        "messages": [],
    }
    patch = inst.get("patch", "")
    test_patch = inst.get("test_patch", "")

    if not patch:
        result["messages"].append("no bug patch")
        return result

    with tempfile.TemporaryDirectory() as tmp:
        clone = Path(tmp) / "repo"
        run(["git", "clone", "--quiet", str(REPO_PATH), str(clone)])
        run(["git", "checkout", "--quiet", BASE_COMMIT], cwd=clone)

        if test_patch:
            t = validate_patch_applies(clone, test_patch)
            result["test_apply"] = t.success
            if t.success:
                subprocess.run(
                    ["git", "apply", "-"],
                    cwd=clone,
                    input=test_patch,
                    capture_output=True,
                    text=True,
                )

        b = validate_patch_applies(clone, patch)
        result["bug_apply"] = b.success
        if not b.success:
            result["messages"].append(f"bug apply: {b.message[:300]}")
            return result

        subprocess.run(
            ["git", "apply", "-"],
            cwd=clone,
            input=patch,
            capture_output=True,
            text=True,
        )

        # Detect crate from patch path
        crate = "common_utils"
        m = re.search(r"crates/([^/]+)/", patch)
        if m:
            crate = m.group(1)

        code, out = run(
            ["cargo", "check", "--release", "-p", crate],
            cwd=clone,
            timeout=900,
        )
        result["compile"] = code == 0
        if not result["compile"]:
            result["messages"].append(out[-500:])

    return result


def main() -> None:
    if not API_KEY:
        print("WARNING: LITE_LLM_API_KEY empty in .env — LLM steps may fail")
    if not REPO_PATH.exists():
        print(f"ERROR: Repo not found at {REPO_PATH}")
        sys.exit(1)

    PILOT_DIR.mkdir(parents=True, exist_ok=True)
    report = {"pr_mirror": [], "lm_grounded": [], "validation": []}

    print("\n=== STEP 1: PR Mirror (2 instances) ===")
    pr_input = prepare_pr_input()
    pilot_ids = {
        json.loads(l)["instance_id"] for l in open(pr_input) if l.strip()
    }
    pr_bugs = run_pr_mirror(pr_input, pilot_ids)
    print(f"Collected {len(pr_bugs)} PR mirror bugs")

    print("\n=== STEP 2: LM Grounded (2 instances) ===")
    lm_bugs = run_lm_grounded()
    print(f"Generated {len(lm_bugs)} LM grounded bugs")

    print("\n=== STEP 3: Test patches ===")
    all_inst = add_test_patches(pr_bugs + lm_bugs)

    out_path = PILOT_DIR / "pilot_instances.json"
    with open(out_path, "w") as f:
        json.dump(all_inst, f, indent=2)
    print(f"Saved {len(all_inst)} instances -> {out_path}")

    print("\n=== STEP 4: Patch apply + compile validation ===")
    for inst in all_inst:
        v = validate_patch_compile(inst)
        report["validation"].append(v)
        print(
            f"  {inst['instance_id']}: "
            f"bug_apply={v['bug_apply']} test_apply={v['test_apply']} compile={v['compile']}"
        )

    print("\n=== STEP 5: Harness validation (Docker) ===")
    docker_img = "swebench/swesmith.arm64.juspay_1776_hyperswitch.fece9bc3:latest"
    dcode, _ = run(["docker", "image", "inspect", docker_img], timeout=30)
    if dcode == 0:
        run(
            [
                "uv", "run", "python", "-m", "swesmith.harness.valid",
                str(out_path),
                "-w", "1",
                "--redo_existing",
            ],
            timeout=7200,
        )
        report["harness"] = "ran"
    else:
        print(f"  Docker image not found ({docker_img}) — skipping full F2P harness")
        print("  Run: uv run python scripts/manage_shared_volumes.py create juspay/hyperswitch")
        print("  Then build Hyperswitch profile image before harness validation")
        report["harness"] = "skipped_no_image"

    report_path = PILOT_DIR / "pilot_report.json"
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2)
    print(f"\nReport: {report_path}")


if __name__ == "__main__":
    main()
