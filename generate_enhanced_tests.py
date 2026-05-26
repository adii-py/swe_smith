#!/usr/bin/env python3
"""
Generate enhanced test patches using file content analysis for better LLM context.

This script:
1. Fetches source files from GitHub at base_commit
2. Extracts structural information using regex (functions, structs, imports)
3. Analyzes the bug patch to identify changed functions/types
4. Provides rich context (structure + relevant code sections) to LLM
5. Generates properly formatted unified diff patches
"""

import json
import os
import re
import time
from pathlib import Path
from typing import Dict, List, Tuple, Optional
from unidiff import PatchSet
import requests


def call_llm(prompt: str, model: str = "private-large", max_retries: int = 3) -> str:
    """Call LLM with prompt using LiteLLM proxy."""
    lite_llm_url = os.getenv("LITE_LLM_URL", "http://localhost:4000")
    api_key = os.getenv("ANTHROPIC_API_KEY") or os.getenv("LITE_LLM_API_KEY")

    if not api_key:
        raise ValueError("No API key found")

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }

    for attempt in range(max_retries):
        try:
            response = requests.post(
                f"{lite_llm_url}/chat/completions",
                headers=headers,
                json={
                    "model": model,
                    "max_tokens": 4000,
                    "temperature": 0.2,
                    "messages": [{"role": "user", "content": prompt}]
                },
                timeout=120
            )
            response.raise_for_status()
            return response.json()["choices"][0]["message"]["content"]
        except Exception as e:
            print(f"  LLM call failed (attempt {attempt+1}/{max_retries}): {e}")
            if attempt < max_retries - 1:
                time.sleep(2)
            else:
                raise


def fetch_file_from_github(repo: str, commit: str, file_path: str) -> Optional[str]:
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
            print(f"  Failed to fetch {url}: {response.status_code}")
            return None
    except Exception as e:
        print(f"  Error fetching file: {e}")
        return None


def analyze_file_structure(file_content: str) -> Dict:
    """Analyze file content and extract structural information using regex."""
    lines = file_content.split('\n')

    analysis = {
        'imports': [],
        'functions': [],
        'structs': [],
        'enums': [],
        'traits': [],
        'impl_blocks': [],
        'test_modules': [],
        'total_lines': len(lines)
    }

    in_test_module = False
    test_module_start = 0
    brace_depth = 0

    for i, line in enumerate(lines):
        stripped = line.strip()

        # Track brace depth
        brace_depth += stripped.count('{') - stripped.count('}')

        # Imports
        if stripped.startswith('use ') or stripped.startswith('pub use '):
            analysis['imports'].append((i, stripped))

        # Test modules
        if stripped == '#[cfg(test)]':
            in_test_module = True
            test_module_start = i

        if in_test_module and stripped.startswith('mod ') and '{' in stripped:
            analysis['test_modules'].append({
                'start_line': test_module_start,
                'name_line': i,
                'name': re.search(r'mod\s+(\w+)', stripped).group(1) if re.search(r'mod\s+(\w+)', stripped) else 'tests'
            })
            in_test_module = False

        # Functions (fn keyword at start of line or after visibility modifier)
        func_match = re.match(r'^(?:pub\s+)?(?:async\s+)?(?:unsafe\s+)?fn\s+(\w+)\s*\(([^)]*)\)(?:\s*->\s*([^\{{]+))?', stripped)
        if func_match:
            func_info = {
                'name': func_match.group(1),
                'params': func_match.group(2).strip() if func_match.group(2) else '',
                'return_type': func_match.group(3).strip() if func_match.group(3) else None,
                'is_async': 'async' in stripped,
                'is_public': stripped.startswith('pub '),
                'is_unsafe': 'unsafe' in stripped,
                'line': i,
                'signature': stripped.split('{')[0].strip() if '{' in stripped else stripped
            }
            analysis['functions'].append(func_info)

        # Structs
        struct_match = re.match(r'^(?:pub\s+)?struct\s+(\w+)', stripped)
        if struct_match:
            analysis['structs'].append({
                'name': struct_match.group(1),
                'is_public': stripped.startswith('pub '),
                'line': i
            })

        # Enums
        enum_match = re.match(r'^(?:pub\s+)?enum\s+(\w+)', stripped)
        if enum_match:
            analysis['enums'].append({
                'name': enum_match.group(1),
                'is_public': stripped.startswith('pub '),
                'line': i
            })

        # Traits
        trait_match = re.match(r'^(?:pub\s+)?trait\s+(\w+)', stripped)
        if trait_match:
            analysis['traits'].append({
                'name': trait_match.group(1),
                'is_public': stripped.startswith('pub '),
                'line': i
            })

        # Impl blocks
        impl_match = re.match(r'^impl(?:<[^>]+>)?(?:\s+(.+))?\s*(?:for\s+(.+))?', stripped)
        if impl_match:
            impl_text = stripped.split('{')[0].strip() if '{' in stripped else stripped
            analysis['impl_blocks'].append({
                'text': impl_text,
                'line': i
            })

    return analysis


