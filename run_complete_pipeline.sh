#!/bin/bash
set -e

echo "=========================================="
echo "Complete PR Pipeline - Fixed Version"
echo "Repository: juspay/hyperswitch"
echo "Target: 40 PRs"
echo "=========================================="
echo ""

# Load environment
if [ -f .env ]; then
    set -a && source .env && set +a
fi

# Fix 1: Use correct token variable name
export GITHUB_TOKENS="${GITHUB_TOKEN:-$(gh auth token)}"
if [ -z "$GITHUB_TOKENS" ]; then
    echo "ERROR: No GitHub token available"
    exit 1
fi
echo "✓ GitHub token configured"

# Create directories
mkdir -p logs/prs/data
mkdir -p logs/bug_gen

# Clean old files for fresh run
rm -f logs/prs/data/hyperswitch-*-new.jsonl

echo ""
echo "=========================================="
echo "STEP 1: Collecting 40 PRs (Single Process)"
echo "=========================================="
echo ""

# Fix 2: Use Python script directly to avoid multiprocessing issues
python3 << 'PYEOF'
import os
import sys
sys.path.insert(0, '/Users/aditya.singh.001/Desktop/SWE-smith')

from swesmith.bug_gen.mirror.collect.print_pulls import Repo
from ghapi.all import GhApi
import json
from datetime import datetime

# Setup
token = os.environ['GITHUB_TOKENS'].split(',')[0]
api = GhApi(token=token)
repo_owner = "juspay"
repo_name = "hyperswitch"

print(f"Fetching PRs from {repo_owner}/{repo_name}...")

# Get repo info
repo_data = api.repos.get(repo_owner, repo_name)
repo = Repo(repo_owner, repo_name, api)

# Fetch PRs
prs = []
max_prs = 40
cutoff_date = datetime(2024, 1, 1)

for i, pull in enumerate(repo.get_all_pulls()):
    if i >= max_prs:
        break
    if not pull.merged:
        continue
    if pull.merged_at and datetime.fromisoformat(pull.merged_at.replace('Z', '+00:00')) < cutoff_date:
        continue

    # Get resolved issues
    resolved = repo.extract_resolved_issues(pull.body or "")

    pr_data = {
        "number": pull.number,
        "title": pull.title,
        "body": pull.body,
        "base": {"ref": pull.base.ref, "sha": pull.base.sha},
        "head": {"ref": pull.head.ref, "sha": pull.head.sha},
        "merged": pull.merged,
        "merged_at": pull.merged_at,
        "resolved_issues": resolved,
    }
    prs.append(pr_data)
    print(f"  Fetched PR #{pull.number}: {pull.title[:50]}...")

print(f"\nTotal PRs fetched: {len(prs)}")

# Save PRs
output_file = "logs/prs/data/hyperswitch-prs-new.jsonl"
with open(output_file, 'w') as f:
    for pr in prs:
        f.write(json.dumps(pr) + '\n')

print(f"✓ Saved to {output_file}")

# Build task instances
print("\nBuilding task instances...")
instances = []

for pr in prs:
    try:
        # Get PR diff
        diff_files = list(api.pulls.list_files(repo_owner, repo_name, pr['number']))

        # Separate code and test patches
        code_patch = []
        test_patch = []

        for f in diff_files:
            # Skip non-Rust files
            if not f.filename.endswith('.rs'):
                continue
            # Simple heuristic for test files
            if 'test' in f.filename.lower() or f.filename.startswith('tests/'):
                test_patch.append(f.patch or "")
            else:
                code_patch.append(f.patch or "")

        # Get problem statement from issues
        problem_statements = []
        for issue_num in pr.get('resolved_issues', []):
            try:
                issue = api.issues.get(repo_owner, repo_name, issue_num)
                problem_statements.append(issue.body or "")
            except:
                pass

        instance = {
            "repo": f"{repo_owner}/{repo_name}",
            "instance_id": f"{repo_owner}__{repo_name}-{pr['number']}",
            "pull_number": pr['number'],
            "base_commit": pr['base']['sha'],
            "patch": "\n".join(code_patch),
            "test_patch": "\n".join(test_patch),
            "problem_statement": "\n\n".join(problem_statements) if problem_statements else pr['body'],
            "hints_text": "",
            "created_at": pr['merged_at'],
        }
        instances.append(instance)
        print(f"  Built instance for PR #{pr['number']}")
    except Exception as e:
        print(f"  ✗ Failed PR #{pr['number']}: {e}")

