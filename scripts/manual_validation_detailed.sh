#!/bin/bash
# Manual validation - step by step for 3-5 instances

set -e

echo "=========================================="
echo "MANUAL VALIDATION - Step by Step"
echo "=========================================="
echo ""

# Instance 1: Analytics - pr_12317 (IN operator bug)
echo "=== INSTANCE 1: pr_12317 (Analytics - IN operator) ==="
echo ""

docker run --rm -it \
  --name manual_val_12317 \
  -v $(pwd)/logs:/workspace/logs \
  hyperswitch-fixed:latest \
  bash << 'EOF1'
set -e
cd /testbed

echo "Step 1: Check current state"
git status
git log --oneline -1

echo ""
echo "Step 2: View original filter_type_to_sql function (lines 555-572)"
sed -n '555,572p' crates/analytics/src/query.rs

echo ""
echo "Step 3: Apply test patch (add validation tests)"
cat >> crates/analytics/src/query.rs << 'TESTCODE'

#[cfg(test)]
mod manual_validation_tests {
    use super::*;

    #[test]
    fn test_in_operator_f2p() {
        // This should FAIL after bug patch
        let result = filter_type_to_sql("col", FilterTypes::In, "1,2");
        assert!(result.contains(" IN "), "F2P: Expected IN, got: {}", result);
        assert!(!result.contains("NOT IN"), "F2P: Should not be NOT IN");
    }

    #[test]
    fn test_equal_operator_p2p() {
        // This should PASS before and after
        let result = filter_type_to_sql("id", FilterTypes::Equal, "123");
        assert_eq!(result, "id = '123'", "P2P: Equal operator");
    }

    #[test]
    fn test_gt_operator_p2p() {
        // This should PASS before and after
        let result = filter_type_to_sql("amt", FilterTypes::Gt, "100");
        assert!(result.contains(">"), "P2P: Gt operator, got: {}", result);
    }
}
TESTCODE

echo "Tests added!"

echo ""
echo "Step 4: Run tests BEFORE applying bug patch (should PASS)"
timeout 300 cargo test -p analytics --lib manual_validation_tests -- --nocapture 2>&1 | tail -30 || echo "Tests completed"
PRE_TEST_EXIT=${PIPESTATUS[0]}

echo ""
echo "Pre-bug test exit code: $PRE_TEST_EXIT"

echo ""
echo "Step 5: Apply bug patch (change IN to NOT IN)"
sed -i '560s/FilterTypes::In => format!("{l} IN ({r})")/FilterTypes::In => format!("{l} NOT IN ({r})")/' crates/analytics/src/query.rs

echo "Bug patch applied!"
echo "Modified line:"
sed -n '560p' crates/analytics/src/query.rs

echo ""
echo "Step 6: Run tests AFTER applying bug patch (should FAIL)"
timeout 300 cargo test -p analytics --lib manual_validation_tests -- --nocapture 2>&1 | tail -40 || echo "Tests completed"
POST_TEST_EXIT=${PIPESTATUS[0]}

echo ""
echo "=========================================="
echo "RESULTS for pr_12317:"
echo "  Pre-bug exit code: $PRE_TEST_EXIT"
echo "  Post-bug exit code: $POST_TEST_EXIT"
echo ""

if [ $PRE_TEST_EXIT -eq 0 ] && [ $POST_TEST_EXIT -ne 0 ]; then
    echo "  ✅ SUCCESS!"
    echo "  F2P: 1 (test_in_operator_f2p)"
    echo "  P2P: 2 (test_equal_operator, test_gt_operator)"
elif [ $PRE_TEST_EXIT -eq 0 ] && [ $POST_TEST_EXIT -eq 0 ]; then
    echo "  ⚠️ Tests still passing - bug may not be detected"
else
    echo "  ❌ Issues occurred"
fi

echo "=========================================="

EOF1

echo ""
echo "Instance 1 complete!"
echo ""

# Check if we should continue with more instances
echo "Press Enter to continue with Instance 2, or Ctrl+C to stop..."
read -r

echo "=== INSTANCE 2: pr_12315 (Analytics - Equal operator) ==="
echo ""

docker run --rm -it \
  --name manual_val_12315 \
  -v $(pwd)/logs:/workspace/logs \
  hyperswitch-fixed:latest \
  bash << 'EOF2'
set -e
cd /testbed

echo "Applying test patch for Equal operator bug..."
cat >> crates/analytics/src/query.rs << 'TESTCODE'

#[cfg(test)]
mod manual_validation_tests_12315 {
    use super::*;

    #[test]
    fn test_equal_f2p() {
        let result = filter_type_to_sql("id", FilterTypes::Equal, "123");
        assert_eq!(result, "id = '123'", "Expected = operator");
    }

    #[test]
    fn test_in_p2p() {
        let result = filter_type_to_sql("col", FilterTypes::In, "1,2");
        assert!(result.contains(" IN "), "IN should work");
    }
}
TESTCODE

echo "Running pre-bug tests..."
timeout 300 cargo test -p analytics --lib manual_validation_tests_12315 -- --nocapture 2>&1 | tail -20
PRE=$?

echo "Applying bug (Equal -> NotEqual)..."
sed -i '558s/=> format!("{l} = '"'"'{r}'"'"'")/=> format!("{l} != '"'"'{r}'"'"'")/' crates/analytics/src/query.rs

echo "Running post-bug tests..."
timeout 300 cargo test -p analytics --lib manual_validation_tests_12315 -- --nocapture 2>&1 | tail -30
POST=$?

echo ""
echo "Results: PRE=$PRE, POST=$POST"
if [ $PRE -eq 0 ] && [ $POST -ne 0 ]; then
    echo "✅ SUCCESS - F2P detected!"
fi

EOF2

echo ""
echo "=========================================="
echo "MANUAL VALIDATION COMPLETE"
echo "=========================================="