def analyze_patch_impact(patch_text: str) -> Dict:
    """Analyze which functions/types are affected by the patch."""
    impact = {
        'changed_functions': [],
        'added_functions': [],
        'removed_functions': [],
        'changed_lines_range': (0, 0),
        'change_types': []
    }

    try:
        patch = PatchSet(patch_text)
        for file in patch:
            for hunk in file:
                start = hunk.source_start
                end = start + hunk.source_length
                impact['changed_lines_range'] = (start, end)

                for line in hunk:
                    line_text = line.value.strip()

                    if line.is_added:
                        impact['change_types'].append('added')
                        # Look for new function definitions
                        new_func = re.search(r'^\+?(?:pub\s+)?(?:async\s+)?fn\s+(\w+)', line_text)
                        if new_func:
                            impact['added_functions'].append(new_func.group(1))

                    elif line.is_removed:
                        impact['change_types'].append('removed')
                        # Look for removed functions
                        rem_func = re.search(r'^-?(?:pub\s+)?(?:async\s+)?fn\s+(\w+)', line_text)
                        if rem_func:
                            impact['removed_functions'].append(rem_func.group(1))

                    # Look for modified function calls
                    func_call = re.search(r'\b(\w+)\s*\(', line_text)
                    if func_call:
                        name = func_call.group(1)
                        if name not in ['if', 'while', 'for', 'match', 'Some', 'Ok', 'Err']:
                            impact['changed_functions'].append(name)

    except Exception as e:
        print(f"  Warning: Could not parse patch: {e}")

    # Deduplicate
    impact['changed_functions'] = list(set(impact['changed_functions']))
    impact['added_functions'] = list(set(impact['added_functions']))
    impact['removed_functions'] = list(set(impact['removed_functions']))

    return impact


def get_context_around_changes(file_content: str, patch_text: str, context_lines: int = 20) -> str:
    """Extract code context around the changed lines."""
    lines = file_content.split('\n')

    try:
        patch = PatchSet(patch_text)
        contexts = []

        for file in patch:
            for hunk in file:
                center = hunk.source_start
                start = max(0, center - context_lines)
                end = min(len(lines), center + context_lines)

                context = f"// Lines {start+1}-{end+1} (changes around line {center}):\n"
                for i in range(start, end):
                    marker = ">>> " if i == center - 1 else "    "
                    context += f"{marker}{i+1:4}: {lines[i]}\n"

                contexts.append(context)

        return '\n'.join(contexts[:2])  # Limit to first 2 hunks
    except:
        # Fallback: return first 50 lines
        return '\n'.join([f"{i+1:4}: {line}" for i, line in enumerate(lines[:50])])