# Save instances
instances_file = "logs/prs/data/hyperswitch-insts-new.jsonl"
with open(instances_file, 'w') as f:
    for inst in instances:
        f.write(json.dumps(inst) + '\n')

print(f"\n✓ Created {len(instances)} task instances")
print(f"✓ Saved to {instances_file}")
PYEOF

echo ""
echo "=========================================="
echo "STEP 2: PR Mirroring (Balanced Config)"
echo "=========================================="
echo ""

# Fix 3: Pre-select profile by setting environment variable
# We'll use the first profile (c6a70eee) which has the most existing data
export SWESMITH_PROFILE_INDEX="0"

INSTANCES_FILE="logs/prs/data/hyperswitch-insts-new.jsonl"
PR_COUNT=$(wc -l < "$INSTANCES_FILE")
echo "Processing $PR_COUNT instances..."
echo "Config: model=kimi-latest, workers=1, max_files=15, max_lines=1500"
echo ""

# Fix 4: Create a wrapper that handles profile selection
python3 << 'PYEOF'
import os
import sys
sys.path.insert(0, '/Users/aditya.singh.001/Desktop/SWE-smith')

# Monkey-patch the profile selection to auto-select
import swesmith.bug_gen.mirror.generate as gen_module

original_sweb_inst_to_rp = gen_module.sweb_inst_to_rp

def auto_select_profile(inst):
    from swesmith.profiles import registry
    owner, repo = inst["repo"].split("/")
    rps = [x for x in registry.values() if x.owner == owner and x.repo == repo]
    if len(rps) == 0:
        raise ValueError(f"{repo} not found in registry")
    # Auto-select first profile (c6a70eee)
    idx = int(os.environ.get('SWESMITH_PROFILE_INDEX', 0))
    print(f"Auto-selected profile {idx + 1}/{len(rps)}: {rps[idx].commit[:8]}")
    return rps[idx]

gen_module.sweb_inst_to_rp = auto_select_profile

# Now run the main function
from swesmith.bug_gen.mirror.generate import main
import sys

sys.argv = [
    'generate',
    'logs/prs/data/hyperswitch-insts-new.jsonl',
    '--model', 'openai/kimi-latest',
    '--num_processes', '1',
    '--max_files', '15',
    '--max_lines', '1500'
]

main([
    'logs/prs/data/hyperswitch-insts-new.jsonl'
], model='openai/kimi-latest', redo_existing=False, redo_skipped=False, api_key=None, num_processes=1, max_files=15, max_lines=1500, max_file_lines=10000)
PYEOF

echo ""
echo "=========================================="
echo "Pipeline Complete!"
echo "=========================================="

# Show results
OUTPUT_DIR="logs/bug_gen/juspay__hyperswitch.c6a70eee/pr_mirror"
if [ -d "$OUTPUT_DIR" ]; then
    TOTAL_INSTANCES=$(find "$OUTPUT_DIR" -type d -name "juspay__hyperswitch-*" | wc -l)
    BUG_FILES=$(find "$OUTPUT_DIR" -name "bug__*.diff" | wc -l)
    echo ""
    echo "Results:"
    echo "  • Total instance directories: $TOTAL_INSTANCES"
    echo "  • Successful bug patches: $BUG_FILES"
    echo "  • Output: $OUTPUT_DIR"
fi

echo ""
echo "To validate: python3 -m swesmith.harness.validate $OUTPUT_DIR"
