#!/usr/bin/env python3
"""
Script to validate and fix bug patches for compilation.

This script:
1. Applies each bug patch to a clean container
2. Checks if the code compiles
3. If compilation fails, attempts to fix the patch
4. Generates a new dataset with compilation-safe bug patches
"""

import json
import subprocess
import tempfile
import os
from pathlib import Path
from typing import Dict, List, Tuple, Optional
import docker
from datetime import datetime


def run_in_container(image: str, command: str, timeout: int = 300) -> Tuple[int, str]:
    """Run a command in a Docker container and return exit code and output."""
    client = docker.from_env()
    try:
        container = client.containers.run(
            image,
            command=f"bash -c '{command}'",
            detach=True,
            remove=False,
            mem_limit="4g",
        )

        try:
            result = container.wait(timeout=timeout)
            logs = container.logs().decode("utf-8")
            container.remove()
            return result["StatusCode"], logs
        except Exception as e:
            container.kill()
            container.remove()
            return -1, f"Timeout or error: {str(e)}"
    except Exception as e:
        return -1, f"Docker error: {str(e)}"


def check_compilation(image: str, crate_name: str) -> Tuple[bool, str]:
    """
    Check if a specific crate compiles.
    Returns (success, error_message)
    """
    cmd = f"""
cd /testbed
cargo check -p {crate_name} 2>&1
"""
    exit_code, output = run_in_container(image, cmd, timeout=600)

    if exit_code == 0:
        return True, ""
    else:
        return False, output


def apply_patch_in_container(image: str, patch_content: str) -> Tuple[bool, str]:
    """
    Apply a patch in a container.
    Returns (success, error_message)
    """
    # Create a temp file with the patch
    with tempfile.NamedTemporaryFile(mode="w", suffix=".diff", delete=False) as f:
        f.write(patch_content)
        patch_file = f.name

    try:
        # Copy patch to container and apply
        cmd = f"""
cd /testbed
cat > /tmp/patch.diff << 'PATCHEOF'
{patch_content}
PATCHEOF
git apply /tmp/patch.diff 2>&1
"""
        exit_code, output = run_in_container(image, cmd, timeout=60)

        if exit_code == 0:
            return True, ""
        else:
            return False, output
    finally:
        os.unlink(patch_file)


def fix_patch_for_compilation(patch_content: str) -> str:
    """
    Attempt to fix a patch so it compiles.

    Strategy:
    1. Remove any changes that modify public API signatures
    2. Keep only logic changes within function bodies
    3. Add feature gate guards if needed
    """
    lines = patch_content.split("\n")
    fixed_lines = []
    skip_mode = False
    in_function_body = False

    for line in lines:
        # Skip file headers - we'll reconstruct them
        if line.startswith("diff --git"):
            fixed_lines.append(line)
            skip_mode = False
            continue

        if (
            line.startswith("index ")
            or line.startswith("--- ")
            or line.startswith("+++ ")
        ):
            fixed_lines.append(line)
            continue

        if line.startswith("@@ "):
            # New hunk - check if it's in a function body
            fixed_lines.append(line)
            in_function_body = False
            # Simple heuristic: hunks with "fn " in context are function definitions
            if "fn " in line:
                in_function_body = False
            continue

        # Skip lines that add new public functions or types
        if line.startswith("+") and not line.startswith("+++"):
            stripped = line[1:].strip()
            # Skip public function additions
            if stripped.startswith("pub fn ") or stripped.startswith("pub async fn "):
                continue
            # Skip struct/enum additions
            if stripped.startswith("pub struct ") or stripped.startswith("pub enum "):
                continue
            # Skip trait implementations that might break things
            if stripped.startswith("impl ") and " for " in stripped:
                continue

        fixed_lines.append(line)

    return "\n".join(fixed_lines)


def create_safe_bug_patch(original_patch: str) -> Tuple[str, bool]:
    """
    Create a compilation-safe bug patch.

    Returns (safe_patch, is_safe)
    """
    # Strategy: Instead of complex transformations, create simple logic bugs
    # that don't change signatures but break behavior

    lines = original_patch.split("\n")
    safe_lines = []

    for line in lines:
        # Keep file headers and structure
        if line.startswith("diff --git"):
            safe_lines.append(line)
        elif line.startswith("index "):
            safe_lines.append(line)
        elif line.startswith("--- "):
            safe_lines.append(line)
        elif line.startswith("+++ "):
            safe_lines.append(line)
        elif line.startswith("@@ "):
            safe_lines.append(line)
        elif line.startswith(" "):  # Context line
            safe_lines.append(line)
        elif line.startswith("-") and not line.startswith("---"):
            # Deletion - keep it
            safe_lines.append(line)
        elif line.startswith("+") and not line.startswith("+++"):
            # Addition - check if safe
            content = line[1:].strip()

            # Keep additions that are:
            # 1. Inside function bodies (simple assignments, operations)
            # 2. Comments or whitespace
            # 3. Simple logic changes

            unsafe_patterns = [
                "pub fn",
                "pub struct",
                "pub enum",
                "pub trait",
                "pub type",
                "pub use",
                "pub mod",
                "#[derive",
                "#[allow",
                "#[warn",
            ]

            is_safe = not any(pattern in content for pattern in unsafe_patterns)

            if is_safe:
                safe_lines.append(line)

    return "\n".join(safe_lines), len(safe_lines) > 5