def find_test_insertion_point(analysis: Dict, file_content: str) -> Tuple[int, bool]:
    """
    Find the best location to insert a test module.
    Returns (line_number, is_new_module).
    """
    lines = file_content.split('\n')

    # Check if there's already a test module
    if analysis['test_modules']:
        # Insert at the end of the last test module
        last_test = analysis['test_modules'][-1]
        # Find closing brace of the module
        brace_depth = 0
        for i in range(last_test['name_line'], len(lines)):
            brace_depth += lines[i].count('{') - lines[i].count('}')
            if brace_depth == 0 and '{' in ''.join(lines[last_test['name_line']:i+1]):
                return (i, False)

    # No existing test module - insert at end of file
    # Find the last non-empty line
    last_non_empty = len(lines) - 1
    while last_non_empty > 0 and not lines[last_non_empty].strip():
        last_non_empty -= 1

    return (last_non_empty + 1, True)


def format_structure_for_prompt(analysis: Dict) -> str:
    """Format structural analysis for LLM prompt."""
    output = []

    output.append("=== FILE STRUCTURE ===\n")

    if analysis['imports']:
        output.append("Key Imports:")
        for line_num, imp in analysis['imports'][:8]:
            output.append(f"  Line {line_num+1}: {imp[:60]}{'...' if len(imp) > 60 else ''}")
        output.append("")

    if analysis['structs']:
        output.append("Structs:")
        for s in analysis['structs'][:6]:
            vis = "pub " if s['is_public'] else ""
            output.append(f"  Line {s['line']+1}: {vis}struct {s['name']}")
        output.append("")

    if analysis['enums']:
        output.append("Enums:")
        for e in analysis['enums'][:4]:
            vis = "pub " if e['is_public'] else ""
            output.append(f"  Line {e['line']+1}: {vis}enum {e['name']}")
        output.append("")

    if analysis['traits']:
        output.append("Traits:")
        for t in analysis['traits'][:4]:
            vis = "pub " if t['is_public'] else ""
            output.append(f"  Line {t['line']+1}: {vis}trait {t['name']}")
        output.append("")

    if analysis['functions']:
        output.append("Functions:")
        for f in analysis['functions'][:12]:
            async_str = "async " if f['is_async'] else ""
            pub_str = "pub " if f['is_public'] else ""
            ret_str = f" -> {f['return_type']}" if f['return_type'] else ""
            params = f['params'][:40] + '...' if len(f['params']) > 40 else f['params']
            output.append(f"  Line {f['line']+1}: {pub_str}{async_str}fn {f['name']}({params}){ret_str}")
        output.append("")

    if analysis['test_modules']:
        output.append("Existing Test Modules:")
        for tm in analysis['test_modules']:
            output.append(f"  Line {tm['start_line']+1}: mod {tm['name']}")
        output.append("")

    return '\n'.join(output)


def create_enhanced_prompt(patch_text: str, file_path: str, crate_name: str,
                           analysis: Dict, context_code: str, impact: Dict) -> str:
    """Create an enhanced prompt with structure + context for the LLM."""

    structure_summary = format_structure_for_prompt(analysis)

    changed_funcs = impact.get('changed_functions', [])
    added_funcs = impact.get('added_functions', [])
    removed_funcs = impact.get('removed_functions', [])

    changed_funcs_str = ', '.join(changed_funcs[:8]) if changed_funcs else 'Unknown'

    # Get affected line range
    line_range = impact.get('changed_lines_range', (0, 0))

    prompt = f"""You are a Rust testing expert. Generate a regression test that detects the bug fixed by this patch.

## TARGET FILE
Path: `{file_path}`
Crate: `{crate_name}`
Total Lines: {analysis['total_lines']}
Changed Lines Range: {line_range[0]}-{line_range[1]}

## BUG PATCH (Unified Diff)
```diff
{patch_text[:2500]}
```

## IMPACT ANALYSIS
Functions referenced in changes: {changed_funcs_str}
Added functions: {', '.join(added_funcs[:5]) if added_funcs else 'None'}
Removed functions: {', '.join(removed_funcs[:5]) if removed_funcs else 'None'}

{structure_summary}
## CODE CONTEXT (Around Changes)
```rust
{context_code}
```

## YOUR TASK
Generate a COMPLETE regression test that:

1. **Identifies the Bug**: From the patch, understand what was wrong:
   - Missing validation?
   - Incorrect error handling?
   - Edge case not handled?
   - Logic error?

2. **Tests the Buggy Behavior**:
   - Call the function(s) that were modified
   - Pass inputs that trigger the bug condition
   - Test should FAIL with the buggy code

3. **Verifies the Fix**:
   - Assertions should PASS after the fix is applied
   - Test the corrected behavior

## TEST REQUIREMENTS
- Start with `#[test]` (or `#[tokio::test]` if async)
- Include `use super::*;` to access module items
- Name format: `test_<function>_handles_<scenario>` or `test_<bug_description>`
- Add comments explaining what bug is being tested
- Include setup code with realistic test data

## EXAMPLE OUTPUT FORMAT
```rust
#[test]
fn test_function_handles_invalid_input() {{
    use super::*;

    // Arrange: Set up data that triggers the bug
    let input = create_problematic_input();

    // Act: Call the function that was fixed
    let result = function_to_test(input);

    // Assert: Verify correct behavior (would fail before fix)
    assert!(result.is_err(), "Should reject invalid input");
    assert_eq!(result.unwrap_err().to_string(), "expected error");
}}
```

Provide ONLY the test function code, wrapped in ```rust blocks.
"""
    return prompt


