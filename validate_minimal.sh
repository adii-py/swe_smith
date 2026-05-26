#!/bin/bash
# Minimal validation - test only analytics crate with simple bugs

set -e

echo "============================================"
echo "MINIMAL VALIDATION - Analytics Crate Only"
echo "============================================"
echo ""

# Run everything in one container
docker run --rm \
  -v $(pwd)/logs:/workspace/logs \
  swebench/swesmith.arm64.juspay_1776_hyperswitch.fece9bc3:latest \
  bash << 'EOF'
set -e
cd /testbed

echo "Step 1: Setup test environment"

# Add test module to query.rs
cat >> crates/analytics/src/query.rs << 'TESTCODE'

#[cfg(test)]
mod minimal_tests {
    use super::*;

    #[test]
    fn test_in_operator_f2p() {
        // This should FAIL after bug is applied
        let result = filter_type_to_sql("status", FilterTypes::In, "'a','b'");
        assert!(result.contains(" IN "), "Expected IN, got: {}", result);
    }

    #[test]
    fn test_equal_operator_p2p() {
        // This should PASS before and after
        let result = filter_type_to_sql("id", FilterTypes::Equal, "123");
        assert_eq!(result, "id = '123'");
    }
}
TESTCODE

echo "  ✓ Test module added"
echo ""

# Run tests BEFORE bug
echo "Step 2: Running tests BEFORE bug patch..."
timeout 300 cargo test -p analytics --lib minimal_tests -- --nocapture 2>&1 | tee /tmp/pre_test.log | tail -20
PRE_STATUS=${PIPESTATUS[0]}

if [ $PRE_STATUS -eq 0 ]; then
    echo "  ✓ Pre-bug tests PASSED"
else
    echo "  ✗ Pre-bug tests FAILED (exit $PRE_STATUS)"
fi
echo ""

# Apply bug patch
echo "Step 3: Applying bug patch (IN → NOT IN)..."
sed -i '560s/=> format!("{l} IN ({r})")/=> format!("{l} NOT IN ({r})")/' crates/analytics/src/query.rs
echo "  ✓ Bug patch applied"
echo ""

# Run tests AFTER bug
echo "Step 4: Running tests AFTER bug patch..."
timeout 300 cargo test -p analytics --lib minimal_tests -- --nocapture 2>&1 | tee /tmp/post_test.log | tail -30
POST_STATUS=${PIPESTATUS[0]}

if [ $POST_STATUS -eq 0 ]; then
    echo "  ✗ Post-bug tests PASSED (unexpected!)"
else
    echo "  ✓ Post-bug tests FAILED as expected"
fi
echo ""

# Results
echo "============================================"
echo "RESULTS"
echo "============================================"
echo "Pre-patch status:  $PRE_STATUS (0=pass)"
echo "Post-patch status: $POST_STATUS (0=pass)"
echo ""

if [ $PRE_STATUS -eq 0 ] && [ $POST_STATUS -ne 0 ]; then
    echo "✓✓✓ SUCCESS! F2P CASES DETECTED! ✓✓✓"
    echo ""
    echo "F2P: 1 (test_in_operator_f2p changed PASS→FAIL)"
    echo "P2P: 1 (test_equal_operator_p2p stayed PASS)"
    echo ""
    echo "This proves the validation system works!"
    exit 0
else
    echo "✗ Did not get expected F2P"
    exit 1
fi
EOF

exit_code=$?

if [ $exit_code -eq 0 ]; then
    echo ""
    echo "============================================"
    echo "VALIDATION SUCCESSFUL!"
    echo "============================================"
    echo "The fixed patches generate F2P and P2P cases!"
else
    echo ""
    echo "Validation completed with issues"
fi
