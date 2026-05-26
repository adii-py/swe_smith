#!/usr/bin/env python3
"""
Generate properly formatted test patches with correct unified diff format.

This script:
1. Fetches actual source files from GitHub at base_commit
2. Finds or creates #[cfg(test)] modules at proper locations
3. Generates valid unified diffs with correct context lines
"""

import json
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path
from unidiff import PatchSet
import requests


def call_llm(prompt: str, model: str = "private-large") -> str:
    """Call LLM with prompt using LiteLLM proxy."""
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
            "max_tokens": 3000,
            "temperature": 0.2,
            "messages": [{"role": "user", "content": prompt}]
        }
    )
    response.raise_for_status()
    return response.json()["choices"][0]["message"]["content"]


def fetch_file_from_github(repo: str, commit: str, file_path: str) -> str:
    """Fetch file content from GitHub at specific commit."""
    # Convert repo format from "owner__repo.hash" to "owner/repo"
    if '.' in repo:
        repo_clean = repo.rsplit('.', 1)[0].replace('__', '/')
    else:
        repo_clean = repo.replace('__', '/')

    url = f"https://raw.githubusercontent.com/{repo_clean}/{commit}/{file_path}"

    try:
        response = requests.get(url, timeout=30)
        if response.status_code == 200:
            return response.text
        else:
            print(f"  Failed to fetch {url}: {response.status_code}")
            return None
    except Exception as e:
        print(f"  Error fetching file: {e}")
        return None


def find_test_module_location(file_content: str) -> tuple:
    """
    Find the best location to insert a new test in the file.
    Returns (line_number, context_lines, is_appending_to_existing_module).
    """
    lines = file_content.split('\n')

    # Look for existing #[cfg(test)] module
    in_test_module = False
    test_module_start = -1
    test_module_indent = 0
    last_test_end = -1
    brace_count = 0

    for i, line in enumerate(lines):
        stripped = line.strip()

        # Found #[cfg(test)]
        if stripped == '#[cfg(test)]':
            in_test_module = True
            test_module_start = i
            # Check next line for mod tests {
            if i + 1 < len(lines):
                next_line = lines[i + 1]
                match = re.match(r'^(\s*)mod\s+\w+\s*\{', next_line)
                if match:
                    test_module_indent = len(match.group(1)) + 4  # +4 for inside module
            continue

        if in_test_module:
            # Count braces to find end of module
            brace_count += stripped.count('{') - stripped.count('}')

            # Track last #[test] function
            if stripped.startswith('#[test]') or stripped.startswith('#[tokio::test]'):
                # Find the end of this test function
                for j in range(i + 1, len(lines)):
                    if lines[j].strip().startswith('fn '):
                        # Found test function start, now find its end
                        fn_brace_count = 0
                        for k in range(j, len(lines)):
                            fn_brace_count += lines[k].count('{') - lines[k].count('}')
                            if fn_brace_count == 0 and '{' in lines[j:k+1].__str__():
                                last_test_end = k
                                break
                        break

            # End of module
            if brace_count == 0 and '}' in line and i > test_module_start + 1:
                # Return location to insert before closing brace
                return (i, lines[max(0, i-3):i], True)

    # No existing test module found - append to end of file
    # Find last non-empty line
    last_non_empty = len(lines) - 1
    while last_non_empty > 0 and not lines[last_non_empty].strip():
        last_non_empty -= 1

    return (last_non_empty + 1, lines[max(0, last_non_empty-2):last_non_empty+1], False)


def create_unified_diff(file_path: str, original_content: str, test_code: str,
                        insert_line: int, context_before: list, is_existing_module: bool) -> str:
    """Create a proper unified diff patch."""
    lines = original_content.split('\n')

    # Prepare the new content
    new_lines = test_code.strip().split('\n')

    # Calculate context
    context_start = max(0, insert_line - 3)
    context_end = min(len(lines), insert_line + 3)

    # Get context lines from original
    before_context = lines[context_start:insert_line]
    after_context = lines[insert_line:context_end]

    # Build the diff
    old_line_num = context_start + 1  # 1-indexed
    new_line_num = context_start + 1

    old_count = len(before_context) + len(after_context)
    new_count = len(before_context) + len(new_lines) + len(after_context)

    diff_lines = [
        f'diff --git a/{file_path} b/{file_path}',
        f'--- a/{file_path}',
        f'+++ b/{file_path}',
        f'@@ -{old_line_num},{old_count} +{new_line_num},{new_count} @@'
    ]

    # Add context before
    for line in before_context:
        diff_lines.append(' ' + line)

    # Add new test lines (with + prefix)
    for line in new_lines:
        diff_lines.append('+' + line)

    # Add context after
    for line in after_context:
        diff_lines.append(' ' + line)

    return '\n'.join(diff_lines) + '\n'


def create_test_module_code(test_function: str, is_appending: bool) -> str:
    """Create appropriate test code based on whether we're appending or creating new module."""
    test_function = test_function.strip()

    if is_appending:
        # Just add the test function with proper indentation
        lines = test_function.split('\n')
        indented = ['    ' + line if line.strip() else line for line in lines]
        return '\n' + '\n'.join(indented)
    else:
        # Create a new test module
        return f'''
#[cfg(test)]
mod regression_tests {{
    use super::*;

{chr(10).join("    " + line if line.strip() else line for line in test_function.split(chr(10)))}
}}
'''


