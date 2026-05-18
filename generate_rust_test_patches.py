#!/usr/bin/env python3
"""
Generate Rust test patches for Hyperswitch bug instances.
Appends #[cfg(test)] modules to modified source files.
Uses LLM to generate tests based on patch context.
"""

import json
import os
import re
from pathlib import Path
from unidiff import PatchSet
from litellm import completion

# Hardcode correct config (system env has wrong values)
MODEL = "openai/kimi-latest"
API_BASE = "https://grid.ai.juspay.net/v1"

# Read API key from .env manually to avoid env var pollution
API_KEY = ""
env_path = Path("/Users/aditya.singh.001/Desktop/SWE-smith/.env")
if env_path.exists():
    for line in env_path.read_text().split("\n"):
        if line.startswith("LITE_LLM_API_KEY="):
            API_KEY = line.split("=", 1)[1].strip().strip('"').strip("'")
            break

print(f"LLM Config: model={MODEL}, base={API_BASE}, key={'set' if API_KEY else 'MISSING'}")

DATASET_PATH = "/Users/aditya.singh.001/Desktop/SWE-smith/logs/bug_gen/juspay__hyperswitch.fece9bc3/pr_mirror/recovered_dataset_clean.json"
REPO_PATH = Path("/tmp/hyperswitch")


def get_first_modified_file(patch_text: str) -> tuple[str, str] | None:
    """Return (filepath, file_content) for the first modified source file."""
    try:
        ps = PatchSet(patch_text)
        for pf in ps:
            if not pf.path.endswith(".rs"):
                continue
            path = REPO_PATH / pf.path
            if path.exists() and path.stat().st_size < 100000:
                return pf.path, path.read_text()
    except Exception as e:
        print(f"  Error parsing patch: {e}")
    return None


def extract_changed_function_names(patch_text: str) -> list[str]:
    """Extract function names that were modified in the patch."""
    funcs = []
    try:
        ps = PatchSet(patch_text)
        for pf in ps:
            for hunk in pf:
                for line in hunk:
                    if line.is_added or line.is_removed:
                        m = re.search(r'fn\s+(\w+)', line.value)
                        if m and m.group(1) not in funcs:
                            funcs.append(m.group(1))
    except Exception:
        pass
    return funcs


def generate_test_patch(instance: dict) -> str | None:
    """Use LLM to generate a Rust test patch appended to the modified file."""
    patch = instance.get("patch", "")
    title = instance["title"]
    iid = instance["instance_id"]

    file_info = get_first_modified_file(patch)
    if not file_info:
        print(f"  {iid}: Could not read modified file")
        return None

    filepath, file_content = file_info
    changed_funcs = extract_changed_function_names(patch)
    funcs_str = ", ".join(changed_funcs[:3]) if changed_funcs else "(see patch)"

    # Truncate file content to keep prompt size manageable
    lines = file_content.split("\n")
    if len(lines) > 200:
        # Keep first 50 and last 100 lines to show imports and modified function context
        file_content = "\n".join(lines[:50] + ["// ... (truncated) ..."] + lines[-100:])

    prompt = f"""You are an expert Rust engineer writing unit tests for a payment orchestration platform called Hyperswitch.

TASK: Write a `#[cfg(test)]` module that will FAIL when the following bug is present, and PASS when the bug is fixed.

BUG DESCRIPTION (from PR title): {title}

MODIFIED FILE: {filepath}
CHANGED FUNCTIONS: {funcs_str}

Here is the current source file (the FIXED/gold state — without the bug patch applied):
```rust
{file_content}
```

Here is the BUG PATCH (the code change that INTRODUCES the bug when applied):
```diff
{patch}
```

INSTRUCTIONS:
1. Write 2-3 focused `#[test]` functions inside a `#[cfg(test)] mod tests {{ ... }}` block
2. The tests should exercise the EXACT behavior the patch changes
3. Use types and functions already visible in the file (same module scope)
4. Construct test inputs using `Default::default()` or explicit struct initialization where needed
5. Each test must have a clear assertion using `assert!`, `assert_eq!`, or `assert_ne!`
6. The assertion MUST fail when the bug patch is applied and pass when it is not
7. Output ONLY the `#[cfg(test)]` module code block — no explanations

OUTPUT FORMAT:
```rust
#[cfg(test)]
mod tests {{
    use super::*;

    #[test]
    fn test_xxx() {{
        // ...
    }}
}}
```
"""

    try:
        response = completion(
            model=MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=4000,
            timeout=120,
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
            return None

        # Build a git diff patch that APPENDS the test module to the file
        # We add it at the end, replacing the final newline if needed
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
    with open(DATASET_PATH) as f:
        data = json.load(f)

    print(f"Dataset: {len(data)} instances")
    print("=" * 60)

    # Pick first 5 connector instances to test the approach
    batch = data[:5]
    print(f"Generating test patches for first {len(batch)} instances...")

    updated = []
    for inst in batch:
        iid = inst["instance_id"]
        print(f"\n{iid} (PR #{inst['pull_number']}): {inst['title'][:60]}")
        test_patch = generate_test_patch(inst)
        if test_patch:
            inst["test_patch"] = test_patch
            print(f"  -> Generated test patch ({len(test_patch)} chars)")
        else:
            print(f"  -> FAILED")
        updated.append(inst)

    output_path = DATASET_PATH.replace("_clean.json", "_with_tests_batch.json")
    with open(output_path, "w") as f:
        json.dump(updated, f, indent=2)
    print(f"\nSaved to: {output_path}")


if __name__ == "__main__":
    main()
