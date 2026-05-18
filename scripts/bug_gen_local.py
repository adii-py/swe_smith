#!/usr/bin/env python3
"""
Local Bug Generation & Validation Script
Runs LM Rewrite, Combine, Validation, and Gather locally without Modal.

Usage:
    python scripts/bug_gen_local.py --repo juspay/hyperswitch [OPTIONS]

Examples:
    # Full pipeline for Hyperswitch
    python scripts/bug_gen_local.py --repo juspay/hyperswitch --phases gen,val,gather

    # Just generate bugs (LM Rewrite + Combine)
    python scripts/bug_gen_local.py --repo juspay/hyperswitch --phases gen

    # Generate + Validate only
    python scripts/bug_gen_local.py --repo juspay/hyperswitch --phases gen,val

    # With PR Mirror
    python scripts/bug_gen_local.py --repo juspay/hyperswitch \
        --enable-pr-mirror \
        --pr-mirror-dataset /path/to/task-instances.jsonl
"""

import argparse
import asyncio
import json
import os
import ssl
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Any

# Fix SSL certificate verification issues on macOS
# Create SSL context that doesn't verify certificates (for development only)
try:
    _create_unverified_https_context = ssl._create_unverified_context
except AttributeError:
    pass
else:
    ssl._create_default_https_context = _create_unverified_https_context

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from swesmith.constants import LOG_DIR_BUG_GEN, LOG_DIR_TASKS, LOG_DIR_RUN_VALIDATION
from swesmith.profiles import registry


