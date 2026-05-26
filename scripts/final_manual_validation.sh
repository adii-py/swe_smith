#!/bin/bash
# Final manual validation - test 3 instances with detailed logging

set -e

echo "=========================================="
echo "FINAL MANUAL VALIDATION ATTEMPT"
echo "=========================================="
echo ""
echo "Testing 3 instances with comprehensive logging"
echo ""

# Test instance 1: pr_12317 (Analytics)
test_instance_12317() {
    echo "=== TESTING: pr_12317 (Analytics) ==="
    echo "Bug type: IN operator changed to NOT IN"
    echo ""
    
    docker run --rm \
      -v $(pwd)/logs:/workspace/logs \
      hyperswitch-fixed:latest \
      bash << 'EOF'
set -x
cd /testbed

# Reset to clean state
git checkout -- crates/analytics/src/query.rs 2>/dev/null || true

# Add comprehensive test
cat >> crates/analytics/src/query.rs << 'TESTBLOCK'

#[cfg(test)]
mod validation_test {
    use super::*;

    #[test]
    fn test_filter_in_operator() {
        let result = filter_type_to_sql("status", FilterTypes::In, "'active','inactive'");
        assert!(result.contains(" IN "), "Expected ' IN ' in result: {}", result);
    }
}
TESTBLOCK

echo "=== PRE-BUG TEST ==="
cargo test -p analytics --lib validation_test::test_filter_in_operator -- --nocapture 2>&1 | tail -20 &
PID=$!
sleep 120
kill $PID 2>/dev/null || true
wait $PID 2>/dev/null || true

echo "Exit status: $?"

# Apply bug
sed -i 's/IN ({r})/NOT IN ({r})/' crates/analytics/src/query.rs

echo "=== POST-BUG TEST ==="
cargo test -p analytics --lib validation_test::test_filter_in_operator -- --nocapture 2>&1 | tail -20 &
PID=$!
sleep 120
kill $PID 2>/dev/null || true
wait $PID 2>/dev/null || true

echo "Exit status: $?"

EOF
}

# Run tests
echo "Starting Instance 1 test..."
test_instance_12317 2>&1 | tee logs/manual_val_12317.log &
PID1=$!

echo "Monitoring... (PID: $PID1)"
sleep 10
echo "Still running..."
sleep 10
echo "Still running..."

# Kill after 5 minutes total
sleep 280
kill $PID1 2>/dev/null || true
wait $PID1 2>/dev/null || true

echo ""
echo "=========================================="
echo "CHECKING RESULTS"
echo "=========================================="
echo ""

# Check what happened
if [ -f logs/manual_val_12317.log ]; then
    echo "Log file created: logs/manual_val_12317.log"
    echo ""
    echo "Last 30 lines:"
    tail -30 logs/manual_val_12317.log
    
    # Count results
    if grep -q "test validation_test::test_filter_in_operator ... ok" logs/manual_val_12317.log; then
        echo ""
        echo "✅ Test passed at some point!"
    fi
    
    if grep -q "FAILED" logs/manual_val_12317.log; then
        echo ""
        echo "❌ Test failed at some point"
    fi
fi

echo ""
echo "=========================================="
echo "MANUAL VALIDATION COMPLETE"
echo "=========================================="
