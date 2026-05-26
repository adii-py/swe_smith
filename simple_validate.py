#!/usr/bin/env python3
"""Simple validation script for the generated bug."""

import subprocess
import tempfile
import shutil
from pathlib import Path

REPO_PATH = Path("/Users/aditya.singh.001/Desktop/hyperswitch")
BUG_PATCH = Path("/Users/aditya.singh.001/Desktop/SWE-smith/logs/bug_gen/hyperswitch/new_bug/bug__patch_fd3784c0.diff")
TEST_PATCH = Path("/Users/aditya.singh.001/Desktop/SWE-smith/test_patch_fixed.diff")

def run(cmd, cwd=None, timeout=300):
    """Run a command and return success/failure."""
    try:
        result = subprocess.run(
            cmd, shell=True, cwd=cwd, capture_output=True, text=True, timeout=timeout
        )
        return result.returncode == 0, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return False, "", "Timeout"

def main():
    print("=" * 60)
    print("SIMPLE VALIDATION FOR GENERATED BUG")
    print("=" * 60)
    
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        test_repo = tmp_path / "hyperswitch"
        
        # 1. Clone repo
        print("\n[1/5] Cloning repository...")
        ok, out, err = run(f"git clone --quiet {REPO_PATH} {test_repo}")
        if not ok:
            print(f"FAILED: {err}")
            return
        print("✓ Repository cloned")
        
        # 2. Checkout correct commit
        print("\n[2/5] Checking out commit fece9bc3...")
        ok, out, err = run("git fetch origin fece9bc38b9890a1a40912ce2a95037842362e27", cwd=test_repo)
        ok, out, err = run("git checkout fece9bc38b9890a1a40912ce2a95037842362e27", cwd=test_repo)
        if not ok:
            print(f"FAILED: {err}")
            return
        print("✓ Commit checked out")
        
        # 3. Apply bug patch
        print("\n[3/5] Applying bug patch...")
        ok, out, err = run(f"git apply {BUG_PATCH}", cwd=test_repo)
        if not ok:
            print(f"FAILED: {err}")
            return
        print("✓ Bug patch applied")
        
        # 4. Apply test patch
        print("\n[4/5] Applying test patch...")
        ok, out, err = run(f"git apply {TEST_PATCH}", cwd=test_repo)
        if not ok:
            print(f"FAILED: {err}")
            return
        print("✓ Test patch applied")
        
        # 5. Run the test
        print("\n[5/5] Running regression test...")
        print("(This may take 5-10 minutes for first compilation)")
        ok, out, err = run(
            "cargo test --release -p router --lib api_logs_tests::test_timestamp_calculation_uses_division -- --nocapture",
            cwd=test_repo,
            timeout=600
        )
        
        print("\n" + "=" * 60)
        if ok:
            print("RESULT: ✓ TEST PASSED")
            print("The regression test successfully caught the bug!")
        else:
            print("RESULT: ✗ TEST FAILED")
            print("Expected: Test should detect the timestamp bug")
            print("\nSTDOUT:", out[-500:] if len(out) > 500 else out)
            print("\nSTDERR:", err[-500:] if len(err) > 500 else err)
        print("=" * 60)

if __name__ == "__main__":
    main()
