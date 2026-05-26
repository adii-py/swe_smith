#!/usr/bin/env python3
"""
Generate targeted test patches for instances that need them.
Uses LLM to create #[cfg(test)] modules that detect the bug.
"""

import json
import os
import re
from pathlib import Path
from unidiff import PatchSet
from litellm import completion

# Hardcode correct config
MODEL = "openai/kimi-latest"
API_BASE = "https://grid.ai.juspay.net/v1"

# Read API key from .env
API_KEY = ""
env_path = Path("/Users/aditya.singh.001/Desktop/SWE-smith/.env")
if env_path.exists():
    for line in env_path.read_text().split("\n"):
        if line.startswith("LITE_LLM_API_KEY="):
            API_KEY = line.split("=", 1)[1].strip().strip('"').strip("'")
            break

REPO_PATH = Path("/tmp/hyperswitch")
DATASET_PATH = Path("/Users/aditya.singh.001/Desktop/SWE-smith/logs/bug_gen/juspay__hyperswitch.fece9bc3/pr_mirror/recovered_dataset.json")
NEED_PATCHES_LIST = Path("/Users/aditya.singh.001/Desktop/SWE-smith/logs/bug_gen/juspay__hyperswitch.fece9bc3/pr_mirror/need_test_patches.txt")
OUTPUT_PATH = Path("/Users/aditya.singh.001/Desktop/SWE-smith/logs/bug_gen/juspay__hyperswitch.fece9bc3/pr_mirror/recovered_dataset_with_test_patches.json")


def get_first_modified_file(patch_text: str) -> tuple[str, str] | None:
    """Return (filepath, file_content) for the first modified source file."""
    try:
        ps = PatchSet(patch_text)
        for pf in ps:
            if not pf.path.endswith(".rs"):
                continue
            path = REPO_PATH / pf.path
            if path.exists() and path.stat().st_size < 200000:
                return pf.path, path.read_text()
    except Exception as e:
        print(f"  Error parsing patch: {e}")
    return None


def extract_changed_functions(patch_text: str) -> list[str]:
    """Extract function names that were modified in the patch."""
    funcs = []
    structs = []
    try:
        ps = PatchSet(patch_text)
        for pf in ps:
            for hunk in pf:
                for line in hunk:
                    if line.is_added or line.is_removed:
                        # Match fn declarations
                        m = re.search(r'fn\s+(\w+)', line.value)
                        if m and m.group(1) not in funcs:
                            funcs.append(m.group(1))
                        # Match impl blocks (struct/trait methods)
                        m = re.search(r'impl(?:<[^>]+>)?\s+(?:\w+::)*([A-Z]\w+)', line.value)
                        if m and m.group(1) not in structs:
                            structs.append(m.group(1))
                        # Match struct definitions
                        m = re.search(r'struct\s+([A-Z]\w+)', line.value)
                        if m and m.group(1) not in structs:
                            structs.append(m.group(1))
    except Exception:
        pass
    return funcs, structs


