#!/bin/bash
#
# SWE-Smith Complete Pipeline Script
# Runs the entire pipeline from bug generation to validation in a single command
#
# Usage:
#   ./run_swe_smith_pipeline.sh --repo owner/repo --commit abc123 [options]
#
# Example:
#   ./run_swe_smith_pipeline.sh \
#     --repo vllm-project/vllm \
#     --commit 3e1ad443 \
#     --model anthropic/claude-3-7-sonnet-20250219 \
#     --output_dir ./my_run

set -euo pipefail

# Default values
REPO=""
COMMIT=""
MODEL="anthropic/claude-3-7-sonnet-20250219"
ISSUE_MODEL="portkey/gpt-5-mini"
BUG_CONFIG="configs/bug_gen/lm_unified_bugs.yml"
ISSUE_CONFIG="configs/issue_gen/ig_v2.yaml"
OUTPUT_DIR="logs/pipeline_run"
N_WORKERS=4
SKIP_ENV_SETUP=false
SKIP_BUG_GEN=false
SKIP_TEST_GEN=false
SKIP_VALIDATION=false
SKIP_ISSUE_GEN=false
RUN_ALL=false
BUG_METHOD="lm_rewrite"

# Parse command line arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --repo)
            REPO="$2"
            shift 2
            ;;
        --commit)
            COMMIT="$2"
            shift 2
            ;;
        --model)
            MODEL="$2"
            shift 2
            ;;
        --issue_model)
            ISSUE_MODEL="$2"
            shift 2
            ;;
        --bug_config)
            BUG_CONFIG="$2"
            shift 2
            ;;
        --bug_method)
            BUG_METHOD="$2"
            shift 2
            ;;
        --issue_config)
            ISSUE_CONFIG="$2"
            shift 2
            ;;
        --output_dir)
            OUTPUT_DIR="$2"
            shift 2
            ;;
        --n_workers)
            N_WORKERS="$2"
            shift 2
            ;;
        --skip_env_setup)
            SKIP_ENV_SETUP=true
            shift
            ;;
        --skip_bug_gen)
            SKIP_BUG_GEN=true
            shift
            ;;
        --skip_test_gen)
            SKIP_TEST_GEN=true
            shift
            ;;
        --skip_validation)
            SKIP_VALIDATION=true
            shift
            ;;
        --skip_issue_gen)
            SKIP_ISSUE_GEN=true
            shift
            ;;
        --run_all)
            RUN_ALL=true
            shift
            ;;
        --help|-h)
            echo "SWE-Smith Complete Pipeline"
            echo ""
            echo "Usage: $0 --repo owner/repo --commit abc123 [options]"
            echo ""
            echo "Required Arguments:"
            echo "  --repo REPO          Repository in format owner/repo (e.g., vllm-project/vllm)"
            echo "  --commit COMMIT      Git commit hash to work from"
            echo ""
            echo "Optional Arguments:"
            echo "  --model MODEL        LLM model for bug generation (default: anthropic/claude-3-7-sonnet-20250219)"
            echo "  --issue_model MODEL  LLM model for issue generation (default: portkey/gpt-5-mini)"
            echo "  --bug_method METHOD  Bug generation method: lm_rewrite or pr_mirror (default: lm_rewrite)"
            echo "  --bug_config PATH    Bug generation config (default: configs/bug_gen/lm_unified_bugs.yml)"
            echo "  --issue_config PATH  Issue generation config (default: configs/issue_gen/ig_v2.yaml)"
            echo "  --output_dir DIR     Output directory (default: logs/pipeline_run)"
            echo "  --n_workers NUM      Number of parallel workers (default: 4)"
            echo ""
            echo "Skip Options:"
            echo "  --skip_env_setup     Skip environment setup (Docker image creation)"
            echo "  --skip_bug_gen       Skip bug generation"
            echo "  --skip_test_gen      Skip test generation"
            echo "  --skip_validation    Skip validation"
            echo "  --skip_issue_gen     Skip issue generation"
            echo ""
            echo "Convenience:"
            echo "  --run_all            Equivalent to running all steps (default behavior)"
            echo "  --help, -h           Show this help message"
            echo ""
            echo "Examples:"
            echo "  # Full pipeline with LM Rewrite (default)"
            echo "  $0 --repo vllm-project/vllm --commit 3e1ad443"
            echo ""
            echo "  # Use PR Mirroring instead of LM Rewrite"
            echo "  $0 --repo vllm-project/vllm --commit 3e1ad443 --bug_method pr_mirror"
            echo ""
            echo "  # Custom output dir with specific model"
            echo "  $0 --repo vllm-project/vllm --commit 3e1ad443 --model openai/gpt-4o --output_dir ./vllm_run"
            echo ""
            echo "  # Only run validation on existing bugs"
            echo "  $0 --repo vllm-project/vllm --commit 3e1ad443 --skip_env_setup --skip_bug_gen --skip_test_gen --skip_issue_gen"
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            echo "Use --help for usage information"
            exit 1
            ;;
    esac
