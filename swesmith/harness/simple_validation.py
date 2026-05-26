#!/usr/bin/env python3
"""
Simple validation script - tests patches directly and shows F2P/P2P results.
"""

import json
import subprocess
import sys
from pathlib import Path


def run_in_docker(command, timeout=300):
    """Run command in docker container."""
    full_cmd = [
        "docker",
        "run",
        "--rm",
        "swebench/swesmith.arm64.juspay_1776_hyperswitch.fece9bc3:latest",
        "bash",
        "-c",
        command,
    ]
    try:
        result = subprocess.run(
            full_cmd, capture_output=True, text=True, timeout=timeout
        )
        return result.returncode, result.stdout + result.stderr
    except subprocess.TimeoutExpired:
        return -1, "Timeout"
    except Exception as e:
        return -1, str(e)


def test_instance(instance):
    """Test a single instance and return results."""
    instance_id = instance.get("instance_id", "unknown")
    patch = instance.get("patch", "")
    test_patch = instance.get("test_patch", "")

    print(f"\n{'=' * 60}")
    print(f"Testing: {instance_id}")
    print(f"{'=' * 60}")

    # Build test script
    test_script = f"""
cd /testbed

# Step 1: Apply test patch
cat > /tmp/test.diff << 'EOF'
{test_patch}
EOF

git apply /tmp/test.diff 2>&1
if [ $? -ne 0 ]; then
    echo "ERROR: Failed to apply test patch"
    exit 1
fi

# Step 2: Run tests BEFORE bug patch (baseline)
echo ""
echo "=== PRE-BUG TEST RUN ==="
timeout 180 cargo test -p analytics --lib validation_tests -- --nocapture 2>&1 | tail -30
PRE_EXIT=${PIPESTATUS[0]}
echo "PRE_TEST_EXIT: $PRE_EXIT"

# Step 3: Apply bug patch
cat > /tmp/bug.diff << 'EOF'
{patch}
EOF

git apply /tmp/bug.diff 2>&1
if [ $? -ne 0 ]; then
    echo "ERROR: Failed to apply bug patch"
    exit 1
fi

# Step 4: Run tests AFTER bug patch
echo ""
echo "=== POST-BUG TEST RUN ==="
timeout 180 cargo test -p analytics --lib validation_tests -- --nocapture 2>&1 | tail -30
POST_EXIT=${PIPESTATUS[0]}
echo "POST_TEST_EXIT: $POST_EXIT"

# Summary
echo ""
echo "=== RESULTS ==="
echo "Pre-patch exit: $PRE_EXIT"
echo "Post-patch exit: $POST_EXIT"

if [ $PRE_EXIT -eq 0 ] && [ $POST_EXIT -ne 0 ]; then
    echo "STATUS: SUCCESS (F2P detected)"
elif [ $PRE_EXIT -eq 0 ] && [ $POST_EXIT -eq 0 ]; then
    echo "STATUS: PARTIAL (P2P only)"
else
    echo "STATUS: FAIL"
fi
"""

    print("  Running tests (this may take 3-5 minutes)...")
    exit_code, output = run_in_docker(test_script, timeout=600)

    # Parse results
    pre_exit = None
    post_exit = None
    status = "unknown"

    for line in output.split("\n"):
        if "PRE_TEST_EXIT:" in line:
            try:
                pre_exit = int(line.split(":")[1].strip())
            except:
                pass
        elif "POST_TEST_EXIT:" in line:
            try:
                post_exit = int(line.split(":")[1].strip())
            except:
                pass
        elif "STATUS:" in line:
            status = line.split(":")[1].strip()

    # Calculate F2P/P2P
    f2p = 0
    p2p = 0

    if pre_exit is not None and post_exit is not None:
        if pre_exit == 0 and post_exit != 0:
            f2p = 1  # At least 1 test failed after patch
            p2p = 1  # At least 1 test still passes
        elif pre_exit == 0 and post_exit == 0:
            p2p = 2  # All tests pass both times

    print(f"  Pre-patch: {'PASS' if pre_exit == 0 else 'FAIL'}")
    print(f"  Post-patch: {'PASS' if post_exit == 0 else 'FAIL'}")
    print(f"  Status: {status}")
    print(f"  F2P: {f2p}, P2P: {p2p}")

    return {
        "instance_id": instance_id,
        "pre_exit": pre_exit,
        "post_exit": post_exit,
        "f2p": f2p,
        "p2p": p2p,
        "status": status,
        "output": output,
    }


def main():
    if len(sys.argv) < 2:
        print("Usage: python simple_validation.py <json_file>")
        sys.exit(1)

    json_file = sys.argv[1]

    print(f"Loading instances from: {json_file}")
    with open(json_file, "r") as f:
        instances = json.load(f)

    print(f"Loaded {len(instances)} instances\n")

    results = []
    for instance in instances:
        result = test_instance(instance)
        results.append(result)

    # Summary
    print(f"\n{'=' * 60}")
    print("FINAL SUMMARY")
    print(f"{'=' * 60}")

    total_f2p = sum(r["f2p"] for r in results)
    total_p2p = sum(r["p2p"] for r in results)
    success_count = sum(1 for r in results if r["f2p"] > 0)

    for r in results:
        print(f"\n{r['instance_id']}:")
        print(f"  Status: {r['status']}")
        print(f"  F2P: {r['f2p']}, P2P: {r['p2p']}")

    print(f"\n{'=' * 60}")
    print(f"Total instances: {len(results)}")
    print(f"Instances with F2P: {success_count}")
    print(f"Total F2P cases: {total_f2p}")
    print(f"Total P2P cases: {total_p2p}")
    print(f"{'=' * 60}")

    # Save results
    output_file = json_file.replace(".json", "_results.json")
    with open(output_file, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nResults saved to: {output_file}")


if __name__ == "__main__":
    main()
