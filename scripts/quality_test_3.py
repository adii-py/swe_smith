"""Generate LLM runtime tests for 3 best-candidate instances and show quality analysis."""
import json
import re
from pathlib import Path

from swesmith.bug_gen.rust_grounded.generator.test_patch_generator import TestPatchGenerator

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT / "juspay__hyperswitch.fece9bc3"
SRC  = ROOT / "data/hyperswitch_pr_mirror_100.json"
OUT  = ROOT / "logs/bug_gen/juspay__hyperswitch.fece9bc3/pr_mirror/scale_100/quality_3_dataset.json"
LOG  = ROOT / "logs/bug_gen/juspay__hyperswitch.fece9bc3/pr_mirror/scale_100/quality_3_test.log"

TEST_SKIP = "--nocapture --skip redis --skip postgres --skip db --skip database --skip integration"
CRATE_CMD = {
    "analytics":             f"CARGO_BUILD_JOBS=1 cargo test -p analytics --lib --no-fail-fast -- {TEST_SKIP}",
    "connector_configs":     f"CARGO_BUILD_JOBS=1 cargo test -p connector_configs --lib --no-fail-fast -- {TEST_SKIP}",
    "hyperswitch_connectors":f"CARGO_BUILD_JOBS=1 cargo test -p hyperswitch_connectors --lib --no-fail-fast -- {TEST_SKIP}",
}

TARGET_PRS = {"pr_12312", "pr_12317", "pr_11757"}

all_insts = json.loads(SRC.read_text())
gen = TestPatchGenerator(model="private-large")

results = []
for raw in all_insts:
    pr = raw["instance_id"].split(".")[-1]
    if pr not in TARGET_PRS:
        continue
    inst = {k: v for k, v in raw.items() if k not in ("metrics", "validation_status")}
    patch = inst["patch"]
    fp = next((l[6:].strip() for l in patch.splitlines() if l.startswith("+++ b/")), None)
    if not fp:
        print(f"skip {pr}: no file path"); results.append(inst); continue
    m = re.match(r"crates/([^/]+)/", fp)
    crate = m.group(1) if m else None
    full = REPO / fp
    if not full.exists():
        print(f"skip {pr}: file missing {fp}"); results.append(inst); continue

    print(f"\n{'='*60}")
    print(f"instance : {inst['instance_id']}")
    print(f"file     : {fp}  ({len(full.read_text().splitlines())} lines)")
    print(f"crate    : {crate}")

    content = full.read_text()
    test_patch, test_names = gen.generate_test_patch(patch, fp, content, str(REPO))

    if test_patch and test_names:
        inst["test_patch"]      = test_patch
        inst["FAIL_TO_PASS"]    = [f"regression_tests::{n}" for n in test_names[:2]]
        inst["PASS_TO_PASS"]    = []
        inst["test_cmd"]        = CRATE_CMD.get(crate, f"CARGO_BUILD_JOBS=1 cargo test -p {crate} --lib --no-fail-fast -- {TEST_SKIP}")
        inst["target_crate"]    = crate
        inst["target_file"]     = fp
        inst["test_generation"] = "llm_runtime"
        print(f"test_names : {test_names}")
        print(f"test_cmd   : {inst['test_cmd']}")
        # Show generated mod body
        lines = test_patch.splitlines()
        in_mod = False
        shown  = 0
        for line in lines:
            stripped = line.lstrip("+")
            if "regression_tests" in stripped or in_mod:
                in_mod = True
                print("  " + stripped)
                shown += 1
                if shown >= 60:
                    print("  ...")
                    break
    else:
        print(f"LLM failed for {pr} — keeping source_analysis tests")

    results.append(inst)

OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(json.dumps(results, indent=2))
print(f"\nWrote {len(results)} instances -> {OUT}")