def create_test_generation_prompt(patch_text: str, file_path: str, crate_name: str,
                                   file_content: str = None) -> str:
    """Create a prompt for the LLM to generate a test."""

    file_info = ""
    if file_content:
        # Include the last part of the file to show where tests should go
        lines = file_content.split('\n')
        tail = '\n'.join(lines[-50:]) if len(lines) > 50 else file_content
        file_info = f"""
CURRENT FILE CONTENT (last 50 lines):
```rust
{tail}
```
"""

    prompt = f"""You are a Rust testing expert. Analyze this bug fix patch and generate a regression test.

PATCH FILE: {file_path}
CRATE: {crate_name}

BUG PATCH:
```diff
{patch_text[:2500]}
```
{file_info}

Your task:
1. Identify what bug was fixed from the patch
2. Write a test function that exercises the buggy code path
3. The test should FAIL when the bug is present and PASS when fixed
4. Include proper assertions that verify the correct behavior

Requirements for the test:
- Must be a complete test function with #[test] attribute (or #[tokio::test] if async)
- Use descriptive name like test_<function_name>_handles_<bug_description>
- Import any types/functions needed from the patched module
- Create realistic test data that triggers the bug condition
- Assertions should catch the incorrect behavior

EXAMPLE FORMAT:
```rust
#[test]
fn test_parse_amount_handles_negative_values() {{
    let result = parse_amount("-100");
    assert!(result.is_err(), "Negative amounts should be rejected");
}}
```

ONLY output the test function code (with #[test] attribute), nothing else.
"""
    return prompt


def generate_test_for_instance(instance: dict, skip_existing: bool = True) -> tuple:
    """
    Generate a proper test patch for a single instance.
    Returns (test_patch, test_code) or (None, None) if failed.
    """
    patch_text = instance.get('patch', '')
    instance_id = instance.get('instance_id', '')
    repo = instance.get('repo', '')
    base_commit = instance.get('base_commit', '')

    # Extract file path and crate
    file_path = ''
    crate_name = 'unknown'
    try:
        ps = PatchSet(patch_text)
        for file in ps:
            if file.path.startswith('crates/'):
                file_path = file.path
                crate_name = file.path.split('/')[1]
                break
    except:
        pass

    if not file_path:
        print(f"  {instance_id}: Could not extract file path")
        return None, None

    print(f"  Fetching source file: {file_path}")

    # Fetch the actual source file
    file_content = fetch_file_from_github(repo, base_commit, file_path)
    if not file_content:
        print(f"  {instance_id}: Could not fetch source file")
        return None, None

    # Find where to insert the test
    insert_line, context_lines, is_existing_module = find_test_module_location(file_content)
    print(f"  Insert at line {insert_line + 1}, existing module: {is_existing_module}")

    # Create prompt
    prompt = create_test_generation_prompt(patch_text, file_path, crate_name, file_content)

    try:
        # Call LLM
        print(f"  Calling LLM...")
        response = call_llm(prompt)

        # Extract test code
        test_code = extract_test_from_response(response)
        if not test_code or '#[test]' not in test_code:
            print(f"  Invalid test code received")
            return None, None

        # Create proper test module code
        test_module_code = create_test_module_code(test_code, is_existing_module)

        # Create unified diff
        test_patch = create_unified_diff(
            file_path, file_content, test_module_code,
            insert_line, context_lines, is_existing_module
        )

        return test_patch, test_code

    except Exception as e:
        print(f"  Error: {e}")
        return None, None


def extract_test_from_response(response: str) -> str:
    """Extract Rust test code from LLM response."""
    if "```rust" in response:
        start = response.find("```rust") + 7
        end = response.find("```", start)
        return response[start:end].strip()
    elif "```" in response:
        start = response.find("```") + 3
        end = response.find("```", start)
        return response[start:end].strip()
    else:
        # Look for #[test] attribute
        if '#[test]' in response or '#[tokio::test]' in response:
            # Extract from #[test] to end of function
            match = re.search(r'(\#\[(?:test|tokio::test)\].*?\n\}\n?)', response, re.DOTALL)
            if match:
                return match.group(1).strip()
        return response.strip()


def main():
    """Generate proper test patches for all instances."""

    dataset_path = Path('logs/bug_gen/juspay__hyperswitch.fece9bc3/pr_mirror/recovered_dataset_with_test_patches.json')
    output_path = Path('logs/bug_gen/juspay__hyperswitch.fece9bc3/pr_mirror/recovered_dataset_proper_tests.json')

    if not dataset_path.exists():
        print(f"Dataset not found: {dataset_path}")
        return

    with open(dataset_path) as f:
        data = json.load(f)

    print(f"Generating proper test patches for {len(data)} instances...")
    print("This will fetch source files and generate valid unified diffs.\n")

    success_count = 0
    failed_count = 0

    for i, inst in enumerate(data):
        instance_id = inst.get('instance_id', '')
        print(f"[{i+1}/{len(data)}] {instance_id}")

        # Skip if already has a good test_patch
        if inst.get('test_patch') and i < 50:
            print(f"  Skipping (already has test_patch)")
            continue

        test_patch, test_code = generate_test_for_instance(inst)

        if test_patch:
            inst['test_patch'] = test_patch
            inst['_generated_test_code'] = test_code
            print(f"  ✓ Test patch generated ({len(test_patch)} chars)")
            success_count += 1
        else:
            print(f"  ✗ Failed")
            failed_count += 1

        # Rate limiting
        time.sleep(0.5)

        # Save progress every 10 instances
        if (i + 1) % 10 == 0:
            with open(output_path, 'w') as f:
                json.dump(data, f, indent=2)
            print(f"  (Progress saved)")

    # Final save
    with open(output_path, 'w') as f:
        json.dump(data, f, indent=2)

    print(f"\n{'='*50}")
    print(f"Results:")
    print(f"  Success: {success_count}")
    print(f"  Failed:  {failed_count}")
    print(f"  Total:   {len(data)}")
    print(f"\nSaved to: {output_path}")


if __name__ == '__main__':
    main()
