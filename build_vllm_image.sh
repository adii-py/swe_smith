#!/bin/bash
# Build optimized vLLM Docker image with all dependencies pre-installed

set -e

echo "========================================"
echo "Building vLLM Docker Image"
echo "========================================"

REPO_ROOT="/Users/aditya.singh.001/Desktop/SWE-smith"
cd "$REPO_ROOT"

# Generate Dockerfile from profile
python3 << 'PYTHON_EOF'
import sys
sys.path.insert(0, '/Users/aditya.singh.001/Desktop/SWE-smith')
from swesmith.profiles import registry

rp = registry.get('vllm-project__vllm.3e1ad443')
dockerfile = rp.dockerfile
image_name = rp.image_name

print(f"Image name: {image_name}")

with open('/tmp/Dockerfile.vllm', 'w') as f:
    f.write(dockerfile)

print("Dockerfile written to /tmp/Dockerfile.vllm")
PYTHON_EOF

echo ""
echo "Starting Docker build (this will take 20-30 minutes)..."
echo ""

# Build the image
docker build \
    -t swebench/swesmith.arm64.vllm-project_1776_vllm.3e1ad443 \
    -f /tmp/Dockerfile.vllm \
    /tmp 2>&1 | tee /tmp/docker_build.log

if [ $? -eq 0 ]; then
    echo ""
    echo "========================================"
    echo "✅ Docker image built successfully!"
    echo "========================================"
    echo "Image: swebench/swesmith.arm64.vllm-project_1776_vllm.3e1ad443"
    docker images | grep vllm-project__vllm.3e1ad443 | head -3
else
    echo ""
    echo "========================================"
    echo "❌ Docker build failed!"
    echo "========================================"
    echo "Check /tmp/docker_build.log for details"
    exit 1
fi