def extract_test_from_response(response: str) -> Optional[str]:
    """Extract Rust test code from LLM response."""
    # Look for code blocks
    if "```rust" in response:
        start = response.find("```rust") + 7
        end = response.find("```", start)
        if end > start:
            return response[start:end].strip()
    elif "```" in response:
        start = response.find("```") + 3
        end = response.find("```", start)
        if end > start:
            return response[start:end].strip()

    # Look for #[test] attribute
    match = re.search(r'(\#\[test\].*?\n?.*?fn\s+\w+.*?\{.*?\n\})', response, re.DOTALL)
    if match:
        return match.group(1).strip()

    match = re.search(r'(\#\[tokio::test\].*?\n?.*?fn\s+\w+.*?\{.*?\n\})', response, re.DOTALL)
    if match:
        return match.group(1).strip()

    return response.strip() if response.strip() else None


def create_proper_test_patch(file_path: str, file_content: str, test_code: str,
                              insert_line: int, is_new_module: bool) -> str:
    """Create a properly formatted unified diff patch."""
    lines = file_content.split('\n')

    # Format test code
    if is_new_module:
        # Create new test module at end of file
        formatted_test = f"""
#[cfg(test)]
mod regression_tests {{
    use super::*;

{chr(10).join('    ' + line if line.strip() else line for line in test_code.split(chr(10)))}
}}
"""
    else:
        # Append to existing test module with indentation
        formatted_test = '\n'.join(['    ' + line if line.strip() else line
                                     for line in test_code.split('\n')])

    test_lines = formatted_test.split('\n')

    # Calculate context (3 lines before and after)
    context_start = max(0, insert_line - 3)
    context_end = min(len(lines), insert_line + 3)

    before_context = lines[context_start:insert_line]
    after_context = lines[insert_line:context_end]

    # Line numbers for diff header (1-indexed)
    old_start = context_start + 1
    old_count = len(before_context) + len(after_context)
    new_start = context_start + 1
    new_count = len(before_context) + len(test_lines) + len(after_context)

    # Build diff
    diff_parts = [
        f'diff --git a/{file_path} b/{file_path}',
        f'--- a/{file_path}',
        f'+++ b/{file_path}',
        f'@@ -{old_start},{old_count} +{new_start},{new_count} @@'
    ]

    for line in before_context:
        diff_parts.append(' ' + line)

    for line in test_lines:
        diff_parts.append('+' + line)

    for line in after_context:
        diff_parts.append(' ' + line)

    return '\n'.join(diff_parts) + '\n'