def validate_and_fix_instance(instance: Dict, image: str) -> Tuple[Dict, bool]:
    """
    Validate and fix a single instance.

    Returns (fixed_instance, success)
    """
    instance_id = instance.get("instance_id", "unknown")
    print(f"\n{'=' * 60}")
    print(f"Processing: {instance_id}")
    print(f"{'=' * 60}")

    patch = instance.get("patch", "")
    if not patch:
        print(f"  [SKIP] No patch found")
        return instance, False

    # Try to apply patch and check compilation
    print(f"  Step 1: Applying patch...")
    success, error = apply_patch_in_container(image, patch)

    if not success:
        print(f"  [FAIL] Could not apply patch: {error[:200]}")
        return instance, False

    # Determine which crate to check
    if "crates/analytics/" in patch:
        crate_name = "analytics"
    elif "crates/router/" in patch:
        crate_name = "router"
    elif "crates/common_utils/" in patch:
        crate_name = "common_utils"
    else:
        crate_name = "analytics"  # Default

    print(f"  Step 2: Checking compilation of {crate_name}...")
    compiles, comp_error = check_compilation(image, crate_name)

    if compiles:
        print(f"  [SUCCESS] Patch compiles!")
        return instance, True

    print(f"  [FAIL] Compilation error: {comp_error[:300]}")

    # Try to fix the patch
    print(f"  Step 3: Attempting to fix patch...")
    safe_patch, has_changes = create_safe_bug_patch(patch)

    if not has_changes:
        print(f"  [SKIP] No safe changes could be extracted")
        return instance, False

    # Test the fixed patch
    print(f"  Step 4: Testing fixed patch...")
    success, error = apply_patch_in_container(image, safe_patch)

    if not success:
        print(f"  [FAIL] Fixed patch could not be applied")
        return instance, False

    compiles, comp_error = check_compilation(image, crate_name)

    if compiles:
        print(f"  [SUCCESS] Fixed patch compiles!")
        fixed_instance = instance.copy()
        fixed_instance["patch"] = safe_patch
        fixed_instance["original_patch"] = patch
        fixed_instance["was_fixed"] = True
        return fixed_instance, True
    else:
        print(f"  [FAIL] Fixed patch still doesn't compile")
        return instance, False


def main():
    # Configuration
    IMAGE = "swebench/swesmith.arm64.juspay_1776_hyperswitch.fece9bc3:latest"
    INPUT_FILE = "/Users/aditya.singh.001/Desktop/SWE-smith/logs/bug_gen/juspay__hyperswitch.fece9bc3/pr_mirror/recovered_dataset_with_test_patches.json"
    OUTPUT_FILE = "/Users/aditya.singh.001/Desktop/SWE-smith/logs/bug_gen/juspay__hyperswitch.fece9bc3/pr_mirror/recovered_dataset_compilation_safe.json"

    print(f"Loading dataset from: {INPUT_FILE}")
    with open(INPUT_FILE, "r") as f:
        instances = json.load(f)

    print(f"Total instances: {len(instances)}")

    # Process first 10 instances as a pilot
    pilot_instances = instances[:10]
    print(f"\nProcessing pilot batch of {len(pilot_instances)} instances...")

    fixed_instances = []
    successful_count = 0

    for i, instance in enumerate(pilot_instances):
        print(f"\n[{i + 1}/{len(pilot_instances)}] ", end="")
        fixed_instance, success = validate_and_fix_instance(instance, IMAGE)
        fixed_instances.append(fixed_instance)
        if success:
            successful_count += 1

    print(f"\n\n{'=' * 60}")
    print(f"SUMMARY")
    print(f"{'=' * 60}")
    print(f"Total processed: {len(pilot_instances)}")
    print(f"Successful (compile): {successful_count}")
    print(f"Failed: {len(pilot_instances) - successful_count}")

    # Save results
    print(f"\nSaving results to: {OUTPUT_FILE}")
    with open(OUTPUT_FILE, "w") as f:
        json.dump(fixed_instances, f, indent=2)

    print("Done!")


if __name__ == "__main__":
    main()
