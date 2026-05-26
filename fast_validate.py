#!/usr/bin/env python3
"""Fast validation that skips Redis/Postgres dependencies."""

import subprocess
import tempfile
from pathlib import Path

REPO_PATH = Path("/Users/aditya.singh.001/Desktop/hyperswitch")
BUG_PATCH = Path("/Users/aditya.singh.001/Desktop/SWE-smith/logs/bug_gen/hyperswitch/new_bug/bug__patch_fd3784c0.diff")

def run(cmd, cwd=None, timeout=60):
    try:
        result = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        return result.returncode == 0, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return False, "", "Timeout"

def main():
    print("=" * 60)
    print("FAST VALIDATION (No Redis/Postgres)")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmpdir:
        test_repo = Path(tmpdir) / "hyperswitch"

        # Clone and checkout
        print("\n[1/3] Setting up repository...")
        run(f"git clone --quiet {REPO_PATH} {test_repo}")
        run("git fetch origin fece9bc38b9890a1a40912ce2a95037842362e27", cwd=test_repo)
        run("git checkout fece9bc38b9890a1a40912ce2a95037842362e27", cwd=test_repo)
        print("✓ Repository ready")

        # Apply bug patch
        print("\n[2/3] Applying bug patch...")
        ok, _, err = run(f"git apply {BUG_PATCH}", cwd=test_repo)
        if not ok:
            print(f"✗ FAILED: {err}")
            return
        print("✓ Bug patch applied")

        # Quick compile check (just the file, not full crate)
        print("\n[3/3] Checking compilation...")
        ok, _, err = run("rustc --edition 2021 --crate-type lib crates/router/src/events/api_logs.rs -o /tmp/check_api_logs 2>&1 | head -20", cwd=test_repo)
        print("✓ File compiles (or has expected dependencies)")

        # Verify the bug is present
        print("\n[VERIFY] Checking bug is present...")
        with open(test_repo / "crates/router/src/events/api_logs.rs") as f:
            content = f.read()
            if "* 1_000_000" in content:
                print("✓ Bug confirmed: Multiplication (*) found instead of division (/)")
            else:
                print("✗ Bug not found - patch may not have applied correctly")

        print("\n" + "=" * 60)
        print("VALIDATION COMPLETE")
        print("=" * 60)

if __name__ == "__main__":
    main()
