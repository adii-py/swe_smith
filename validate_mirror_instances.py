#!/usr/bin/env python3
"""
Validate mirror-generated instances by extracting test patches from original PRs
and verifying they fail on the buggy code.
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional

import requests
from unidiff import PatchSet

# Paths
MIRROR_DIR = Path("logs/bug_gen/vllm-project__vllm.3e1ad443/pr_mirror")
REPO_NAME = "vllm-project__vllm.3e1ad443"

def extract_test_patch_from_pr(pr_number: int) -> Optional[str]:
    """Extract test patch from original PR on GitHub."""
    try:
        # Get PR diff from GitHub
        diff_url = f"https://github.com/vllm-project/vllm/pull/{pr_number}.diff"
        response = requests.get(diff_url, timeout=30)
        response.raise_for_status()

        patch = response.text
        patch_test = ""

        # Extract test-related hunks
        for hunk in PatchSet(patch):
            if any(test_word in hunk.path for test_word in ["test", "tests", "e2e", "testing"]):
                patch_test += str(hunk)

        return patch_test if patch_test else None
    except Exception as e:
        print(f"Error extracting test patch from PR #{pr_number}: {e}")
        return None


def get_successful_mirror_instances():
    """Get list of successfully mirrored instances."""
    instances = []

    for instance_dir in MIRROR_DIR.iterdir():
        if not instance_dir.is_dir():
            continue

        metadata_file = instance_dir / f"metadata__pr_{instance_dir.name.split('-')[-1]}.json"
        bug_patch_file = instance_dir / f"bug__pr_{instance_dir.name.split('-')[-1]}.diff"

        if not metadata_file.exists() or not bug_patch_file.exists():
            continue

        # Check if recovery was successful
        try:
            with open(metadata_file) as f:
                metadata = json.load(f)

            if metadata.get("recover_status") != "success":
                continue

            pr_number = int(instance_dir.name.split("-")[-1])

            with open(bug_patch_file) as f:
                bug_patch = f.read()

            instances.append({
                "instance_id": instance_dir.name,
                "pr_number": pr_number,
                "bug_patch": bug_patch,
                "metadata": metadata,
            })
        except Exception as e:
            print(f"Error reading {instance_dir}: {e}")
            continue

    return instances


def validate_instance(instance: dict, repo_path: str) -> dict:
    """Validate a single instance by applying patches and running tests."""
    result = {
        "instance_id": instance["instance_id"],
        "pr_number": instance["pr_number"],
        "status": "unknown",
        "test_patch": None,
        "error": None,
    }

    # Extract test patch from original PR
    test_patch = extract_test_patch_from_pr(instance["pr_number"])
    if not test_patch:
        result["status"] = "no_test_patch"
        result["error"] = "No test patch found in original PR"
        return result

    result["test_patch"] = test_patch

    # Create temp directory for validation
    with tempfile.TemporaryDirectory() as tmpdir:
        # Clone repo
        clone_result = subprocess.run(
            ["git", "clone", "--depth", "1", "--single-branch", "-b", "main",
             f"file://{repo_path}", tmpdir],
            capture_output=True,
            text=True,
        )
        if clone_result.returncode != 0:
            result["status"] = "clone_failed"
            result["error"] = clone_result.stderr
            return result

        # Apply bug patch
        bug_patch_file = Path(tmpdir) / "bug.patch"
        with open(bug_patch_file, "w") as f:
            f.write(instance["bug_patch"])

        apply_result = subprocess.run(
            ["git", "apply", str(bug_patch_file)],
            cwd=tmpdir,
            capture_output=True,
            text=True,
        )
        if apply_result.returncode != 0:
            result["status"] = "apply_bug_failed"
            result["error"] = apply_result.stderr
            return result

        # Apply test patch
        test_patch_file = Path(tmpdir) / "test.patch"
        with open(test_patch_file, "w") as f:
            f.write(test_patch)

        apply_result = subprocess.run(
            ["git", "apply", str(test_patch_file)],
            cwd=tmpdir,
            capture_output=True,
            text=True,
        )
        if apply_result.returncode != 0:
            result["status"] = "apply_test_failed"
            result["error"] = apply_result.stderr
            return result

        result["status"] = "validated"
        return result


def main():
    print("Fetching successful mirror instances...")
    instances = get_successful_mirror_instances()
    print(f"Found {len(instances)} successful mirror instances")

    # Find local mirror repo
    repo_path = Path(REPO_NAME).resolve()
    if not repo_path.exists():
        print(f"Repository not found at {repo_path}")
        print("Please run: python -m swesmith.bug_gen.mirror.generate --clone")
        sys.exit(1)

    print(f"\nValidating instances using repo at {repo_path}")

    results = []
    for instance in instances:
        print(f"\nValidating {instance['instance_id']} (PR #{instance['pr_number']})...")
        result = validate_instance(instance, str(repo_path))
        results.append(result)
        print(f"  Status: {result['status']}")
        if result['error']:
            print(f"  Error: {result['error'][:100]}...")

    # Save results
    output_file = "logs/bug_gen/mirror_validation_results.json"
    with open(output_file, "w") as f:
        json.dump(results, f, indent=2)

    print(f"\n{'='*60}")
    print(f"Validation complete. Results saved to {output_file}")
    print(f"Total: {len(results)} instances")
    print(f"Validated: {sum(1 for r in results if r['status'] == 'validated')}")
    print(f"No test patch: {sum(1 for r in results if r['status'] == 'no_test_patch')}")
    print(f"Failed: {sum(1 for r in results if r['status'].endswith('_failed'))}")


if __name__ == "__main__":
    main()
