#!/bin/bash
# Pre-flight check for 2 PR-mirror + 2 LLM complex rewrite pilot
set -e
cd "$(dirname "$0")/.."

echo "=== Pipeline pre-flight ==="

# .env
for v in LITE_LLM_URL LITE_LLM_API_KEY LITE_LLM_MODEL; do
  if grep -q "^${v}=" .env 2>/dev/null && [ -n "$(grep "^${v}=" .env | cut -d= -f2-)" ]; then
    echo "OK  $v set"
  else
    echo "WARN $v missing or empty in .env"
  fi
done

# Local repo
if [ -d "juspay__hyperswitch.fece9bc3/.git" ]; then
  echo "OK  hyperswitch repo at juspay__hyperswitch.fece9bc3"
  echo "    commit: $(cd juspay__hyperswitch.fece9bc3 && git rev-parse --short HEAD)"
else
  echo "FAIL hyperswitch repo missing"
  exit 1
fi

# Python deps
uv run python -c "
from swesmith.bug_gen.patch_inverter import invert_unified_diff
from swesmith.bug_gen.mirror import generate
from swesmith.bug_gen.llm import rust_rewrite
from swesmith.bug_gen.rust_grounded.pipeline import GroundedBugPipeline
from swesmith.harness import valid
print('OK  core modules import')
"

# PR input data
BATCH="logs/bug_gen/juspay__hyperswitch.fece9bc3/pr_mirror/batch1_small.jsonl"
if [ -f "$BATCH" ]; then
  echo "OK  PR batch data: $BATCH ($(wc -l < "$BATCH") lines)"
else
  echo "WARN $BATCH missing — need print_pulls + build_dataset"
fi

# Docker (optional for F2P)
IMG="swebench/swesmith.arm64.juspay_1776_hyperswitch.fece9bc3:latest"
if docker image inspect "$IMG" >/dev/null 2>&1; then
  echo "OK  Docker image: $IMG"
else
  echo "WARN Docker image missing — harness valid will skip F2P until image built"
fi

echo "=== Pre-flight done ==="
