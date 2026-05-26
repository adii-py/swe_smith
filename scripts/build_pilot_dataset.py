#!/usr/bin/env python3
"""Assemble pilot dataset from PR mirror bugs + optional LM bugs."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO_PROFILE = "juspay__hyperswitch.fece9bc3"
BASE = "fece9bc38b9890a1a40912ce2a95037842362e27"
PILOT_IDS = {
    "juspay__hyperswitch.fece9bc3.pr_12167",
    "juspay__hyperswitch.fece9bc3.pr_12191",
}
TEST_CMD = (
    "CARGO_BUILD_JOBS=1 cargo test --release -p hyperswitch_connectors -p payment_methods "
    "--lib --all-features --no-fail-fast -- "
    "--nocapture --skip redis --skip postgres --skip db --skip database --skip integration"
)
PR_DIR = ROOT / "logs/bug_gen" / REPO_PROFILE / "pr_mirror"
OUT = ROOT / "logs/bug_gen" / REPO_PROFILE / "pilot_2x2" / "pilot_instances.json"


def main():
    instances = []
    for iid in PILOT_IDS:
        folder = PR_DIR / iid
        bug = folder / f"bug__pr_{iid.split('_')[-1]}.diff"
        if not bug.exists():
            continue
        instances.append({
            "instance_id": iid,
            "repo": REPO_PROFILE,
            "base_commit": BASE,
            "patch": bug.read_text(),
            "test_patch": "",
            "problem_statement": f"PR mirror bug from {iid}",
            "FAIL_TO_PASS": [],
            "PASS_TO_PASS": [],
            "test_cmd": TEST_CMD,
            "bug_type": "pr_mirror",
        })
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(instances, indent=2))
    print(f"Wrote {len(instances)} instances -> {OUT}")


if __name__ == "__main__":
    main()
