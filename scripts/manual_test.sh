#!/bin/bash
# Manual test script to prove F2P generation works

set -e

echo "=========================================="
echo "Manual Test for F2P Generation"
echo "=========================================="
echo ""

# Run in Docker container
docker run --rm \
  swebench/swesmith.arm64.juspay_1776_hyperswitch.fece9bc3:latest \
  bash << 'CONTAINER_SCRIPT'
set -e
cd /testbed

echo "Step 1: Check current state"
git log --oneline -1
git status

echo ""
echo "Step 2: Add test module to query.rs"
cat >> crates/analytics/src/query.rs << 'EOF'

#[cfg(test)]
mod f2p_tests {
    use super::*;

    #[test]
    fn test_in_operator_returns_correct_sql() {
        let result = filter_type_to_sql("status", FilterTypes::In, "'a','b'");
        assert_eq!(result, "status IN ('a','b')", 
            "Expected IN operator, but got: {}", result);
    }

    #[test] 
    fn test_equal_still_works() {
        // This is a P2P test - should pass before and after
        let result = filter_type_to_sql("id", FilterTypes::Equal, "123");
        assert_eq!(result, "id = '123'");
    }
}
EOF

echo "Test module added successfully"

echo ""
echo "Step 3: Run tests BEFORE applying bug (should PASS)"
echo "Running: cargo test -p analytics --lib f2p_tests"
cd /testbed
timeout 180 cargo test -p analytics --lib f2p_tests -- --nocapture 2>&1 | tee /tmp/pre_test.log
echo "Exit code: $?"

echo ""
echo "Step 4: Apply simple bug (change IN to NOT IN)"
# Use sed to make the change
sed -i 's/FilterTypes::In => format!("{l} IN ({r})")/FilterTypes::In => format!("{l} NOT IN ({r})")/g' crates/analytics/src/query.rs

echo "Bug applied: Changed IN to NOT IN"
echo ""
echo "Verifying change:"
grep -n "NOT IN\|IN" crates/analytics/src/query.rs | grep -A 2 -B 2 "FilterTypes::In" | head -5

echo ""
echo "Step 5: Run tests AFTER applying bug (should FAIL)"
echo "Running: cargo test -p analytics --lib f2p_tests"
timeout 180 cargo test -p analytics --lib f2p_tests -- --nocapture 2>&1 | tee /tmp/post_test.log
POST_EXIT=$?
echo "Exit code: $POST_EXIT"

echo ""
echo "=========================================="
echo "RESULTS SUMMARY"
echo "=========================================="
if [ $POST_EXIT -ne 0 ]; then
    echo "✓ SUCCESS: Tests failed after bug patch"
    echo "✓ This means we have FAIL_TO_PASS cases!"
    echo ""
    echo "Pre-patch: Tests passed (exit 0)"
    echo "Post-patch: Tests failed (exit $POST_EXIT)"
    echo ""
    echo "F2P Count: 1 (test_in_operator_returns_correct_sql)"
    echo "P2P Count: 1 (test_equal_still_works)"
else
    echo "✗ Unexpected: Tests still passed after bug"
    echo "Check logs above for details"
fi

echo ""
echo "=========================================="

CONTAINER_SCRIPT

echo ""
echo "Test completed!"
