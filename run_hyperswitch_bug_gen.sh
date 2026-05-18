#!/bin/bash
# Generate 10 LM bugs and 10 PR mirror bugs for Hyperswitch
# Uses existing swesmith bug generation scripts

set -e

REPO="juspay__hyperswitch.fece9bc3"
OUTPUT_DIR="logs/bug_gen/juspay__hyperswitch.fece9bc3"
CONFIG_FILE="configs/bug_gen/lm_unified_bugs.yml"

# Create output directories
mkdir -p "${OUTPUT_DIR}/lm_bugs"
mkdir -p "${OUTPUT_DIR}/pr_mirror"

echo "=========================================="
echo "HYPERSWITCH BUG GENERATION"
echo "=========================================="
echo "Repo: ${REPO}"
echo "LM Config: ${CONFIG_FILE}"
echo "Output: ${OUTPUT_DIR}"
echo ""

# Check if Docker image exists
echo "Checking Docker image..."
IMAGE_NAME="swebench/swesmith.arm64.juspay_1776_hyperswitch.fece9bc38b9890a1a40912ce2a95037842362e27"
if ! docker images | grep -q "${IMAGE_NAME}"; then
    echo "Building Docker image..."
    docker build --platform linux/arm64 -t "${IMAGE_NAME}" -f - . << 'DOCKERFILE'
FROM rust:1.88
ARG DEBIAN_FRONTEND=noninteractive
ENV TZ=Etc/UTC
RUN apt update && apt install -y wget git build-essential pkg-config libssl-dev \
&& rm -rf /var/lib/apt/lists/*
RUN git clone https://github.com/juspay/hyperswitch.git /testbed
WORKDIR /testbed
RUN git checkout fece9bc38b9890a1a40912ce2a95037842362e27
RUN cargo fetch 2>&1 | tail -20 || true
DOCKERFILE
fi

echo "Docker image ready"
echo ""

# ==========================================
# PART 1: Generate 10 LM Bugs
# ==========================================
echo "=========================================="
echo "PART 1: Generating 10 LM Bugs"
echo "=========================================="

# Check if config file exists
if [ ! -f "${CONFIG_FILE}" ]; then
    echo "Creating LM bug config..."
    mkdir -p "$(dirname ${CONFIG_FILE})"
    cat > "${CONFIG_FILE}" << 'EOF'
name: lm_unified_bugs
prompts:
  - role: system
    content: |
      You are a Rust expert introducing subtle bugs for testing purposes.
      Your task is to modify the given function to introduce a bug while maintaining realistic code structure.

  - role: user
    content: |
      Here is a Rust function. Introduce ONE subtle bug:

      ```rust
      {code}
      ```

      Possible bug types:
      1. Off-by-one errors (> vs >=)
      2. Logic inversion (!condition instead of condition)
      3. Wrong operator (&& vs ||, + vs -)
      4. Missing validation check
      5. Incorrect constant value
      6. Swapped variable usage

      Rules:
      - Make the bug realistic and hard to spot
      - Preserve function signature
      - Keep imports and types intact
      - Only change the logic, not comments

      Return ONLY the modified function code in a code block.
      Explain the bug briefly after the code.

strategies:
  - validate_id:
      file_pattern: "crates/router/src/core/utils.rs"
      bug_types: ["off_by_one", "invert_logic", "wrong_operator"]
  - validate_dispute_stage:
      file_pattern: "crates/router/src/core/utils.rs"
      bug_types: ["remove_check", "logic_error"]
  - validate_payment_status:
      file_pattern: "crates/router/src/core/payments/helpers.rs"
      bug_types: ["off_by_one", "invert_logic", "wrong_operator"]
EOF
fi

# Run LM rewrite bug generation
echo "Running LM bug generation..."
python3 -m swesmith.bug_gen.llm.rewrite \
    "${REPO}" \
    --config "${CONFIG_FILE}" \
    --model "openai/kimi-latest" \
    --n_workers 2 \
    --max_bugs 10 \
    2>&1 | tee "${OUTPUT_DIR}/lm_bugs/generation.log"

LM_COUNT=$(find "${OUTPUT_DIR}" -name "bug__lm_rewrite*.diff" 2>/dev/null | wc -l)
echo ""
echo "Generated ${LM_COUNT} LM bugs"
echo ""

# ==========================================
# PART 2: Generate 10 PR Mirror Bugs
# ==========================================
echo "=========================================="
echo "PART 2: Generating 10 PR Mirror Bugs"
echo "=========================================="

# Collect PRs from Hyperswitch
PRS_FILE="${OUTPUT_DIR}/pr_mirror/collected_prs.jsonl"

echo "Collecting PRs from juspay/hyperswitch..."
python3 << PYTHON_SCRIPT
import requests
import json

REPO = "juspay__hyperswitch.fece9bc3"
COMMIT = "fece9bc38b9890a1a40912ce2a95037842362e27"
PRS_FILE = "logs/bug_gen/juspay__hyperswitch.fece9bc3/pr_mirror/collected_prs.jsonl"

# Get recently merged PRs from Hyperswitch
url = "https://api.github.com/repos/juspay/hyperswitch/pulls"
params = {
    "state": "closed",
    "sort": "updated",
    "direction": "desc",
    "per_page": 20
}

try:
    response = requests.get(url, params=params, timeout=30)
    prs = response.json()

    count = 0
    with open(PRS_FILE, "w") as f:
        for pr in prs:
            if pr.get("merged_at") and count < 10:
                instance = {
                    "instance_id": f"{REPO}.pr_{pr['number']}",
                    "repo": "juspay/hyperswitch",
                    "pull_number": pr["number"],
                    "patch": pr.get("diff_url", ""),
                    "title": pr["title"],
                    "base_commit": COMMIT
                }
                f.write(json.dumps(instance) + "\n")
                count += 1
    print(f"Collected {count} PRs")
except Exception as e:
    print(f"Error collecting PRs: {e}")
    # Create empty file
    open(PRS_FILE, "w").close()
PYTHON_SCRIPT

if [ -s "${PRS_FILE}" ]; then
    echo "Running PR mirror generation..."
    python3 -m swesmith.bug_gen.mirror.generate \
        "${PRS_FILE}" \
        --model "openai/kimi-latest" \
        -n 2 \
        2>&1 | tee "${OUTPUT_DIR}/pr_mirror/generation.log"
else
    echo "No PRs collected. Using procedural generation instead..."
    python3 -m swesmith.bug_gen.procedural.generate \
        --repo "${REPO}" \
        --max_bugs 10 \
        --seed 42 \
        2>&1 | tee "${OUTPUT_DIR}/pr_mirror/generation.log"
fi

PR_COUNT=$(find "${OUTPUT_DIR}" -name "bug__pr_*.diff" -o -name "bug__procedural*.diff" 2>/dev/null | wc -l)
echo ""
echo "Generated ${PR_COUNT} PR/Procedural bugs"
echo ""

# ==========================================
# SUMMARY
# ==========================================
echo "=========================================="
echo "SUMMARY"
echo "=========================================="
echo "LM Bugs: ${LM_COUNT}"
echo "PR/Procedural Bugs: ${PR_COUNT}"
echo ""
echo "Output directory: ${OUTPUT_DIR}"
echo ""
echo "Next steps:"
echo "1. Review generated bugs in ${OUTPUT_DIR}"
echo "2. Collect bugs into dataset"
echo "3. Generate test patches"
echo "4. Run validation"