def process_instance(instance: dict) -> Optional[str]:
    """Process a single instance and generate test patch."""
    instance_id = instance.get('instance_id', '')
    repo = instance.get('repo', '')
    base_commit = instance.get('base_commit', '')
    patch_text = instance.get('patch', '')

    # Extract file path
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

    print(f"  Fetching source: {file_path}")
    file_content = fetch_file_from_github(repo, base_commit, file_path)
    if not file_content:
        print(f"  Failed to fetch source")
        return None

    # Analyze file structure
    print(f"  Analyzing structure...")
    try:
        analysis = analyze_file_structure(file_content)
    except Exception as e:
        print(f"  Structure analysis failed: {e}")
        analysis = {
            'imports': [], 'functions': [], 'structs': [], 'enums': [],
            'traits': [], 'impl_blocks': [], 'test_modules': [],
            'total_lines': len(file_content.split('\n'))
        }

    # Analyze patch impact
    impact = analyze_patch_impact(patch_text)

    # Get context around changes
    context_code = get_context_around_changes(file_content, patch_text)

    # Create enhanced prompt
    prompt = create_enhanced_prompt(patch_text, file_path, crate_name,
                                    analysis, context_code, impact)

    # Call LLM
    print(f"  Calling LLM...")
    try:
        response = call_llm(prompt)
    except Exception as e:
        print(f"  LLM call failed: {e}")
        return None

    # Extract test code
    test_code = extract_test_from_response(response)
    if not test_code:
        print(f"  No valid test code extracted")
        return None

    if '#[test]' not in test_code and '#[tokio::test]' not in test_code:
        print(f"  Warning: Missing #[test] attribute")
        # Try to add it
        if 'fn ' in test_code:
            test_code = '#[test]\n' + test_code
        else:
            return None

    print(f"  Test code extracted ({len(test_code)} chars)")

    # Find insertion point
    insert_line, is_new_module = find_test_insertion_point(analysis, file_content)

    # Create proper patch
    try:
        test_patch = create_proper_test_patch(file_path, file_content, test_code,
                                              insert_line, is_new_module)
        return test_patch
    except Exception as e:
        print(f"  Failed to create patch: {e}")
        return None


def main():
    """Generate enhanced test patches."""

    dataset_path = Path('logs/bug_gen/juspay__hyperswitch.fece9bc3/pr_mirror/recovered_dataset_with_test_patches.json')
    output_path = Path('logs/bug_gen/juspay__hyperswitch.fece9bc3/pr_mirror/recovered_dataset_enhanced_tests.json')

    print(f"Loading dataset: {dataset_path}")
    with open(dataset_path) as f:
        data = json.load(f)

    print(f"Generating enhanced tests for {len(data)} instances...\n")

    success_count = 0
    fail_count = 0
    skip_count = 0

    for i, inst in enumerate(data):
        instance_id = inst.get('instance_id', '')

        # Skip if already has a good test_patch
        if inst.get('_fixed_format') or (inst.get('test_patch') and i < 48):
            print(f"[{i+1}/{len(data)}] {instance_id} - Skipping (already processed)")
            skip_count += 1
            continue

        print(f"[{i+1}/{len(data)}] {instance_id}")

        try:
            test_patch = process_instance(inst)
            if test_patch:
                inst['test_patch'] = test_patch
                inst['_enhanced'] = True
                print(f"  ✓ Test patch generated")
                success_count += 1
            else:
                print(f"  ✗ Failed")
                fail_count += 1
        except Exception as e:
            print(f"  ✗ Error: {e}")
            fail_count += 1

        # Rate limiting
        time.sleep(0.5)

        # Save progress every 5
        if (i + 1) % 5 == 0:
            with open(output_path, 'w') as f:
                json.dump(data, f, indent=2)
            print(f"  (Progress saved)")

    # Final save
    with open(output_path, 'w') as f:
        json.dump(data, f, indent=2)

    print(f"\n{'='*50}")
    print(f"Results:")
    print(f"  Success:  {success_count}")
    print(f"  Failed:   {fail_count}")
    print(f"  Skipped:  {skip_count}")
    print(f"  Total:    {len(data)}")
    print(f"\nSaved to: {output_path}")


if __name__ == '__main__':
    main()