done

# Validate required arguments
if [[ -z "$REPO" ]] || [[ -z "$COMMIT" ]]; then
    echo "Error: --repo and --commit are required arguments"
    echo "Use --help for usage information"
    exit 1
fi

# Extract repo name components
REPO_NAME=$(basename "$REPO")
REPO_OWNER=$(dirname "$REPO" | tr '/' '_')
SHORT_COMMIT=${COMMIT:0:8}
INSTANCE_ID="${REPO_OWNER}__${REPO_NAME}.${SHORT_COMMIT}"

# Setup directories
BUG_GEN_DIR="${OUTPUT_DIR}/bug_gen/${INSTANCE_ID}"
VALIDATION_DIR="${OUTPUT_DIR}/validation/${INSTANCE_ID}"
TASK_INSTS_DIR="${OUTPUT_DIR}/task_insts"
mkdir -p "$BUG_GEN_DIR" "$VALIDATION_DIR" "$TASK_INSTS_DIR"

# Logging functions
log_info() {
    echo "[INFO] $(date '+%Y-%m-%d %H:%M:%S') - $1"
}

log_error() {
    echo "[ERROR] $(date '+%Y-%m-%d %H:%M:%S') - $1" >&2
}

log_section() {
    echo ""
    echo "========================================"
    echo "  $1"
    echo "========================================"
    echo ""
}

log_section "SWE-Smith Pipeline Configuration"
log_info "Repository: $REPO"
log_info "Commit: $COMMIT"
log_info "Instance ID: $INSTANCE_ID"
log_info "Bug generation method: $BUG_METHOD"
log_info "Bug generation model: $MODEL"
log_info "Issue generation model: $ISSUE_MODEL"
log_info "Bug config: $BUG_CONFIG"
log_info "Issue config: $ISSUE_CONFIG"
log_info "Output directory: $OUTPUT_DIR"
log_info "Workers: $N_WORKERS"

# ============================================
# STEP 1: Environment Setup
# ============================================
if [[ "$SKIP_ENV_SETUP" == false ]]; then
    log_section "STEP 1: Environment Setup"

    # Create Docker image for the repository
    log_info "Creating Docker image for $REPO..."
    python3 -m swesmith.build_repo.create_images --repos "$REPO"

    log_info "Environment setup complete"
else
    log_info "Skipping environment setup"
fi

