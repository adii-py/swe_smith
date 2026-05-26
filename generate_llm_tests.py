#!/usr/bin/env python3
"""
Generate targeted test patches using LLM analysis.

This script sends patches to an LLM and asks it to generate tests that:
1. Identify what the bug is from the patch
2. Create tests that exercise the buggy code path
3. Include assertions that fail when bug is present but pass when fixed
"""

import json
import os
from pathlib import Path
from unidiff import PatchSet
import time
import requests

# Try to use LiteLLM if available, otherwise use direct API
try:
    from litellm import completion
    HAS_LITELLM = True
except ImportError:
    HAS_LITELLM = False
    import requests


def call_llm(prompt: str, model: str = "private-large") -> str:
    """Call LLM with prompt using LiteLLM proxy."""

    # Use LiteLLM proxy
    lite_llm_url = os.getenv("LITE_LLM_URL", "http://localhost:4000")
    api_key = os.getenv("ANTHROPIC_API_KEY") or os.getenv("LITE_LLM_API_KEY")

    if not api_key:
        raise ValueError("No API key found")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }

    response = requests.post(
        f"{lite_llm_url}/chat/completions",
        headers=headers,
        json={
            "model": model,
            "max_tokens": 2000,
            "temperature": 0.2,
            "messages": [{"role": "user", "content": prompt}]
        }
    )
    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"]


def create_test_generation_prompt(patch_text: str, file_path: str, crate_name: str) -> str:
    """Create a prompt for the LLM to generate a test."""

    prompt = f"""You are a Rust testing expert. Analyze this bug fix patch and generate a test that:
1. Identifies what bug was fixed
2. Creates a test that EXERCISES the buggy code path
3. Includes assertions that FAIL when the bug is present but PASS when fixed

PATCH FILE: {file_path}
CRATE: {crate_name}

```diff
{patch_text[:2000]}  # Truncate if too long
```

Generate a COMPLETE Rust test function that:
- Has the #[test] attribute
- Imports necessary modules
- Sets up test data that triggers the bug
- Calls the function/method that was modified
- Asserts the CORRECT behavior (which will fail with the bug)

ONLY output the Rust test code, nothing else. The test should be in a #[cfg(test)] module.
"""
    return prompt


def extract_test_from_response(response: str) -> str:
    """Extract Rust test code from LLM response."""
    # Look for code blocks
    if "```rust" in response:
        start = response.find("```rust") + 7
        end = response.find("```", start)
        return response[start:end].strip()
    elif "```" in response:
        start = response.find("```") + 3
        end = response.find("```", start)
        return response[start:end].strip()
    else:
        return response.strip()


def generate_test_for_instance(instance: dict) -> str:
    """Generate a test for a single instance using LLM."""

    patch_text = instance.get('patch', '')
    instance_id = instance.get('instance_id', '')

    # Extract file path and crate
    file_path = ''
    crate_name = 'unknown'
    try:
        patch = PatchSet(patch_text)
        for file in patch:
            if file.path.startswith('crates/'):
                file_path = file.path
                crate_name = file.path.split('/')[1]
                break
    except:
        pass

    if not file_path:
        print(f"  {instance_id}: Could not extract file path")
        return None

    # Create prompt
    prompt = create_test_generation_prompt(patch_text, file_path, crate_name)

    try:
        # Call LLM
        print(f"  Calling LLM for {instance_id}...")
        response = call_llm(prompt)
        test_code = extract_test_from_response(response)

        # Wrap in patch format
        test_patch = f"""diff --git a/{file_path} b/{file_path}
--- a/{file_path}
+++ b/{file_path}
@@ -1,3 +1,20 @@
+
+{test_code}
"""
        return test_patch

    except Exception as e:
        print(f"  Error generating test for {instance_id}: {e}")
        return None


def main():
    """Generate LLM-based tests for all instances."""

    dataset_path = Path('logs/bug_gen/juspay__hyperswitch.fece9bc3/pr_mirror/recovered_dataset_with_test_patches.json')
    output_path = Path('logs/bug_gen/juspay__hyperswitch.fece9bc3/pr_mirror/recovered_dataset_llm_tests.json')

    with open(dataset_path) as f:
        data = json.load(f)

    print(f"Generating LLM tests for {len(data)} instances...")
    print("This will take some time (rate limited)...\n")

    # Process all instances
    test_subset = data  # All instances

    for i, inst in enumerate(test_subset):
        instance_id = inst.get('instance_id', '')
        print(f"[{i+1}/{len(test_subset)}] {instance_id}")

        test_patch = generate_test_for_instance(inst)
        if test_patch:
            inst['test_patch'] = test_patch
            print(f"  ✓ Test generated")
        else:
            print(f"  ✗ Failed")

        # Rate limiting
        time.sleep(1)

    # Save
    with open(output_path, 'w') as f:
        json.dump(data, f, indent=2)

    print(f"\nSaved to: {output_path}")
    print(f"Generated tests for {len(test_subset)} instances")


if __name__ == '__main__':
    main()
