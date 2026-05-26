#!/usr/bin/env python3
"""Merge validation report.json F2P/P2P into pilot dataset files."""
import json
from pathlib import Path

from swebench.harness.constants import (
    FAIL_TO_FAIL,
    FAIL_TO_PASS,
    LOG_REPORT,
    PASS_TO_FAIL,
    PASS_TO_PASS,
)
from swesmith.constants import LOG_DIR_RUN_VALIDATION

ROOT = Path(__file__).resolve().parents[1]
PILOT = ROOT / "logs/bug_gen/juspay__hyperswitch.fece9bc3/pilot_2x2/pilot_instances.json"
OUT_VALIDATED = ROOT / "logs/bug_gen/juspay__hyperswitch.fece9bc3/pilot_2x2/pilot_validated.json"
OUT_DATA = ROOT / "data/hyperswitch_pilot_validated.json"


def _prefix_test_names(names: list[str]) -> list[str]:
    """Normalize to common_utils:: paths like pr_12234 gold dataset."""
    out = []
    for n in names:
        if n.startswith("common_utils::"):
            out.append(n)
        else:
            out.append(f"common_utils::{n}")
    return sorted(set(out))


def main():
    instances = json.loads(PILOT.read_text())
    updated = []
    for inst in instances:
        inst_id = inst["instance_id"]
        report_path = LOG_DIR_RUN_VALIDATION / inst["repo"] / inst_id / LOG_REPORT
        row = {**inst}
        if report_path.exists():
            report = json.loads(report_path.read_text())
            row["FAIL_TO_PASS"] = _prefix_test_names(report.get(FAIL_TO_PASS, []))
            row["PASS_TO_PASS"] = _prefix_test_names(report.get(PASS_TO_PASS, []))
            row["validation_status"] = (
                "success" if len(row["FAIL_TO_PASS"]) >= 2 else "partial"
            )
            row["metrics"] = {
                "FAIL_TO_PASS": len(row["FAIL_TO_PASS"]),
                "PASS_TO_PASS": len(row["PASS_TO_PASS"]),
                "FAIL_TO_FAIL": len(report.get(FAIL_TO_FAIL, [])),
                "PASS_TO_FAIL": len(report.get(PASS_TO_FAIL, [])),
            }
        updated.append(row)

    OUT_VALIDATED.parent.mkdir(parents=True, exist_ok=True)
    OUT_VALIDATED.write_text(json.dumps(updated, indent=2))
    PILOT.write_text(json.dumps(updated, indent=2))
    OUT_DATA.write_text(json.dumps(updated, indent=2))

    print(f"Wrote {OUT_VALIDATED}")
    print(f"Updated {PILOT}")
    print(f"Wrote {OUT_DATA}")
    for row in updated:
        m = row.get("metrics", {})
        print(
            f"  {row['instance_id']}: F2P={m.get('FAIL_TO_PASS', '?')} "
            f"P2P={m.get('PASS_TO_PASS', '?')} status={row.get('validation_status', '?')}"
        )


if __name__ == "__main__":
    main()
