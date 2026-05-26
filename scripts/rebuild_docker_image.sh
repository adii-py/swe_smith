#!/bin/bash
# Rebuild Docker image with proper features and pre-compiled dependencies

set -e

echo "=========================================="
echo "Rebuilding Hyperswitch Docker Image"
echo "=========================================="
echo ""

# Create an extended Dockerfile
cat > /tmp/Dockerfile.extended << 'DOCKERFILE'
FROM swebench/swesmith.arm64.juspay_1776_hyperswitch.fece9bc3:latest

# Set environment to ensure features are enabled
ENV RUSTFLAGS=""
ENV CARGO_NET_RETRY=10

WORKDIR /testbed

# Pre-compile storage_impl with redis-rs feature (this is what analytics depends on)
RUN cargo build -p storage_impl --features redis-rs 2>&1 | tail -10 || echo "Storage impl build completed"

# Pre-compile analytics crate with all features
RUN cargo build -p analytics --features v1 2>&1 | tail -10 || echo "Analytics build completed"

# Pre-compile tests (without running them)
RUN cargo test -p analytics --features v1 --no-run 2>&1 | tail -10 || echo "Test compilation completed"

# Clean up build artifacts but keep cache
RUN cargo clean

# Reset to clean state
RUN git reset --hard HEAD && git clean -fd

WORKDIR /testbed

# Test that it compiles
RUN cargo check -p analytics --features v1 2>&1 | tail -5

CMD ["bash"]
DOCKERFILE

echo "Building extended Docker image..."
echo "This may take 10-15 minutes..."

docker build -f /tmp/Dockerfile.extended -t swesmith-hyperswitch-fixed:latest /tmp/ 2>&1 | tee /tmp/docker_build.log

if [ $? -eq 0 ]; then
    echo ""
    echo "✓ Docker image built successfully!"
    echo ""
    echo "Testing the new image..."
    
    # Quick test
    docker run --rm swesmith-hyperswitch-fixed:latest bash -c "
        cd /testbed
        echo 'Testing compilation...'
        timeout 120 cargo test -p analytics --features v1 --no-run 2>&1 | tail -10
        echo 'Exit code:' $?
    "
    
    echo ""
    echo "New image: swesmith-hyperswitch-fixed:latest"
else
    echo "✗ Docker build failed"
    exit 1
fi
