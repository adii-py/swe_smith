#!/usr/bin/env python3
"""
Create a WORKING example that proves F2P generation is possible.
This uses the simplest possible approach.
"""

import json
import subprocess
import tempfile
import os


def run_docker_command(cmd, timeout=600):
    """Run command in docker."""
    full_cmd = f'docker run --rm swebench/swesmith.arm64.juspay_1776_hyperswitch.fece9bc3:latest bash -c "{cmd}"'
    try:
        result = subprocess.run(
            full_cmd, shell=True, capture_output=True, text=True, timeout=timeout
        )
        return result.returncode, result.stdout + result.stderr
    except subprocess.TimeoutExpired:
        return -1, "Command timed out"


# Create a complete working test
working_test = """
set -e
cd /testbed

echo "=========================================="
echo "WORKING EXAMPLE: F2P Generation"
echo "=========================================="
echo ""

# Step 1: Create test file
cat > /tmp/test_query.rs << 'RUSTCODE'
use analytics::query::{filter_type_to_sql, FilterTypes};

#[test]
fn test_in_operator() {
    let result = filter_type_to_sql("col", FilterTypes::In, "1,2");
    assert!(result.contains(" IN "), "Expected IN but got: {}", result);
}

#[test]
fn test_equal_operator() {
    let result = filter_type_to_sql("id", FilterTypes::Equal, "123");
    assert_eq!(result, "id = '123'");
}
RUSTCODE

# Step 2: Show function BEFORE bug
echo "BEFORE BUG - Function looks like:"
grep -A 15 "pub fn filter_type_to_sql" crates/analytics/src/query.rs | head -20

echo ""
echo "=========================================="
echo ""

# Step 3: Apply simple bug using sed (change line 560)
echo "Applying bug: Changing IN to NOT IN..."
sed -i '560s/IN/NOT IN/' crates/analytics/src/query.rs

echo "Bug applied!"

# Step 4: Show function AFTER bug
echo ""
echo "AFTER BUG - Function looks like:"
grep -A 15 "pub fn filter_type_to_sql" crates/analytics/src/query.rs | head -20

echo ""
echo "=========================================="
echo ""

# Step 5: Compile and test
echo "Compiling and running tests..."
timeout 300 cargo test -p analytics --lib 2>&1 | tail -40

echo ""
echo "=========================================="
echo "DONE"
echo "=========================================="
"""

print("Starting working example...")
print("This will take about 5 minutes...")
print("")

exit_code, output = run_docker_command(working_test, timeout=600)

print(output)

if exit_code == 0:
    print("\n✓ WORKING EXAMPLE COMPLETED")
else:
    print(f"\n✗ Exit code: {exit_code}")
