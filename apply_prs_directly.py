#!/usr/bin/env python3
"""
Apply PR patches directly using git apply instead of LLM recovery.
This approach avoids the 85% LLM recovery failure rate.
"""

import json
import subprocess
import tempfile
import shutil
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, as_completed

REPO_NAME = "juspay__hyperswitch.fece9bc3"
COMMIT = "fece9bc38b9890a1a40912ce2a95037842362e27"
INPUT_FILE = "logs/bug_gen/juspay__hyperswitch.fece9bc3/pr_mirror/complex_prs.jsonl"
OUTPUT_DIR = Path("logs/bug_gen/juspay__hyperswitch.fece9bc3/pr_mirror_direct")


def apply_pr_patch(pr_data):
    """Apply a PR patch directly using git apply."""

    pr_number = pr_data["pull_number"]
    instance_id = pr_data["instance_id"]
    patch_content = pr_data.get("patch", "")

    if not patch_content:
        return {"instance_id": instance_id, "status": "failed", "reason": "no_patch"}

    # Create output directory if needed
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Use existing mirror instead of cloning (check common locations)
    mirror_paths = [
        Path("juspay__hyperswitch.fece9bc3"),
        Path.home() / ".swesmith" / "mirrors" / "juspay__hyperswitch.fece9bc3",
        Path("/tmp/juspay__hyperswitch.fece9bc3"),
    ]
    mirror_path = None
    for mp in mirror_paths:
        if mp.exists():
            mirror_path = mp
            break

    # Create temp directory and copy from mirror
    with tempfile.TemporaryDirectory() as tmpdir:
        repo_path = Path(tmpdir) / "hyperswitch"

        try:
            if mirror_path.exists():
                # Copy from existing mirror
                shutil.copytree(mirror_path, repo_path, ignore=shutil.ignore_patterns("target"))
            else:
                # Fall back to cloning if mirror doesn't exist
                clone_result = subprocess.run(
                    ["git", "clone", "--depth", "1",
                     "https://github.com/juspay/hyperswitch.git", str(repo_path)],
                    capture_output=True,
                    text=True,
                    timeout=300
                )
                if clone_result.returncode != 0:
                    return {"instance_id": instance_id, "status": "failed", "reason": "clone_failed"}

            # Reset to base commit
            checkout_result = subprocess.run(
                ["git", "reset", "--hard", COMMIT],
                cwd=repo_path,
                capture_output=True,
                text=True,
                timeout=30
            )

            if checkout_result.returncode != 0:
                return {"instance_id": instance_id, "status": "failed", "reason": "checkout_failed"}

            # Write patch to file
            patch_file = Path(tmpdir) / "patch.diff"
            patch_file.write_text(patch_content)

            # Try to apply patch
            apply_result = subprocess.run(
                ["git", "apply", "--verbose", str(patch_file)],
                cwd=repo_path,
                capture_output=True,
                text=True,
                timeout=30
            )

            if apply_result.returncode == 0:
                # Patch applied successfully - this is our bug!

                # Get the diff of changes (bug patch)
                diff_result = subprocess.run(
                    ["git", "diff", "HEAD"],
                    cwd=repo_path,
                    capture_output=True,
                    text=True,
                    timeout=30
                )

                bug_patch = diff_result.stdout

                # Save the bug
                bug_dir = OUTPUT_DIR / instance_id
                bug_dir.mkdir(parents=True, exist_ok=True)

                # Write bug patch
                with open(bug_dir / f"bug__pr_{pr_number}.diff", "w") as f:
                    f.write(bug_patch)

                # Write metadata
                metadata = {
                    "instance_id": instance_id,
                    "repo": REPO_NAME,
                    "pull_number": pr_number,
                    "title": pr_data["title"],
                    "num_files": pr_data["num_files"],
                    "crates_changed": pr_data["crates_changed"],
                    "cross_crate": pr_data["cross_crate"],
                    "status": "success",
                    "method": "git_apply",
                }

                with open(bug_dir / f"metadata__pr_{pr_number}.json", "w") as f:
                    json.dump(metadata, f, indent=2)

                return {"instance_id": instance_id, "status": "success", "files": pr_data["num_files"]}

            else:
                # Patch failed - likely due to conflicts
                # For failed patches, we can save them for manual inspection
                # or attempt a 3-way merge

                return {
                    "instance_id": instance_id,
                    "status": "failed",
                    "reason": "apply_failed",
                    "stderr": apply_result.stderr[:200]
                }

        except subprocess.TimeoutExpired:
            return {"instance_id": instance_id, "status": "failed", "reason": "timeout"}
        except Exception as e:
            return {"instance_id": instance_id, "status": "failed", "reason": str(e)}


def main():
    print("=" * 80)
    print("APPLYING PR PATCHES DIRECTLY (NO LLM RECOVERY)")
    print("=" * 80)

    # Load complex PRs
    if not Path(INPUT_FILE).exists():
        print(f"Input file not found: {INPUT_FILE}")
        print("Run fetch_complex_prs.py first")
        return

    prs = []
    with open(INPUT_FILE) as f:
        for line in f:
            prs.append(json.loads(line))

    print(f"Loaded {len(prs)} PRs")
    print()

    # Apply patches in parallel
    results = {"success": [], "failed": []}

    with ProcessPoolExecutor(max_workers=4) as executor:
        futures = {executor.submit(apply_pr_patch, pr): pr for pr in prs}

        for future in as_completed(futures):
            result = future.result()

            if result["status"] == "success":
                results["success"].append(result)
                print(f"✅ {result['instance_id']}: Applied ({result['files']} files)")
            else:
                results["failed"].append(result)
                print(f"❌ {result['instance_id']}: {result.get('reason', 'unknown')}")

    # Summary
    print()
    print("=" * 80)
    print("SUMMARY")
    print("=" * 80)
    print(f"Total PRs: {len(prs)}")
    print(f"Success: {len(results['success'])}")
    print(f"Failed: {len(results['failed'])}")
    print()
    print(f"Bugs saved to: {OUTPUT_DIR}")

    # Save summary
    summary_file = OUTPUT_DIR / "summary.json"
    with open(summary_file, "w") as f:
        json.dump(results, f, indent=2)


if __name__ == "__main__":
    main()
