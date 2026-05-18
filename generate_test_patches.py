#!/usr/bin/env python3
"""
Generate test patches for instances without natural test coverage.
Uses LLM to write Rust tests based on the bug patch context.
"""

import json
import os
import re
from pathlib import Path
from unidiff import PatchSet

os.environ.setdefault("LITE_LLM_MODEL", "openai/kimi-latest")
os.environ.setdefault("LITE_LLM_BASE_URL", "https://grid.ai.juspay.net/v1")

from litellm import completion

DATASET_PATH = "/Users/aditya.singh.001/Desktop/SWE-smith/logs/bug_gen/juspay__hyperswitch.fece9bc3/pr_mirror/recovered_dataset_clean.json"
REPO_PATH = Path("/tmp/hyperswitch")
OUTPUT_PATH = "/Users/aditya.singh.001/Desktop/SWE-smith/logs/bug_gen/juspay__hyperswitch.fece9bc3/pr_mirror/recovered_dataset_with_tests.json"


def get_modified_file_content(patch_text: str) -> tuple[str, str] | None:
    """Get the first modified source file and its content from repo."""
    try:
        ps = PatchSet(patch_text)
        for pf in ps:
            path = REPO_PATH / pf.path
            if path.exists() and path.stat().st_size < 50000:
                return pf.path, path.read_text()
    except Exception:
        pass
    return None


def generate_test_patch(instance: dict) -> str | None:
    """Use LLM to generate a test patch for this instance."""
    patch = instance.get("patch", "")
    title = instance["title"]
    iid = instance["instance_id"]

    file_info = get_modified_file_content(patch)
    if not file_info:
        print(f"  {iid}: Could not read modified file")
        return None

    filepath, file_content = file_info
    crate = filepath.split("/")[1] if len(filepath.split("/")) > 1 else "unknown"

    # Find the function that was modified
    try:
        ps = PatchSet(patch)
        changed_funcs = []
        for pf in ps:
            for hunk in pf:
                for line in hunk:
                    if line.is_added or line.is_removed:
                        m = re.search(r'fn\s+(\w+)', line.value)
                        if m and m.group(1) not in changed_funcs:
                            changed_funcs.append(m.group(1))
    except Exception:
        changed_funcs = []

    changed_funcs_str = ", ".join(changed_funcs[:3]) if changed_funcs else "(unknown)"

    prompt = f"""You are an expert Rust tester writing unit tests for a payment orchestration platform called Hyperswitch.

TASK: Write a Rust unit test that will FAIL when the following bug is present, and PASS when the bug is fixed.

BUG DESCRIPTION (from PR title): {title}

MODIFIED FILE: {filepath}
CRATE: {crate}
CHANGED FUNCTIONS: {changed_funcs_str}

Here is the modified source file (truncated to relevant section):
```rust
{file_content[:4000]}
```

Here is the bug patch (the code change that introduces the bug):
```diff
{patch}
```

INSTRUCTIONS:
1. Write 2-3 focused Rust unit tests in a single test module
2. The tests should exercise the specific behavior that the patch changes
3. Import necessary types and functions from the crate
4. Use realistic but minimal mock data (don't need real API calls)
5. Each test should have a clear assertion that would fail with the bug patch applied
6. Output ONLY the test code as a diff patch that can be applied to create a new test file
7. Create the test file at `crates/{crate}/tests/<name>.rs` or inline `#[cfg(test)]` module

FORMAT: Output as a unified diff patch like:
```diff
--- /dev/null
+++ crates/{crate}/tests/generated_test.rs
@@ -0,0 +1,XX @@
+<test code>
```
"""

    try:
        response = completion(
            model=os.environ["LITE_LLM_MODEL"],
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=4000,
            timeout=120,
        )
        output = response.choices[0].message.content

        # Extract diff from code fences
        diff_match = re.search(r'```diff\s*\n(.*?)```', output, re.DOTALL)
        if diff_match:
            return diff_match.group(1).strip()

        # Try without diff marker
        diff_match = re.search(r'```\s*\n(---.*?)(?=```|$)', output, re.DOTALL)
        if diff_match:
            return diff_match.group(1).strip()

        # Fallback: if output starts with ---, use it directly
        if output.strip().startswith("---"):
            return output.strip()

        print(f"  {iid}: Could not extract diff from LLM output")
        return None
    except Exception as e:
        print(f"  {iid}: LLM error: {e}")
        return None


def main():
    with open(DATASET_PATH) as f:
        data = json.load(f)

    # Categorize instances
    router_with_coverage = []
    need_test_patches = []

    for inst in data:
        patch = inst.get("patch", "")
        try:
            ps = PatchSet(patch)
            files = [pf.path for pf in ps]
        except Exception:
            files = []

        crates = set()
        for fpath in files:
            parts = fpath.split("/")
            if len(parts) > 1 and parts[0] == "crates":
                crates.add(parts[1])

        test_files = []
        for c in crates:
            crate_path = REPO_PATH / "crates" / c
            if crate_path.exists():
                test_files.extend(list(crate_path.rglob("tests/*.rs")))

        if "router" in inst.get("test_cmd", "") and test_files:
            router_with_coverage.append(inst)
        else:
            need_test_patches.append(inst)

    print(f"Router instances with potential natural coverage: {len(router_with_coverage)}")
    print(f"Instances needing test patches: {len(need_test_patches)}")
    print()

    # Save router-focused dataset for validation
    router_path = DATASET_PATH.replace("_clean.json", "_router_natural.json")
    with open(router_path, "w") as f:
        json.dump(router_with_coverage, f, indent=2)
    print(f"Saved router dataset to: {router_path}")

    # Generate test patches for a batch
    batch_size = 10
    batch = need_test_patches[:batch_size]
    print(f"\nGenerating test patches for first {batch_size} instances...")

    updated = []
    for inst in batch:
        iid = inst["instance_id"]
        print(f"\n{iid} (PR #{inst['pull_number']}): {inst['title'][:60]}")
        test_patch = generate_test_patch(inst)
        if test_patch:
            inst["test_patch"] = test_patch
            print(f"  -> Generated test patch ({len(test_patch)} chars)")
        else:
            print(f"  -> FAILED to generate test patch")
        updated.append(inst)

    # Save progress
    with open(OUTPUT_PATH, "w") as f:
        json.dump(updated, f, indent=2)
    print(f"\nSaved batch with test patches to: {OUTPUT_PATH}")

    # Also save the full dataset with test patches merged
    remaining = need_test_patches[batch_size:]
    full_data = router_with_coverage + updated + remaining
    full_path = DATASET_PATH.replace("_clean.json", "_with_tests_partial.json")
    with open(full_path, "w") as f:
        json.dump(full_data, f, indent=2)
    print(f"Saved full partial dataset to: {full_path}")


if __name__ == "__main__":
    main()
