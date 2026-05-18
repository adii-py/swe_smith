#!/usr/bin/env python3
"""
Directly create branches in mirror repo with buggy state.
This bypasses the gather requirement for validation logs.
"""

import json
import subprocess
import tempfile
import os
import sys

def run_cmd(cmd, cwd=None, check=True):
    """Run a shell command."""
    print(f"$ {cmd}")
    result = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True, text=True)
    if result.stdout:
        print(result.stdout)
    if result.stderr:
        print(result.stderr, file=sys.stderr)
    if check and result.returncode != 0:
        raise RuntimeError(f"Command failed: {cmd}")
    return result

def setup_branch(instance, repo_url, base_commit):
    """Set up a single branch with buggy state."""
    instance_id = instance["instance_id"]
    patch_content = instance["patch"]

    # Extract repo name from instance_id
    parts = instance_id.rsplit("-", 1)
    if len(parts) != 2:
        parts = instance_id.rsplit(".", 1)
    repo_part = parts[0]  # e.g., "vllm-project__vllm.3e1ad443" or "vllm-project__vllm"

    # Create branch name
    branch_name = f"vllm-project__vllm.3e1ad443/{instance_id}"

    print(f"\n=== Processing {instance_id} ===")
    print(f"Branch: {branch_name}")

    with tempfile.TemporaryDirectory() as tmpdir:
        repo_path = os.path.join(tmpdir, "repo")

        # Clone the mirror repo
        run_cmd(f"git clone {repo_url} {repo_path}")

        # Configure git
        run_cmd("git config user.email 'swesmith@swesmith.ai'", cwd=repo_path)
        run_cmd("git config user.name 'swesmith'", cwd=repo_path)
        run_cmd("git config commit.gpgsign false", cwd=repo_path)

        # Fetch all branches
        run_cmd("git fetch origin", cwd=repo_path)

        # Checkout base commit
        run_cmd(f"git checkout {base_commit}", cwd=repo_path)

        # Apply the bug patch
        patch_file = os.path.join(tmpdir, "bug.patch")
        with open(patch_file, "w") as f:
            f.write(patch_content)
            # Ensure trailing newline
            if not patch_content.endswith('\n'):
                f.write('\n')

        # Try to apply patch
        result = subprocess.run(
            f"git apply {patch_file}",
            shell=True, cwd=repo_path, capture_output=True, text=True
        )
        if result.returncode != 0:
            print(f"WARNING: Patch failed to apply for {instance_id}")
            print(result.stderr)
            return False

        print(f"Patch applied successfully for {instance_id}")

        # Check for changes
        result = subprocess.run(
            "git status --porcelain",
            shell=True, cwd=repo_path, capture_output=True, text=True
        )
        if not result.stdout.strip():
            print(f"WARNING: No changes for {instance_id}")
            return False

        # Create and checkout branch
        run_cmd(f"git checkout -b {branch_name}", cwd=repo_path)

        # Stage and commit
        run_cmd("git add .", cwd=repo_path)
        run_cmd("git commit --no-gpg-sign -m 'Bug Patch'", cwd=repo_path)

        # Get F2P test files from instance and remove them
        f2p_tests = instance.get("FAIL_TO_PASS", [])
        test_files_removed = []
        if f2p_tests:
            for test_file in f2p_tests:
                # Extract file path from test (handle different formats)
                if "::" in test_file:
                    test_file = test_file.split("::")[0]
                # Skip if test_file is empty or invalid
                if not test_file or test_file == ".":
                    continue
                test_path = os.path.join(repo_path, test_file)
                if os.path.exists(test_path) and os.path.isfile(test_path):
                    os.remove(test_path)
                    test_files_removed.append(test_file)
                    print(f"Removed F2P test file: {test_file}")

        if test_files_removed:
            run_cmd("git add .", cwd=repo_path)
            run_cmd("git commit --no-gpg-sign -m 'Remove F2P Tests'", cwd=repo_path)

        # Push branch to origin
        run_cmd(f"git push -u origin {branch_name}", cwd=repo_path)

        print(f"SUCCESS: Created branch {branch_name}")
        return True

def main():
    # Load instances
    with open("vllm_3prs_final_validated.json") as f:
        instances = json.load(f)

    # Mirror repo URL (SSH)
    repo_url = "git@github.com:adii-py/vllm-project__vllm.3e1ad443.git"
    # Use the current main branch commit (mirror doesn't have 3e1ad443)
    base_commit = "3e1ad4435f7c205dcbd5b14d1a529cc328b22ce8"

    success_count = 0
    for instance in instances:
        try:
            if setup_branch(instance, repo_url, base_commit):
                success_count += 1
        except Exception as e:
            print(f"ERROR processing {instance['instance_id']}: {e}")

    print(f"\n=== Summary ===")
    print(f"Successfully created {success_count}/{len(instances)} branches")

if __name__ == "__main__":
    main()