def run_command(cmd: list[str] | str, cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess:
    """Run a shell command and return result."""
    print(f"$ {' '.join(cmd) if isinstance(cmd, list) else cmd}")
    if isinstance(cmd, list):
        return subprocess.run(cmd, cwd=cwd, check=check, capture_output=True, text=True)
    else:
        return subprocess.run(cmd, cwd=cwd, shell=True, check=check, capture_output=True, text=True)


def get_repo_id(repo_name: str) -> str:
    """Resolve repo name to repo_id."""
    try:
        return registry.get_from_inst({"repo": repo_name, "instance_id": "dummy"}).repo_name
    except Exception:
        target = repo_name.replace("/", "__")
        for key in registry.keys():
            if target in key:
                return key
        return repo_name


def phase_lm_rewrite(repo_id: str, model: str, max_bugs: int, profile) -> dict:
    """Phase 1: LM Rewrite - Generate bugs using LLM."""
    print(f"\n{'='*60}")
    print(f"PHASE 1: LM REWRITE")
    print(f"{'='*60}\n")

    from swesmith.bug_gen.llm.rewrite import main as rewrite_main
    import shutil

    # Ensure model has provider prefix
    if model and "/" not in model:
        # Try to infer provider or use openai as default
        if model.startswith("claude"):
            model = f"anthropic/{model}"
        elif model.startswith("gpt"):
            model = f"openai/{model}"
        else:
            model = f"openai/{model}"
        print(f"Normalized model name: {model}")

    # Step 0: For public repos, clone directly to bypass mirror requirement
    try:
        # Check if repo is already cloned locally
        if Path(repo_id).exists():
            print(f"Repo directory exists: {repo_id}")
        else:
            # Clone directly from source (public repo)
            source_url = f"https://github.com/{profile.owner}/{profile.repo}.git"
            print(f"Cloning from: {source_url}")
            run_command(["git", "clone", source_url, repo_id], check=True)

            # Checkout specific commit
            run_command(["git", "checkout", profile.commit], cwd=Path(repo_id), check=True)
            print(f"Checked out commit: {profile.commit}")

        # Temporarily trick the profile into thinking mirror exists
        # by setting the cache directly
        profile._cache_mirror_exists = True
        print("Using direct clone (public repo)")

    except Exception as e:
        print(f"Direct clone failed: {e}")
        print("Trying mirror approach...")
        try:
            if not profile._mirror_exists():
                print("Creating mirror repository...")
                profile.create_mirror()
            else:
                print("Mirror exists")
        except Exception as me:
            print(f"Mirror creation also failed: {me}")
            return {"success": False, "error": f"Could not clone repo: {e}"}

    try:
        rewrite_main(
            repo=repo_id,
            config_file="configs/bug_gen/lm_rewrite_logic_bugs.yml",
            model=model,
            n_workers=4,
            redo_existing=True,
            max_bugs=max_bugs,
        )

        # Cleanup cloned repo (handle symlinks)
        repo_path = Path(repo_id)
        if repo_path.exists():
            if repo_path.is_symlink():
                repo_path.unlink()
            else:
                shutil.rmtree(repo_id)

        return {"success": True, "repo_id": repo_id}
    except Exception as e:
        print(f"LM Rewrite failed: {e}")
        # Cleanup on failure (handle symlinks)
        repo_path = Path(repo_id)
        if repo_path.exists():
            if repo_path.is_symlink():
                repo_path.unlink()
            else:
                shutil.rmtree(repo_id)
        return {"success": False, "error": str(e)}


def phase_pr_mirror(repo_id: str, model: str, dataset_path: str, profile) -> dict:
    """Phase 2 (Optional): PR Mirror - Mirror bugs from SWE-bench dataset."""
    print(f"\n{'='*60}")
    print(f"PHASE 2: PR MIRROR")
    print(f"{'='*60}\n")

    import shutil
    from swesmith.bug_gen.mirror.generate import main as mirror_main

    if not Path(dataset_path).exists():
        print(f"Dataset not found: {dataset_path}")
        return {"success": False, "error": "Dataset not found"}

    # Setup local repo for PR mirroring
    local_repo = f"/Users/aditya.singh.001/Desktop/{profile.repo}"
    if Path(local_repo).exists():
        print(f"Using local repo: {local_repo}")
        # Copy to expected location
        if Path(repo_id).exists():
            if Path(repo_id).is_dir() and not Path(repo_id).is_symlink():
                shutil.rmtree(repo_id)
            else:
                Path(repo_id).unlink()
        shutil.copytree(local_repo, repo_id, symlinks=True)
        # Trick profile
        profile._cache_mirror_exists = True
        print(f"Copied repo to {repo_id}")

    try:
        mirror_main(
            sweb_insts_files=[dataset_path],
            model=model,
            redo_existing=False,
            redo_skipped=False,
            num_processes=4,
            max_files=8,
            max_lines=500,
            max_file_lines=10000,
        )
        return {"success": True}
    except Exception as e:
        print(f"PR Mirror failed: {e}")
        import traceback
        traceback.print_exc()
        return {"success": False, "error": str(e)}


def phase_combine(repo_id: str, num_patches: int, limit_per_file: int, profile) -> dict:
    """Phase 3: Combine - Merge patches from same file."""
    print(f"\n{'='*60}")
    print(f"PHASE 3: COMBINE PATCHES")
    print(f"{'='*60}\n")

    from swesmith.bug_gen.combine.same_file import main as combine_main
    import shutil

    bug_gen_dir = LOG_DIR_BUG_GEN / repo_id
    if not bug_gen_dir.exists():
        print(f"Bug gen directory not found: {bug_gen_dir}")
        return {"success": False, "error": "No bugs to combine"}

    # Ensure repo is cloned locally for combining
    if not Path(repo_id).exists():
        print(f"Cloning repo for combine phase...")
        source_url = f"https://github.com/{profile.owner}/{profile.repo}.git"
        try:
            run_command(["git", "clone", source_url, repo_id], check=True)
            run_command(["git", "checkout", profile.commit], cwd=Path(repo_id), check=True)
        except Exception as e:
            print(f"Clone failed: {e}")
            return {"success": False, "error": f"Clone failed: {e}"}

    # Trick profile into using local clone
    profile._cache_mirror_exists = True

    try:
        combine_main(
            bug_gen_dir=str(bug_gen_dir),
            num_patches=num_patches,
            limit_per_file=limit_per_file,
            max_combos=100,
            include_invalid_patches=False,
        )
        # Cleanup
        if Path(repo_id).exists():
            shutil.rmtree(repo_id)
        return {"success": True}
    except Exception as e:
        print(f"Combine failed: {e}")
        if Path(repo_id).exists():
            shutil.rmtree(repo_id)
        return {"success": False, "error": str(e)}


def phase_collect_patches(repo_id: str) -> list[dict]:
    """Collect all patches after generation."""
    print(f"\n{'='*60}")
    print(f"COLLECTING PATCHES")
    print(f"{'='*60}\n")

    from swesmith.bug_gen.collect_patches import main as collect_patches_main

    bug_gen_dir = LOG_DIR_BUG_GEN / repo_id
    if not bug_gen_dir.exists():
        print(f"No bug gen directory: {bug_gen_dir}")
        return []

    try:
        collect_patches_main(str(bug_gen_dir))

        # Load collected patches
        patches_file = LOG_DIR_BUG_GEN / f"{repo_id}_all_patches.json"
        if patches_file.exists():
            with open(patches_file) as f:
                patches = json.load(f)
            print(f"Collected {len(patches)} patches")
            return patches
    except Exception as e:
        print(f"Collect patches failed: {e}")

    return []


def phase_validate(repo_id: str, profile) -> dict:
    """Phase 4: Validation - Run tests in Docker."""
    print(f"\n{'='*60}")
    print(f"PHASE 4: VALIDATION")
    print(f"{'='*60}\n")

    import docker
    from swesmith.harness.grading import get_valid_report, read_test_output
    from swebench.harness.constants import KEY_INSTANCE_ID, TestStatus

    patches_file = LOG_DIR_BUG_GEN / f"{repo_id}_all_patches.json"
    if not patches_file.exists():
        print(f"No patches file: {patches_file}")
        return {"success": False, "error": "No patches to validate"}

    with open(patches_file) as f:
        patches = json.load(f)

    if not patches:
        print("No patches to validate")
        return {"success": True, "validated": 0, "valid": 0}

    # Check if image exists locally
    image_name = profile.image_name
    print(f"Using image: {image_name}")

    try:
        result = run_command(["docker", "image", "inspect", image_name], check=False)
        if result.returncode != 0:
            print(f"Image not found locally: {image_name}")
            print("Please build the image first:")
            print(f"  python -c \"from swesmith.profiles.rust import HyperswitchProfile; p = HyperswitchProfile(); p.build_image()\"")
            return {"success": False, "error": "Docker image not found"}
    except Exception as e:
        return {"success": False, "error": f"Docker check failed: {e}"}

    validation_dir = LOG_DIR_RUN_VALIDATION / repo_id
    validation_dir.mkdir(parents=True, exist_ok=True)

    # Run baseline test first
    print("\nRunning baseline tests...")
    baseline_output_file = validation_dir / f"{repo_id}.ref" / "test_output.txt"
    baseline_output_file.parent.mkdir(parents=True, exist_ok=True)

    client = docker.from_env()
    validated = 0
    valid = 0

    # Create a shared volume for Rust target directory to avoid rebuilding
    # artifacts across multiple bug instances (only for Rust repos)
    volumes = {}
    if hasattr(profile, 'exts') and '.rs' in profile.exts:
        volume_name = f"swesmith-target-{profile.repo_name}"
        try:
            client.volumes.get(volume_name)
        except docker.errors.NotFound:
            client.volumes.create(volume_name)
        volumes[volume_name] = {"bind": "/testbed/target", "mode": "rw"}

    # Get baseline test output
    try:
        container = client.containers.run(
            image_name,
            command=f"bash -c 'cd /testbed && {profile.test_cmd}'",
            detach=False,
            remove=True,
            stdout=True,
            stderr=True,
            volumes=volumes,
        )
        baseline_output = container.decode('utf-8', errors='replace')
        baseline_output_file.write_text(baseline_output)
        print("Baseline test complete")
    except Exception as e:
        print(f"Baseline test failed: {e}")
        baseline_output = ""

    # Validate each patch
    print(f"\nValidating {len(patches)} patches...")
    for i, patch in enumerate(patches, 1):
        instance_id = patch.get("instance_id", f"patch_{i}")
        print(f"  [{i}/{len(patches)}] {instance_id}...", end=" ")

        patch_dir = validation_dir / instance_id
        patch_dir.mkdir(parents=True, exist_ok=True)

        # Save patch
        patch_file = patch_dir / "patch.diff"
        patch_file.write_text(patch.get("patch", ""))

        try:
            # Run test with patch - mount the patch file into the container
            # Use absolute path for Docker volume mounting
            abs_patch_path = str(patch_file.absolute())
            # Add patch mount to shared volume (for Rust repos)
            run_volumes = {abs_patch_path: {"bind": "/mnt/patch.diff", "mode": "rw"}}
            run_volumes.update(volumes)
            container = client.containers.run(
                image_name,
                command=f"bash -c 'cd /testbed && git apply /mnt/patch.diff && {profile.test_cmd}'",
                volumes=run_volumes,
                detach=False,
                remove=True,
                stdout=True,
                stderr=True,
            )
            output = container.decode('utf-8', errors='replace')

            # Save output
            output_file = patch_dir / "test_output.txt"
            output_file.write_text(output)

            # Grade results
            report = get_valid_report(
                str(baseline_output_file),
                str(output_file),
                patch
            )

            # Save report
            report_file = patch_dir / "report.json"
            report_file.write_text(json.dumps(report, indent=2))

            validated += 1
            if report.get("PASS_TO_FAIL"):
                valid += 1
                print("VALID")
            else:
                print("INVALID")

        except Exception as e:
            print(f"ERROR: {e}")
            # Save error report
            error_report = {"valid": False, "error": str(e), "PASS_TO_FAIL": []}
            (patch_dir / "report.json").write_text(json.dumps(error_report, indent=2))

    print(f"\nValidation complete: {valid}/{validated} valid bugs")
    return {"success": True, "validated": validated, "valid": valid}


def phase_gather(repo_id: str) -> dict:
    """Phase 5: Gather - Create task instances and push branches."""
    print(f"\n{'='*60}")
    print(f"PHASE 5: GATHER (Push Branches)")
    print(f"{'='*60}\n")

    from swesmith.harness.gather import _main as gather_main

    validation_dir = LOG_DIR_RUN_VALIDATION / repo_id
    if not validation_dir.exists():
        print(f"No validation directory: {validation_dir}")
        return {"success": False, "error": "Run validation first"}

    try:
        # Call gather_main directly with the required validation_logs_path argument
        gather_main(
            validation_logs_path=str(validation_dir),
            debug_subprocess=False,
            override_branch=False,
            repush_image=False,
            verbose=True,
        )

        # Check for task instances
        task_insts_file = LOG_DIR_TASKS / f"{repo_id}.json"
        if task_insts_file.exists():
            with open(task_insts_file) as f:
                task_insts = json.load(f)
            print(f"Created {len(task_insts)} task instances")
            return {"success": True, "instances": len(task_insts)}

        return {"success": True, "instances": 0}
    except Exception as e:
        print(f"Gather failed: {e}")
        import traceback
        traceback.print_exc()
        return {"success": False, "error": str(e)}


def phase_issue_gen(repo_id: str, model: str, config_file: str, workers: int) -> dict:
    """Phase 6: Issue Generation - Generate GitHub-style issues for validated tasks using litellm."""
    print(f"\n{'='*60}")
    print(f"PHASE 6: ISSUE GENERATION")
    print(f"{'='*60}\n")

    from swesmith.issue_gen.generate import IssueGen
    from swesmith.constants import LOG_DIR_TASKS

    # Find task instances file (could be .json or .jsonl)
    task_insts_file_json = LOG_DIR_TASKS / f"{repo_id}.json"
    task_insts_file_jsonl = LOG_DIR_TASKS / f"{repo_id}.jsonl"

    if task_insts_file_json.exists():
        dataset_path = str(task_insts_file_json)
    elif task_insts_file_jsonl.exists():
        dataset_path = str(task_insts_file_jsonl)
    else:
        print(f"No task instances file found: {task_insts_file_json} or {task_insts_file_jsonl}")
        return {"success": False, "error": "Run gather first to create task instances"}

    # Check if config file exists
    if not Path(config_file).exists():
        print(f"Config file not found: {config_file}")
        return {"success": False, "error": f"Config file not found: {config_file}"}

    try:
        # Load the config and override model if specified
        import yaml
        with open(config_file) as f:
            config = yaml.safe_load(f)

        # Use model from args if provided, else use config model, else default to env model
        if model:
            config['model'] = model
            print(f"Using model from args: {model}")
        elif 'model' not in config:
            # Try to get from environment
            env_model = os.getenv("LITE_LLM_MODEL")
            if env_model:
                config['model'] = env_model
                print(f"Using model from LITE_LLM_MODEL env: {env_model}")
            else:
                config['model'] = "openai/gpt-4o"
                print(f"Using default model: openai/gpt-4o")

        # Ensure we're NOT using portkey - force litellm usage
        if config['model'].startswith("portkey/"):
            config['model'] = config['model'].replace("portkey/", "openai/")
            print(f"Converting portkey model to litellm format: {config['model']}")

        # Remove provider portkey if set
        if config.get('provider') == 'portkey':
            del config['provider']
            print("Removed portkey provider to use litellm directly")

        # Create temp config file with updated model
        import tempfile
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as tmp:
            yaml.dump(config, tmp)
            tmp_config_path = tmp.name

        try:
            # Initialize IssueGen with our temp config
            issue_gen = IssueGen(
                config_file=Path(tmp_config_path),
                workers=workers,
                dataset_path=dataset_path,
                redo_existing=False,
            )

            # Override the model to use litellm directly (not portkey)
            issue_gen.portkey_model = None

            # Run issue generation
            issue_gen.run()

            return {"success": True}
        finally:
            # Clean up temp config
            if os.path.exists(tmp_config_path):
                os.unlink(tmp_config_path)

    except Exception as e:
        print(f"Issue generation failed: {e}")
        import traceback
        traceback.print_exc()
        return {"success": False, "error": str(e)}


def main(
    repo: str,
    phases: str = "gen,val,gather,issue",
    model: str = None,
    max_bugs: int = 200,
    enable_lm_rewrite: bool = True,
    enable_pr_mirror: bool = True,
    pr_mirror_dataset: str = None,
    enable_combine: bool = True,
    combine_num_patches: int = 2,
    combine_limit_per_file: int = 10,
    issue_config: str = "configs/issue_gen/ig_v2.yaml",
    issue_workers: int = 4,
):
    """Main entry point."""
    print(f"\n{'#'*60}")
    print(f"# LOCAL BUG GENERATION")
    print(f"# Repo: {repo}")
    print(f"# Phases: {phases}")
    print(f"# LM Rewrite: {enable_lm_rewrite}")
    print(f"# PR Mirror: {enable_pr_mirror} (dataset: {pr_mirror_dataset or 'Not provided'})")
    print(f"# Combine: {enable_combine}")
    print(f"# Issue Config: {issue_config}")
    print(f"{'#'*60}\n")

    # Resolve repo ID
    repo_id = get_repo_id(repo)
    print(f"Resolved repo_id: {repo_id}")

    # Get profile
    try:
        profile = registry.get(repo_id)
        print(f"Profile: {profile.__class__.__name__}")
        print(f"Image: {profile.image_name}")
    except Exception as e:
        print(f"Failed to get profile: {e}")
        return

    # Parse phases
    phase_list = [p.strip() for p in phases.split(",")]
    results = {}

    # Phase 1-3: Generation (Always run all 3 if gen phase enabled)
    if "gen" in phase_list:
        print(f"\nRunning ALL 3 generation methods:\n")

        # 1. LM Rewrite
        if enable_lm_rewrite:
            results["lm_rewrite"] = phase_lm_rewrite(repo_id, model, max_bugs, profile)
        else:
            print("Skipping LM Rewrite (disabled)")

        # 2. PR Mirror (run even if LM Rewrite failed, as long as dataset provided)
        if enable_pr_mirror:
            if pr_mirror_dataset:
                results["pr_mirror"] = phase_pr_mirror(repo_id, model, pr_mirror_dataset, profile)
            else:
                print("Skipping PR Mirror (no dataset provided)")
                results["pr_mirror"] = {"success": False, "error": "No dataset provided"}
        else:
            print("Skipping PR Mirror (disabled)")

        # 3. Combine (always runs if bugs were generated from any method)
        if enable_combine:
            results["combine"] = phase_combine(repo_id, combine_num_patches, combine_limit_per_file, profile)
        else:
            print("Skipping Combine (disabled)")

        # Collect all patches from all methods
        patches = phase_collect_patches(repo_id)
        results["total_patches"] = len(patches)
        print(f"\nTotal patches from all methods: {len(patches)}")

    # Phase 4: Validation
    if "val" in phase_list:
        results["validation"] = phase_validate(repo_id, profile)

    # Phase 5: Gather
    if "gather" in phase_list:
        results["gather"] = phase_gather(repo_id)

    # Phase 6: Issue Generation
    if "issue" in phase_list:
        results["issue_gen"] = phase_issue_gen(repo_id, model, issue_config, issue_workers)

    # Summary
    print(f"\n{'='*60}")
    print(f"SUMMARY")
    print(f"{'='*60}")
    for key, value in results.items():
        if isinstance(value, dict):
            status = "✓" if value.get("success") else "✗"
            print(f"  {status} {key}: {value}")
        else:
            print(f"  • {key}: {value}")


def str2bool(v):
    """Convert string to boolean for argparse."""
    if isinstance(v, bool):
        return v
    if v.lower() in ('yes', 'true', 't', 'y', '1'):
        return True
    elif v.lower() in ('no', 'false', 'f', 'n', '0'):
        return False
    else:
        raise argparse.ArgumentTypeError('Boolean value expected.')


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Local Bug Generation & Validation")
    parser.add_argument("--repo", required=True, help="Repository name (e.g., juspay/hyperswitch)")
    parser.add_argument("--phases", default="gen,val,gather,issue", help="Phases to run (gen,val,gather,issue)")
    parser.add_argument("--model", default=None, help="LLM model (uses LITE_LLM_MODEL from .env if not set)")
    parser.add_argument("--max-bugs", type=int, default=200, help="Max bugs to generate")
    parser.add_argument("--enable-lm-rewrite", type=str2bool, default=True, help="Enable LM rewrite (default: True)")
    parser.add_argument("--enable-pr-mirror", type=str2bool, default=True, help="Enable PR mirror (default: True)")
    parser.add_argument("--pr-mirror-dataset", help="Path to SWE-bench task instances JSONL")
    parser.add_argument("--enable-combine", type=str2bool, default=True, help="Enable patch combining (default: True)")
    parser.add_argument("--combine-num-patches", type=int, default=2, help="Number of patches to merge")
    parser.add_argument("--combine-limit-per-file", type=int, default=10, help="Max combined patches per file")
    parser.add_argument("--issue-config", default="configs/issue_gen/ig_v2.yaml", help="Config file for issue generation")
    parser.add_argument("--issue-workers", type=int, default=4, help="Number of workers for issue generation")

    args = parser.parse_args()
    main(**vars(args))
