#!/bin/bash
# Full solution: Fix Docker image, create sed-based validation, generate test patches

set -e

echo "=========================================="
echo "FULL SOLUTION FOR F2P/P2P GENERATION"
echo "=========================================="
echo ""

# Step 1: Build fixed Docker image with proper features
echo "Step 1: Building fixed Docker image..."

cat > /tmp/Dockerfile.final << 'DOCKERFILE'
FROM swebench/swesmith.arm64.juspay_1776_hyperswitch.fece9bc3:latest

WORKDIR /testbed

# Fix analytics Cargo.toml to properly enable redis-rs
RUN sed -i 's/storage_impl = { version = "0.1.0", path = "..\/storage_impl", default-features = false }/storage_impl = { version = "0.1.0", path = "..\/storage_impl", default-features = false, features = ["redis-rs"] }/' crates/analytics/Cargo.toml

# Pre-compile to cache dependencies
RUN cargo build -p analytics --features v1 2>&1 | tail -5 || true

WORKDIR /testbed
DOCKERFILE

docker build -f /tmp/Dockerfile.final -t hyperswitch-fixed:latest /tmp/ 2>&1 | tail -20

echo "✓ Fixed Docker image built"
echo ""

# Step 2: Create validation script using sed instead of patches
echo "Step 2: Creating sed-based validation..."

cat > /tmp/validate_with_sed.sh << 'SCRIPT'
#!/bin/bash
# Validate instances using sed to apply bugs (avoids patch parsing issues)

INSTANCE_ID=$1
BUG_TYPE=$2  # "in", "equal", or "gt"

echo "Validating: $INSTANCE_ID with bug type: $BUG_TYPE"

cd /testbed

# Add test module
cat >> crates/analytics/src/query.rs << 'EOF'

#[cfg(test)]
mod validation_tests {
    use super::*;

    #[test]
    fn test_f2p_in() {
        let result = filter_type_to_sql("col", FilterTypes::In, "1,2");
        assert!(result.contains(" IN "), "F2P: Expected IN, got {}", result);
    }

    #[test]
    fn test_f2p_equal() {
        let result = filter_type_to_sql("id", FilterTypes::Equal, "123");
        assert_eq!(result, "id = '123'", "F2P: Expected = operator");
    }

    #[test]
    fn test_f2p_gt() {
        let result = filter_type_to_sql("amt", FilterTypes::Gt, "100");
        assert!(result.contains(">"), "F2P: Expected > operator, got {}", result);
    }

    #[test]
    fn test_p2p_gte() {
        let result = filter_type_to_sql("amt", FilterTypes::Gte, "100");
        assert!(result.contains(">="), "P2P: Gte should work, got {}", result);
    }

    #[test]
    fn test_p2p_notequal() {
        let result = filter_type_to_sql("id", FilterTypes::NotEqual, "123");
        assert!(result.contains("!="), "P2P: NotEqual should work, got {}", result);
    }
}
EOF

# Run pre-bug tests
echo "Running pre-bug tests..."
timeout 300 cargo test -p analytics --features v1 --lib validation_tests 2>&1 | tail -30
PRE_EXIT=${PIPESTATUS[0]}

# Apply bug based on type
echo "Applying bug: $BUG_TYPE"
case $BUG_TYPE in
    "in")
        sed -i 's/FilterTypes::In => format!("{l} IN ({r})")/FilterTypes::In => format!("{l} NOT IN ({r})")/' crates/analytics/src/query.rs
        ;;
    "equal")
        sed -i 's/FilterTypes::Equal => format!("{l} = '"'"'{r}'"'"'")/FilterTypes::Equal => format!("{l} != '"'"'{r}'"'"'")/' crates/analytics/src/query.rs
        ;;
    "gt")
        sed -i 's/FilterTypes::Gt => format!("{l} > {r}")/FilterTypes::Gt => format!("{l} < {r}")/' crates/analytics/src/query.rs
        ;;
esac

# Run post-bug tests
echo "Running post-bug tests..."
timeout 300 cargo test -p analytics --features v1 --lib validation_tests 2>&1 | tail -30
POST_EXIT=${PIPESTATUS[0]}

# Output results
echo "RESULTS: PRE=$PRE_EXIT POST=$POST_EXIT"
if [ $PRE_EXIT -eq 0 ] && [ $POST_EXIT -ne 0 ]; then
    echo "STATUS: SUCCESS_WITH_F2P"
elif [ $PRE_EXIT -eq 0 ] && [ $POST_EXIT -eq 0 ]; then
    echo "STATUS: SUCCESS_P2P_ONLY"
else
    echo "STATUS: FAIL"
fi
SCRIPT

chmod +x /tmp/validate_with_sed.sh

echo "✓ Validation script created"
echo ""

# Step 3: Test with first instance
echo "Step 3: Testing with pr_12317 (IN -> NOT IN bug)..."
docker run --rm \
  -v /tmp/validate_with_sed.sh:/tmp/validate.sh:ro \
  hyperswitch-fixed:latest \
  bash /tmp/validate.sh "pr_12317" "in" 2>&1 | tee /tmp/test_output.log

if grep -q "STATUS: SUCCESS_WITH_F2P" /tmp/test_output.log; then
    echo ""
    echo "✓✓✓ SUCCESS! F2P cases generated! ✓✓✓"
    echo ""
    echo "Now scaling to all instances..."
else
    echo ""
    echo "Test results:"
    tail -20 /tmp/test_output.log
fi