# ============================================
# STEP 2: Bug Generation
# ============================================
if [[ "$SKIP_BUG_GEN" == false ]]; then
    log_section "STEP 2: Bug Generation"

    if [[ "$BUG_METHOD" == "lm_rewrite" ]]; then
        # LM Rewrite - Blank functions and rewrite via LLM
        log_info "Running LM Rewrite bug generation with $BUG_CONFIG..."
        python3 -m swesmith.bug_gen.llm.rewrite "$INSTANCE_ID" \
            --model "$MODEL" \
            --config_file "$BUG_CONFIG" \
            --n_workers "$N_WORKERS"

    elif [[ "$BUG_METHOD" == "pr_mirror" ]]; then
        # PR Mirroring - Collect and mirror PRs (CPU-compatible only)
        log_info "Running PR Mirror bug generation (CPU-filtered)..."

        # Step 2a: Collect task instances from PRs
        log_info "Collecting PRs from $REPO..."
        PR_DATA_DIR="${OUTPUT_DIR}/prs/data"
        PR_DUMP_DIR="${OUTPUT_DIR}/prs/dumps"
        mkdir -p "$PR_DATA_DIR" "$PR_DUMP_DIR"

        # Collect more PRs initially since we'll filter some out
        python3 -m swesmith.bug_gen.mirror.collect \
            --repos "$REPO" \
            --path_prs "$PR_DUMP_DIR" \
            --path_tasks "$PR_DATA_DIR" \
            --max_pulls 100

        # Step 2b: Filter PRs for CPU compatibility
        log_info "Filtering PRs for CPU compatibility (no GPU tests)..."
        PR_INPUT_FILE="${PR_DATA_DIR}/${REPO_NAME}-insts.jsonl"
        CPU_FILTERED_FILE="${PR_DATA_DIR}/${REPO_NAME}-insts-cpu.jsonl"

        if [[ -f "$PR_INPUT_FILE" ]]; then
            python3 << 'EOF'
import json
import sys
import re

def is_gpu_required(test_code):
    """Check if test code requires GPU/CUDA."""
    if not test_code:
        return False

    gpu_patterns = [
        r'torch\.cuda',
        r'cuda\(\)',
        r'@require_gpu',
        r'@pytest\.mark\.gpu',
        r'@pytest\.mark\.skipif.*cuda',
        r'device.*cuda',
        r'gpu.*available',
        r'requires_gpu',
        r'GPU_AVAILABLE',
        r'torch\.backends\.cuda',
        r'nvidia',
        r'cupy',
    ]

    test_code_lower = test_code.lower()
    for pattern in gpu_patterns:
        if re.search(pattern, test_code_lower, re.IGNORECASE):
            return True
    return False

def filter_cpu_compatible(pr_file, output_file):
    """Filter PRs to only include CPU-compatible ones."""
    cpu_count = 0
    gpu_count = 0

    with open(pr_file, 'r') as fin, open(output_file, 'w') as fout:
        for line in fin:
            try:
                pr = json.loads(line.strip())

                # Check test patch for GPU requirements
                test_patch = pr.get('test_patch', '')

                if is_gpu_required(test_patch):
                    gpu_count += 1
                    continue

                # Also check if patch itself adds GPU-specific code
                patch = pr.get('patch', '')
                if is_gpu_required(patch):
                    gpu_count += 1
                    continue

                # Keep CPU-compatible PRs
                fout.write(json.dumps(pr) + '\n')
                cpu_count += 1

            except json.JSONDecodeError:
                continue

    print(f"Filtered: {cpu_count} CPU-compatible, {gpu_count} GPU-required skipped")
    return cpu_count

input_file = "${PR_INPUT_FILE}"
output_file = "${CPU_FILTERED_FILE}"
count = filter_cpu_compatible(input_file, output_file)
sys.exit(0 if count > 0 else 1)
EOF

            if [[ $? -ne 0 ]]; then
                log_error "No CPU-compatible PRs found"
                exit 1
            fi
        else
            log_error "No PR data found at $PR_INPUT_FILE"
            exit 1
        fi

        # Step 2c: Run mirroring on CPU-filtered PRs
        log_info "Mirroring CPU-compatible PRs into bugs..."
        python3 -m swesmith.bug_gen.mirror.generate "$CPU_FILTERED_FILE" \
            --model "$MODEL" \
            --output_dir "$BUG_GEN_DIR"

        # Step 2d: Extract F2P and P2P test cases from PR test patches
        log_info "Extracting F2P/P2P test cases from PRs..."
        python3 << 'EOF'
