#!/usr/bin/env python3
"""Check which router test files import the modules modified by each patch."""

import json
from pathlib import Path
from unidiff import PatchSet

REPO = Path("/tmp/hyperswitch")
DATASET = "/Users/aditya.singh.001/Desktop/SWE-smith/logs/bug_gen/juspay__hyperswitch.fece9bc3/pr_mirror/recovered_dataset_clean.json"


def get_module_path_from_file(filepath: str) -> str:
    """Convert 'crates/router/src/core/payments.rs' to router::core::payments."""
    parts = filepath.replace(".rs", "").split("/")
    if parts[0] == "crates" and len(parts) >= 3:
        # crates/router/src/core/payments.rs -> router::core::payments
        crate = parts[1]
        mod_parts = parts[3:]  # skip crates/X/src/
        return "::".join([crate] + mod_parts)
    return filepath


def test_file_imports_module(test_path: Path, module: str) -> bool:
    """Check if a test file imports or references a module."""
    try:
        content = test_path.read_text()
        # Module could be referenced in various ways
        parts = module.split("::")
        # Check for use statements or direct references
        for i in range(len(parts)):
            prefix = "::".join(parts[i:])
            if prefix in content:
                return True
        # Also check for the file stem as a module name
        if parts[-1] in content:
            return True
    except Exception:
        pass
    return False


def main():
    with open(DATASET) as f:
        data = json.load(f)

    router_tests = list((REPO / "crates" / "router" / "tests").rglob("*.rs"))
    print(f"Router test files found: {len(router_tests)}")
    print()

    for inst in data:
        if "router" not in inst.get("test_cmd", ""):
            continue

        patch = inst.get("patch", "")
        try:
            ps = PatchSet(patch)
        except Exception:
            continue

        files = [pf.path for pf in ps]
        modules = [get_module_path_from_file(f) for f in files]

        matched_tests = []
        for test_file in router_tests:
            for mod in modules:
                if test_file_imports_module(test_file, mod):
                    matched_tests.append(str(test_file.relative_to(REPO)))
                    break

        status = "LIKELY COVERED" if matched_tests else "NO TEST COVERAGE"
        print(f"PR #{inst['pull_number']}: {inst['title'][:70]}")
        print(f"  Modules: {modules}")
        print(f"  Matched tests: {matched_tests if matched_tests else 'NONE'}")
        print(f"  Status: {status}")
        print()


if __name__ == "__main__":
    main()
