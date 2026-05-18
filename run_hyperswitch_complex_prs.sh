#!/bin/bash
# Generate 10 complex PR mirror bugs with git apply (no LLM recovery)
# Targets PRs with multi-file/cross-file dependencies

set -e

echo "=========================================="
echo "HYPERSWITCH COMPLEX PR MIRROR BUGS"
echo "=========================================="
echo "Strategy: Direct git apply (no LLM recovery)"
echo "Target: PRs with >= 3 files, cross-crate deps"
echo ""

# Step 1: Fetch complex PRs
echo "Step 1: Fetching complex PRs..."
python3 fetch_complex_prs.py

# Step 2: Apply patches directly with git
echo ""
echo "Step 2: Applying patches directly with git apply..."
python3 apply_prs_directly.py

# Step 3: Summary
echo ""
echo "=========================================="
echo "COMPLETED"
echo "=========================================="

SUCCESS=$(find logs/bug_gen/juspay__hyperswitch.fece9bc3/pr_mirror_direct -name "bug__pr_*.diff" 2>/dev/null | wc -l)
echo "Successfully applied: $SUCCESS bugs"
echo ""
echo "Next: Generate test patches and validate"