import json
import os
import re
from pathlib import Path

def extract_test_names(test_patch):
    """Extract test function names from test patch."""
    test_names = []

    # Pattern to match test function definitions
    patterns = [
        r'def\s+(test_\w+)\s*\(',  # Standard pytest
        r'def\s+(Test\w+)\s*\(',   # Test class
        r'@pytest\.mark\.parametrize.*\ndef\s+(test_\w+)',
    ]

    for pattern in patterns:
        matches = re.findall(pattern, test_patch)
        test_names.extend(matches)

    return list(set(test_names))

def process_mirrored_instances(bug_gen_dir):
    """Process mirrored instances to extract test info."""
    json_files = list(Path(bug_gen_dir).glob('*.json'))

    for json_file in json_files:
        try:
            with open(json_file, 'r') as f:
                instance = json.load(f)

            # If test_patch exists but FAIL_TO_PASS is empty, extract tests
            test_patch = instance.get('test_patch', '')
            fail_to_pass = instance.get('FAIL_TO_PASS', [])

            if test_patch and (not fail_to_pass or len(fail_to_pass) == 0):
                test_names = extract_test_names(test_patch)

                if test_names:
                    # Determine file path from test_patch
                    file_match = re.search(r'\+\+\+ b/(\S+)', test_patch)
                    if file_match:
                        test_file = file_match.group(1)
                        instance['FAIL_TO_PASS'] = [
                            f"{test_file}::{name}" for name in test_names
                        ]

                        # Save updated instance
                        with open(json_file, 'w') as f:
                            json.dump(instance, f, indent=2)
                        print(f"Updated {json_file.name} with {len(test_names)} tests")

        except Exception as e:
            print(f"Error processing {json_file}: {e}")
            continue

bug_gen_dir = "${BUG_GEN_DIR}"
process_mirrored_instances(bug_gen_dir)
EOF
    else
        log_error "Unknown bug method: $BUG_METHOD. Use 'lm_rewrite' or 'pr_mirror'"
        exit 1
    fi

    # Check if any bugs were generated
    BUG_COUNT=$(find "$BUG_GEN_DIR" -name "*.diff" -o -name "*.json" 2>/dev/null | wc -l)
    log_info "Generated $BUG_COUNT bug artifacts"

    if [[ "$BUG_COUNT" -eq 0 ]]; then
        log_error "No bugs were generated. Exiting."
        exit 1
    fi

    # Collect all patches into a single JSON file
    log_info "Collecting all bug patches..."
    PATCHES_JSON="${BUG_GEN_DIR}/${INSTANCE_ID}_all_patches.json"
    python3 -m swesmith.bug_gen.collect_patches "$BUG_GEN_DIR" --output "$PATCHES_JSON"

    log_info "Bug generation complete. Patches saved to: $PATCHES_JSON"
else
    log_info "Skipping bug generation"
    PATCHES_JSON="${BUG_GEN_DIR}/${INSTANCE_ID}_all_patches.json"
fi

# ============================================
# STEP 3: Test Generation
# ============================================
if [[ "$SKIP_TEST_GEN" == false ]]; then
    log_section "STEP 3: Test Generation"

    # Use the enhanced test generation script
    if [[ -f "design_targeted_tests_v2.py" ]]; then
        log_info "Running enhanced test generation (design_targeted_tests_v2.py)..."
        python design_targeted_tests_v2.py \
            --instances "$PATCHES_JSON" \
            --output_dir "$BUG_GEN_DIR" \
            --use_llm \
            --validate
    elif [[ -f "generate_p2p_f2p_tests.py" ]]; then
        log_info "Running P2P/F2P test generation..."
        python generate_p2p_f2p_tests.py \
            --input "$PATCHES_JSON" \
            --output_dir "$BUG_GEN_DIR"
    else
        log_info "No test generation script found. Tests will be generated during validation."
    fi

    log_info "Test generation complete"
