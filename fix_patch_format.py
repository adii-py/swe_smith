#!/usr/bin/env python3
"""
Fix patch format for existing LLM-generated test patches.

This script:
1. Reads the LLM-generated test patches
2. Extracts valid test code (where possible)
3. Fetches source files from GitHub
4. Regenerates proper unified diffs with correct context
"""

import json
import re
from pathlib import Path
from unidiff import PatchSet
import requests
import time


def fetch_file_from_github(repo: str, commit: str, file_path: str) -> str:
    """Fetch file content from GitHub at specific commit."""
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
            return None
    except Exception as e:
        print(f"  Error fetching file: {e}")
        return None


def find_test_insertion_point(file_content: str) -> tuple:
    """
    Find the best location to insert a test module.
    Returns (line_number, is_new_module).
    """
    lines = file_content.split('\n')

    # Look for existing #[cfg(test)] module
    in_test_module = False
    brace_count = 0

    for i, line in enumerate(lines):
        stripped = line.strip()

        if stripped == '#[cfg(test)]':
            in_test_module = True
            continue

        if in_test_module:
            brace_count += stripped.count('{') - stripped.count('}')
            if brace_count == 0 and '}' in line and i > 0:
                # Found end of test module - insert before closing brace
                return (i, False)

    # No existing test module - add at end of file
    last_non_empty = len(lines) - 1
    while last_non_empty > 0 and not lines[last_non_empty].strip():
        last_non_empty -= 1

    return (last_non_empty + 1, True)


def extract_valid_test_code(test_patch: str) -> str:
    """Extract valid Rust test code from malformed patch."""
    if not test_patch:
        return None

    # Look for #[cfg(test)] module in the patch content (after the header)
    lines = test_patch.split('\n')
    content_started = False
    content_lines = []

    for line in lines:
        # Skip diff headers
        if line.startswith('diff --git') or line.startswith('--- ') or line.startswith('+++ '):
            continue
        if line.startswith('@@'):
            content_started = True
            continue
        if content_started:
            # Remove leading + or space (context/patch markers)
            if line.startswith('+') or line.startswith(' '):
                content_lines.append(line[1:])
            elif line.startswith('-'):
                continue  # Skip removed lines
            else:
                content_lines.append(line)

    code = '\n'.join(content_lines).strip()

    # Validate: must have #[test] attribute and look like valid test code
    if '#[test]' not in code and '#[tokio::test]' not in code:
        return None

    # Must have a fn definition
    if not re.search(r'\bfn\s+\w+', code):
        return None

    # Check if it looks like a complete test function (has opening and closing braces)
    open_braces = code.count('{')
    close_braces = code.count('}')
    if open_braces < 2 or close_braces < 2:  # Need at least module + function braces
        return None

    return code


def create_proper_test_patch(file_path: str, file_content: str, test_code: str) -> str:
    """Create a properly formatted unified diff patch."""
    lines = file_content.split('\n')
    insert_line, is_new_module = find_test_insertion_point(file_content)

    # Adjust test code indentation if appending to existing module
    if not is_new_module:
        # Add indentation to test code (it's going inside an existing module)
        test_lines = test_code.split('\n')
        indented_lines = []
        for line in test_lines:
            if line.strip():
                indented_lines.append('    ' + line)
            else:
                indented_lines.append(line)
        test_code = '\n'.join(indented_lines)

    # Prepare the new content
    new_lines = test_code.split('\n')

    # Calculate context (3 lines before and after)
    context_start = max(0, insert_line - 3)
    context_end = min(len(lines), insert_line + 3)

    before_context = lines[context_start:insert_line]
    after_context = lines[insert_line:context_end]

    # Calculate line numbers for diff header (1-indexed)
    old_line_start = context_start + 1
    old_line_count = len(before_context) + len(after_context)
    new_line_start = context_start + 1
    new_line_count = len(before_context) + len(new_lines) + len(after_context)

    # Build the diff
    diff_parts = [
        f'diff --git a/{file_path} b/{file_path}',
        f'--- a/{file_path}',
        f'+++ b/{file_path}',
        f'@@ -{old_line_start},{old_line_count} +{new_line_start},{new_line_count} @@'
    ]

    # Context before
    for line in before_context:
        diff_parts.append(' ' + line)

    # New test lines (marked with +)
    for line in new_lines:
        diff_parts.append('+' + line)

    # Context after
    for line in after_context:
        diff_parts.append(' ' + line)

    return '\n'.join(diff_parts) + '\n'


def process_instance(inst: dict) -> str:
    """Process a single instance and return proper test patch."""
    instance_id = inst.get('instance_id', '')
    repo = inst.get('repo', '')
    base_commit = inst.get('base_commit', '')
    patch_text = inst.get('patch', '')

    # Get file path from patch
    file_path = None
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
        print(f"  No valid file path")
        return None

    # Extract existing test code
    existing_test_patch = inst.get('test_patch', '')
    test_code = extract_valid_test_code(existing_test_patch)

    if not test_code:
        print(f"  No valid test code in existing patch")
        return None

    print(f"  Fetching {file_path}...")
    file_content = fetch_file_from_github(repo, base_commit, file_path)
    if not file_content:
        print(f"  Failed to fetch source")
        return None

    try:
        proper_patch = create_proper_test_patch(file_path, file_content, test_code)
        return proper_patch
    except Exception as e:
        print(f"  Error creating patch: {e}")
        return None


def main():
    """Fix patch format for all instances with LLM tests."""

    input_path = Path('logs/bug_gen/juspay__hyperswitch.fece9bc3/pr_mirror/recovered_dataset_llm_tests.json')
    output_path = Path('logs/bug_gen/juspay__hyperswitch.fece9bc3/pr_mirror/recovered_dataset_fixed_format.json')

    print(f"Reading from: {input_path}")
    with open(input_path) as f:
        data = json.load(f)

    print(f"Processing {len(data)} instances...\n")

    fixed_count = 0
    skipped_count = 0
    error_count = 0

    for i, inst in enumerate(data):
        instance_id = inst.get('instance_id', '')
        has_test = bool(inst.get('test_patch'))

        print(f"[{i+1}/{len(data)}] {instance_id} - {'Has test' if has_test else 'No test'}")

        if not has_test:
            skipped_count += 1
            continue

        try:
            new_patch = process_instance(inst)
            if new_patch:
                inst['test_patch'] = new_patch
                inst['_fixed_format'] = True
                print(f"  ✓ Fixed patch format")
                fixed_count += 1
            else:
                print(f"  ✗ Could not fix")
                error_count += 1
        except Exception as e:
            print(f"  ✗ Error: {e}")
            error_count += 1

        # Small delay to be nice to GitHub
        time.sleep(0.3)

        # Save progress every 10
        if (i + 1) % 10 == 0:
            with open(output_path, 'w') as f:
                json.dump(data, f, indent=2)
            print(f"  (saved progress)")

    # Final save
    with open(output_path, 'w') as f:
        json.dump(data, f, indent=2)

    print(f"\n{'='*50}")
    print(f"Results:")
    print(f"  Fixed:   {fixed_count}")
    print(f"  Skipped: {skipped_count}")
    print(f"  Errors:  {error_count}")
    print(f"\nSaved to: {output_path}")


if __name__ == '__main__':
    main()
