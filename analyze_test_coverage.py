#!/usr/bin/env python3
"""
Analyze which bug-fix patches have existing test coverage that could produce f2p.
Maps each patch to test files in the same crate.
"""

import json
import re
from pathlib import Path
from unidiff import PatchSet

REPO_PATH = Path("/tmp/hyperswitch")
DATASET_PATH = "/Users/aditya.singh.001/Desktop/SWE-smith/logs/bug_gen/juspay__hyperswitch.fece9bc3/pr_mirror/recovered_dataset_clean.json"


def find_crate_for_file(filepath: str) -> str | None:
    """Given a filepath like 'crates/router/src/webhooks.rs', return 'router'."""
    parts = filepath.split("/")
    if len(parts) >= 2 and parts[0] == "crates":
        return parts[1]
    return None


def find_test_files_in_crate(crate: str) -> list[Path]:
    """Find all test files in a crate."""
    crate_path = REPO_PATH / "crates" / crate
    if not crate_path.exists():
        return []
    test_files = []
    for p in crate_path.rglob("*.rs"):
        if "test" in p.name.lower() or p.parent.name in ("tests", "test"):
            test_files.append(p)
    return test_files


def extract_changed_functions(patch_text: str) -> list[tuple[str, str]]:
    """Extract (filepath, function_name) pairs from a patch."""
    results = []
    try:
        ps = PatchSet(patch_text)
        for pf in ps:
            fname = pf.path
            for hunk in pf:
                for line in hunk:
                    if line.is_added or line.is_removed:
                        # Try to find function name from context
                        m = re.search(r'fn\s+(\w+)', line.value)
                        if m:
                            results.append((fname, m.group(1)))
    except Exception:
        pass
    return results


def main():
    with open(DATASET_PATH) as f:
        data = json.load(f)

    # Sort: fix PRs first
    fix_prs = [inst for inst in data if inst["title"].lower().startswith("fix")]
    other_prs = [inst for inst in data if not inst["title"].lower().startswith("fix")]

    print(f"Analyzing {len(fix_prs)} bug-fix PRs + {len(other_prs)} others = {len(data)} total")
    print("=" * 80)

    with_tests = []
    without_tests = []

    for inst in fix_prs + other_prs:
        iid = inst["instance_id"]
        pr_num = inst["pull_number"]
        title = inst["title"]
        patch = inst.get("patch", "")

        try:
            ps = PatchSet(patch)
        except Exception as e:
            print(f"ERROR parsing {iid}: {e}")
            continue

        files = [pf.path for pf in ps]
        crates = set()
        for f in files:
            c = find_crate_for_file(f)
            if c:
                crates.add(c)

        test_files = []
        for c in crates:
            test_files.extend(find_test_files_in_crate(c))

        # Also check if patch touches test files directly
        touched_test_files = [f for f in files if "test" in f.lower()]

        entry = {
            "iid": iid,
            "pr": pr_num,
            "title": title,
            "files": files,
            "crates": sorted(crates),
            "test_files": [str(t.relative_to(REPO_PATH)) for t in test_files],
            "touched_tests": touched_test_files,
            "has_tests": len(test_files) > 0 or len(touched_test_files) > 0,
        }

        if entry["has_tests"]:
            with_tests.append(entry)
        else:
            without_tests.append(entry)

    print(f"\nINSTANCES WITH TEST FILES IN SAME CRATE: {len(with_tests)}/{len(data)}")
    for e in with_tests[:20]:
        test_count = len(e["test_files"])
        tt = "YES" if e["touched_tests"] else "no"
        print(f"  PR #{e['pr']}: {e['title'][:65]}")
        print(f"    Files: {e['files']}")
        print(f"    Crates: {e['crates']} | Test files: {test_count} | Patch touches tests: {tt}")

    print(f"\nINSTANCES WITHOUT TEST FILES IN SAME CRATE: {len(without_tests)}/{len(data)}")
    for e in without_tests:
        print(f"  PR #{e['pr']}: {e['title'][:75]}")
        print(f"    Files: {e['files']}")

    # Save detailed report
    report_path = "/Users/aditya.singh.001/Desktop/SWE-smith/test_coverage_analysis.json"
    with open(report_path, "w") as f:
        json.dump({"with_tests": with_tests, "without_tests": without_tests}, f, indent=2)
    print(f"\nDetailed report saved to: {report_path}")


if __name__ == "__main__":
    main()