else
    log_info "Skipping test generation"
fi

# ============================================
# STEP 4: Validation
# ============================================
if [[ "$SKIP_VALIDATION" == false ]]; then
    log_section "STEP 4: Validation"

    # Determine the input file for validation
    if [[ -f "${BUG_GEN_DIR}/${INSTANCE_ID}_with_tests.json" ]]; then
        VALIDATION_INPUT="${BUG_GEN_DIR}/${INSTANCE_ID}_with_tests.json"
    elif [[ -f "${BUG_GEN_DIR}/${INSTANCE_ID}_all_patches.json" ]]; then
        VALIDATION_INPUT="${BUG_GEN_DIR}/${INSTANCE_ID}_all_patches.json"
    else
        log_error "No validation input file found in $BUG_GEN_DIR"
        exit 1
    fi

    log_info "Running validation on: $VALIDATION_INPUT"

    # Run validation
    python3 -m swesmith.harness.valid "$VALIDATION_INPUT" \
        --output_dir "$VALIDATION_DIR" \
        --n_workers "$N_WORKERS"

    # Gather validated instances (those with 1+ F2P tests)
    log_info "Gathering validated task instances..."
    VALIDATED_OUTPUT="${TASK_INSTS_DIR}/${INSTANCE_ID}.json"
    python3 -m swesmith.harness.gather "$VALIDATION_DIR" \
        --output "$VALIDATED_OUTPUT"

    # Count validated instances
    if [[ -f "$VALIDATED_OUTPUT" ]]; then
        VALIDATED_COUNT=$(python -c "import json; data=json.load(open('$VALIDATED_OUTPUT')); print(len(data) if isinstance(data, list) else 1)")
        log_info "Validated instances: $VALIDATED_COUNT"
    else
        log_warning "No validated instances output file found"
    fi

    log_info "Validation complete. Results saved to: $VALIDATED_OUTPUT"
else
    log_info "Skipping validation"
    VALIDATED_OUTPUT="${TASK_INSTS_DIR}/${INSTANCE_ID}.json"
fi

# ============================================
# STEP 5: Issue Generation
# ============================================
if [[ "$SKIP_ISSUE_GEN" == false ]]; then
    log_section "STEP 5: Issue Generation"

    if [[ ! -f "$VALIDATED_OUTPUT" ]]; then
        log_error "No validated instances found at $VALIDATED_OUTPUT"
        log_error "Cannot generate issues without validated task instances"
        exit 1
    fi

    log_info "Generating GitHub-style issues using config: $ISSUE_CONFIG"

    # Generate issues using the latest ig_v2.yaml config
    python3 -m swesmith.issue_gen.generate "$VALIDATED_OUTPUT" \
        --config_file "$ISSUE_CONFIG" \
        --model "$ISSUE_MODEL" \
        --n_workers "$N_WORKERS" \
        --experiment_id "pipeline_${INSTANCE_ID}" \
        --use_existing

    log_info "Issue generation complete"
else
    log_info "Skipping issue generation"
fi

# ============================================
# Final Summary
# ============================================
log_section "Pipeline Complete!"

log_info "Output locations:"
log_info "  Bug patches:     $BUG_GEN_DIR"
log_info "  Validation:      $VALIDATION_DIR"
log_info "  Task instances:  $VALIDATED_OUTPUT"

if [[ -f "$VALIDATED_OUTPUT" ]]; then
    FINAL_COUNT=$(python -c "import json; data=json.load(open('$VALIDATED_OUTPUT')); print(len(data) if isinstance(data, list) else 1)")
    log_info "Total validated task instances: $FINAL_COUNT"
fi

log_info "Done!"
