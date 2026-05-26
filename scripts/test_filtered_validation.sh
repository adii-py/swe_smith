#!/bin/bash
# Test validation with filtered tests (no Redis)

set -e

echo "=========================================="
echo "TESTING FILTERED VALIDATION (No Redis)"
echo "=========================================="
echo ""

# Test instance: pr_10949
echo "=== Testing: pr_10949 ==="
echo "Bug: Authorization error type change"
echo ""

# Extract just this instance
python3 << 'PYEOF'
import json
with open('logs/bug_gen/juspay__hyperswitch.fece9bc3/pr_mirror/pr_mirror_filtered_tests.json') as f:
    data = json.load(f)

# Find pr_10949
for inst in data:
    if 'pr_10949' in inst['instance_id']:
        with open('/tmp/test_pr_10949.json', 'w') as out:
            json.dump([inst], out, indent=2)
        print(f"Extracted: {inst['instance_id']}")
        print(f"Test cmd: {inst['test_cmd'][:100]}...")
        break
PYEOF

echo ""
echo "Starting validation (this may take 30-60 minutes)..."
docker run --rm \
  -v $(pwd)/logs:/workspace/logs \
  -v $(pwd)/swesmith:/workspace/swesmith \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -e PYTHONUNBUFFERED=1 \
  swesmith-validation:latest \
  bash -c "cd /workspace && python3 -m swesmith.harness.valid /tmp/test_pr_10949.json --workers 1 --redo_existing" 2>&1 | tee logs/filtered_test_pr_10949.log &

PID1=$!

echo "Validation running in background (PID: $PID1)"
echo "Monitoring for 2 minutes then checking progress..."

sleep 120

echo ""
echo "=== Progress Check ==="
if docker ps | grep -q "pr_10949"; then
    echo "✓ Container still running"
    docker ps --format "table {{.Names}}\t{{.Status}}" | grep pr_10949
else
    echo "✗ Container stopped"
fi

echo ""
echo "Checking for results..."
if [ -f logs/run_validation/juspay__hyperswitch.fece9bc3/juspay__hyperswitch.fece9bc3.pr_10949/report.json ]; then
    echo "✓ Report generated!"
    cat logs/run_validation/juspay__hyperswitch.fece9bc3/juspay__hyperswitch.fece9bc3.pr_10949/report.json
else
    echo "⏳ Still running..."
fi

echo ""
echo "=========================================="
echo "Test initiated. Full results will be available in logs/filtered_test_pr_10949.log"
echo "=========================================="