def generate_test_patch(instance: dict, analysis: dict) -> str | None:
    """Use LLM to generate a Rust test patch that detects the bug."""
    patch = instance.get("patch", "")
    title = instance["title"]
    iid = instance["instance_id"]

    file_info = get_first_modified_file(patch)
    if not file_info:
        print(f"  {iid}: Could not read modified file")
        return None

    filepath, file_content = file_info
    changed_funcs, changed_structs = extract_changed_functions(patch)
    funcs_str = ", ".join(changed_funcs[:5]) if changed_funcs else "(see patch)"
    structs_str = ", ".join(changed_structs[:3]) if changed_structs else ""

    # Get crate from filepath
    crate = filepath.split("/")[1] if len(filepath.split("/")) > 1 else "unknown"

    # Truncate file content to keep prompt manageable
    lines = file_content.split("\n")
    if len(lines) > 300:
        # Keep first 100 and last 150 lines
        file_content = "\n".join(lines[:100] + ["// ... (truncated) ..."] + lines[-150:])

    # Read the actual patch to understand what changed
    patch_summary = ""
    try:
        ps = PatchSet(patch)
        for pf in ps[:1]:  # Just first file
            patch_summary = f"File: {pf.path}\n"
            for hunk in pf[:3]:  # First 3 hunks
                for line in hunk[:20]:  # First 20 lines per hunk
                    prefix = "+" if line.is_added else "-" if line.is_removed else " "
                    patch_summary += f"{prefix} {line.value}\n"
    except:
        patch_summary = patch[:500]

    prompt = f"""You are an expert Rust engineer. Your task is to write a UNIT TEST that detects a bug.

IMPORTANT: The test MUST be wrapped in a `#[cfg(test)]` module with the EXACT structure shown below.

BUG INFORMATION:
- PR Title: {title}
- Changed File: {filepath}
- Changed Function(s): {funcs_str}

PATCH CONTEXT (showing what changed):
```
{patch_summary}
```

YOUR TASK:
Write a test module with 1-2 test functions that:
1. Call the changed function(s) with specific test inputs
2. Assert the CORRECT behavior (what it should do when fixed)
3. The assertion MUST fail when the bug patch is applied

RULES:
- Output ONLY the test code, NOTHING else
- NO markdown explanations before or after
- NO code comments explaining the bug
- MUST use this EXACT structure:

#[cfg(test)]
mod tests {{
    use super::*;

    #[test]
    fn test_should_detect_bug() {{
        // Test code that calls the changed function
        // and asserts correct behavior
    }}
}}

START YOUR RESPONSE WITH #[cfg(test)]:
"""

    try:
        response = completion(
            model=MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=3000,
            timeout=180,
            api_key=API_KEY,
            api_base=API_BASE,
        )
        output = response.choices[0].message.content

        # Extract Rust code from fences
        code = None
        for pat in [r'```rust\s*\n(.*?)```', r'```\s*\n(#[cfg\(test\)].*?)```']:
            m = re.search(pat, output, re.DOTALL)
            if m:
                code = m.group(1).strip()
                break

        if not code and output.strip().startswith("#[cfg(test)]"):
            code = output.strip()

        if not code:
            print(f"  {iid}: Could not extract test code from LLM output")
            print(f"     Output preview: {output[:200]}...")
            return None

        # Build a git diff patch that APPENDS the test module
        original_lines = file_content.count("\n") + 1
        test_lines = code.count("\n") + 1

        diff_lines = [
            f"diff --git a/{filepath} b/{filepath}",
            "index 0000000..1111111 100644",
            f"--- a/{filepath}",
            f"+++ b/{filepath}",
            f"@@ -{original_lines},0 +{original_lines},{test_lines} @@",
        ]
        for line in code.split("\n"):
            diff_lines.append("+" + line)

        return "\n".join(diff_lines) + "\n"

    except Exception as e:
        print(f"  {iid}: LLM error: {e}")
        return None


def main():
    # Load dataset
    print(f"Loading dataset from {DATASET_PATH}")
    with open(DATASET_PATH) as f:
        data = json.load(f)

    # Create lookup by instance_id
    instances_by_id = {inst["instance_id"]: inst for inst in data}

    # Load list of instances needing test patches
    with open(NEED_PATCHES_LIST) as f:
        need_patches = [line.strip() for line in f if line.strip()]

    print(f"Found {len(need_patches)} instances needing test patches")

    # Load analysis for context
    analysis_path = Path("/Users/aditya.singh.001/Desktop/SWE-smith/logs/bug_gen/juspay__hyperswitch.fece9bc3/pr_mirror/test_coverage_analysis.json")
    analysis_by_id = {}
    if analysis_path.exists():
        with open(analysis_path) as f:
            analysis_list = json.load(f)
            analysis_by_id = {a["instance_id"]: a for a in analysis_list}

    # Process each instance
    generated = 0
    failed = 0

    for i, iid in enumerate(need_patches):
        if iid not in instances_by_id:
            print(f"[{i+1}/{len(need_patches)}] {iid}: NOT FOUND in dataset")
            failed += 1
            continue

        instance = instances_by_id[iid]
        analysis = analysis_by_id.get(iid, {})

        print(f"[{i+1}/{len(need_patches)}] Generating test patch for {iid}...")
        print(f"       Title: {instance['title'][:60]}...")
        print(f"       Strategy: {analysis.get('test_strategy', 'unknown')}")

        # Skip if already has test_patch
        if instance.get("test_patch"):
            print(f"       Already has test_patch, skipping")
            generated += 1
            continue

        test_patch = generate_test_patch(instance, analysis)

        if test_patch:
            instance["test_patch"] = test_patch
            print(f"       SUCCESS - Generated {len(test_patch)} char test patch")
            generated += 1
        else:
            print(f"       FAILED - Could not generate test patch")
            failed += 1

        # Save progress after each instance
        with open(OUTPUT_PATH, "w") as f:
            json.dump(data, f, indent=2)

        print()

    print("="*80)
    print(f"SUMMARY: Generated {generated} test patches, Failed {failed}")
    print(f"Output saved to: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
