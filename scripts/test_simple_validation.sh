#!/bin/bash
# Simple validation test - focus on what works

set -e

echo "Testing simple validation approach"
echo "===================================="
echo ""

# Test 1: Just check if cargo can parse the manifest
echo "Test 1: Check if manifest parses..."
docker run --rm hyperswitch-fixed:latest bash -c "
cd /testbed
timeout 30 cargo check --manifest-path crates/analytics/Cargo.toml --no-default-features 2>&1 | head -20
echo \"Exit code: \$?\"
"

echo ""
echo "Test 2: Try building with all features..."
docker run --rm hyperswitch-fixed:latest bash -c "
cd /testbed
timeout 60 cargo check -p analytics --all-features 2>&1 | grep -E '(error|Finished)' | head -20
echo \"Exit code: \$?\"
"

echo ""
echo "===================================="
echo "Done!"
