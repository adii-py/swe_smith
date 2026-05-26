#!/usr/bin/env python3
"""
Generate test patches using AST analysis + file content for better LLM context.

This script:
1. Fetches source files from GitHub at base_commit
2. Parses files using tree-sitter to extract AST structure
3. Analyzes the bug patch to identify changed functions/types
4. Provides rich context (AST + relevant code sections) to LLM
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

# Tree-sitter imports
from tree_sitter import Language, Parser, Node
import tree_sitter_rust


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


def init_rust_parser() -> Parser:
    """Initialize tree-sitter parser for Rust."""
    language = Language(tree_sitter_rust.language())
    parser = Parser(language)
    return parser


def extract_node_text(node: Node, source: bytes) -> str:
    """Extract text from AST node."""
    return source[node.start_byte:node.end_byte].decode('utf-8')


def extract_function_info(node: Node, source: bytes) -> Dict:
    """Extract function information from AST node."""
    info = {
        'name': None,
        'signature': None,
        'params': [],
        'return_type': None,
        'is_async': False,
        'is_public': False,
        'start_line': node.start_point[0],
        'end_line': node.end_point[0]
    }

    # Get function text
    func_text = extract_node_text(node, source)
    info['signature'] = func_text.split('{')[0].strip() if '{' in func_text else func_text.strip()

    for child in node.children:
        child_type = child.type

        if child_type == 'visibility_modifier':
            vis_text = extract_node_text(child, source)
            if 'pub' in vis_text:
                info['is_public'] = True

        elif child_type == 'function_modifiers':
            modifiers = extract_node_text(child, source)
            if 'async' in modifiers:
                info['is_async'] = True

        elif child_type == 'identifier':
            info['name'] = extract_node_text(child, source)

        elif child_type == 'parameters':
            params_text = extract_node_text(child, source)
            # Parse individual parameters
            param_list = []
            for param_node in child.children:
                if param_node.type in ['parameter', 'self_parameter']:
                    param_text = extract_node_text(param_node, source)
                    param_list.append(param_text.strip())
            info['params'] = param_list

        elif child_type == 'return_type':
            ret_text = extract_node_text(child, source)
            info['return_type'] = ret_text.replace('->', '').strip()

    return info


def extract_struct_info(node: Node, source: bytes) -> Dict:
    """Extract struct information from AST node."""
    info = {
        'name': None,
        'fields': [],
        'is_public': False,
        'start_line': node.start_point[0],
        'end_line': node.end_point[0]
    }

    for child in node.children:
        if child.type == 'visibility_modifier':
            info['is_public'] = True
        elif child.type == 'type_identifier':
            info['name'] = extract_node_text(child, source)
        elif child.type == 'field_declaration_list':
            for field in child.children:
                if field.type == 'field_declaration':
                    field_text = extract_node_text(field, source)
                    info['fields'].append(field_text.strip())

    return info


def extract_imports(root: Node, source: bytes) -> List[str]:
    """Extract import/use statements from AST."""
    imports = []

    def traverse(node: Node):
        if node.type == 'use_declaration':
            import_text = extract_node_text(node, source)
            imports.append(import_text.strip())
        for child in node.children:
            traverse(child)

    traverse(root)
    return imports


def analyze_file_ast(file_content: str) -> Dict:
    """Analyze file content and extract AST structure."""
    parser = init_rust_parser()
    source_bytes = file_content.encode('utf-8')
    tree = parser.parse(source_bytes)
    root = tree.root_node

    analysis = {
        'imports': [],
        'functions': [],
        'structs': [],
        'enums': [],
        'traits': [],
        'impl_blocks': [],
        'total_lines': len(file_content.split('\n'))
    }

    def traverse(node: Node):
        node_type = node.type

        if node_type == 'use_declaration':
            analysis['imports'].append(extract_node_text(node, source_bytes))

        elif node_type == 'function_item':
            func_info = extract_function_info(node, source_bytes)
            analysis['functions'].append(func_info)

        elif node_type == 'struct_item':
            struct_info = extract_struct_info(node, source_bytes)
            analysis['structs'].append(struct_info)

        elif node_type == 'enum_item':
            enum_name = None
            for child in node.children:
                if child.type == 'type_identifier':
                    enum_name = extract_node_text(child, source_bytes)
            if enum_name:
                analysis['enums'].append({
                    'name': enum_name,
                    'start_line': node.start_point[0],
                    'end_line': node.end_point[0]
                })

        elif node_type == 'trait_item':
            trait_name = None
            for child in node.children:
                if child.type == 'type_identifier':
                    trait_name = extract_node_text(child, source_bytes)
            if trait_name:
                analysis['traits'].append({
                    'name': trait_name,
                    'start_line': node.start_point[0],
                    'end_line': node.end_point[0]
                })

        elif node_type == 'impl_item':
            impl_text = extract_node_text(node, source_bytes).split('{')[0].strip()
            analysis['impl_blocks'].append({
                'signature': impl_text,
                'start_line': node.start_point[0],
                'end_line': node.end_point[0]
            })

        for child in node.children:
            traverse(child)

    traverse(root)
    return analysis


def analyze_patch_impact(patch_text: str, file_content: str) -> Dict:
    """Analyze which functions/types are affected by the patch."""
    impact = {
        'changed_functions': [],
        'changed_lines_range': (0, 0),
        'change_types': []  # 'added', 'removed', 'modified'
    }

    try:
        patch = PatchSet(patch_text)
        for file in patch:
            for hunk in file:
                # Track line range
                start = hunk.source_start
                end = start + hunk.source_length
                impact['changed_lines_range'] = (start, end)

                for line in hunk:
                    if line.is_added:
                        impact['change_types'].append('added')
                        # Look for function names in added lines
                        if 'fn ' in line.value:
                            match = re.search(r'fn\s+(\w+)', line.value)
                            if match:
                                impact['changed_functions'].append(match.group(1))
                    elif line.is_removed:
                        impact['change_types'].append('removed')
    except:
        pass

    impact['changed_functions'] = list(set(impact['changed_functions']))
    return impact


def get_context_around_changes(file_content: str, patch_text: str, context_lines: int = 15) -> str:
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

                context = f"// Context around line {center}:\n"
                for i in range(start, end):
                    marker = ">>> " if i == center - 1 else "    "
                    context += f"{marker}{i+1:4}: {lines[i]}\n"

                contexts.append(context)

        return '\n'.join(contexts)
    except:
        # Fallback: return first 50 lines
        return '\n'.join([f"{i+1:4}: {line}" for i, line in enumerate(lines[:50])])


def find_test_insertion_point(ast_analysis: Dict, file_content: str) -> Tuple[int, bool]:
    """
    Find the best location to insert a test module.
    Returns (line_number, is_new_module).
    """
    lines = file_content.split('\n')

    # Check if there's already a test module
    for func in ast_analysis['functions']:
        # Check for #[test] attribute by looking at lines before function
        start_line = func['start_line']
        for i in range(max(0, start_line - 5), start_line):
            if '#[cfg(test)]' in lines[i] or '#[test]' in lines[i]:
                # Find end of this test module
                return (func['end_line'], False)

    # No existing test module found - insert at end of file
    return (len(lines), True)


def format_ast_for_prompt(ast_analysis: Dict) -> str:
    """Format AST analysis for LLM prompt."""
    output = []

    output.append("=== FILE STRUCTURE ===\n")

    if ast_analysis['imports']:
        output.append("Imports:")
        for imp in ast_analysis['imports'][:10]:  # Limit to 10
            output.append(f"  {imp}")
        output.append("")

    if ast_analysis['structs']:
        output.append("Structs:")
        for s in ast_analysis['structs'][:5]:
            vis = "pub " if s['is_public'] else ""
            output.append(f"  {vis}struct {s['name']} {{...}} (lines {s['start_line']}-{s['end_line']})")
        output.append("")

    if ast_analysis['enums']:
        output.append("Enums:")
        for e in ast_analysis['enums'][:5]:
            output.append(f"  enum {e['name']} (lines {e['start_line']}-{e['end_line']})")
        output.append("")

    if ast_analysis['traits']:
        output.append("Traits:")
        for t in ast_analysis['traits'][:5]:
            output.append(f"  trait {t['name']} (lines {t['start_line']}-{t['end_line']})")
        output.append("")

    if ast_analysis['functions']:
        output.append("Functions:")
        for f in ast_analysis['functions'][:10]:
            async_str = "async " if f['is_async'] else ""
            pub_str = "pub " if f['is_public'] else ""
            params = ', '.join(f['params'][:3])  # Limit params
            ret = f" -> {f['return_type']}" if f['return_type'] else ""
            output.append(f"  {pub_str}{async_str}fn {f['name']}({params}){ret}")
        output.append("")

    return '\n'.join(output)


def create_enhanced_prompt(patch_text: str, file_path: str, crate_name: str,
                           ast_analysis: Dict, context_code: str, impact: Dict) -> str:
    """Create an enhanced prompt with AST + context for the LLM."""

    ast_summary = format_ast_for_prompt(ast_analysis)

    changed_funcs = impact.get('changed_functions', [])
    changed_funcs_str = ', '.join(changed_funcs) if changed_funcs else 'Unknown'

    prompt = f"""You are a Rust testing expert. Generate a regression test that detects the bug fixed by this patch.

## TARGET FILE
Path: `{file_path}`
Crate: `{crate_name}`

## BUG PATCH (Unified Diff)
```diff
{patch_text[:3000]}
```

## CHANGED FUNCTIONS
These functions were modified: {changed_funcs_str}

## FILE STRUCTURE (AST Analysis)
{ast_summary}

## CODE CONTEXT (Around Changes)
```rust
{context_code}
```

## YOUR TASK
Generate a complete regression test function that:

1. **Identifies the Bug**: From the patch, understand what bug was fixed (missing validation, incorrect error handling, edge case not handled, etc.)

2. **Exercises the Buggy Code Path**: Call the function(s) that were modified with inputs that trigger the bug condition

3. **Verifies the Fix**: Include assertions that:
   - FAIL when the bug is present (before the fix)
   - PASS when the bug is fixed (after the fix)

## TEST REQUIREMENTS
- Use `#[test]` attribute (or `#[tokio::test]` if async)
- Import needed types from the module with `use super::*;`
- Test function name should be descriptive: `test_<function>_handles_<scenario>`
- Create realistic test data that demonstrates the bug
- Multiple assertions are encouraged to thoroughly test the fix

## OUTPUT FORMAT
Provide ONLY the test function code (with #[test] attribute), like this:

```rust
#[test]
fn test_example_function_handles_edge_case() {{
    use super::*;

    // Setup test data that triggers the bug
    let input = ...;

    // Call the function that was modified
    let result = function_name(input);

    // Assert correct behavior (fails with bug, passes with fix)
    assert!(result.is_ok());
}}
```
"""
    return prompt


def extract_test_from_response(response: str) -> Optional[str]:
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
        # Look for #[test] attribute
        match = re.search(r'(\#\[(?:test|tokio::test)\].*?\n?.*?\{.*?\n\})', response, re.DOTALL)
        if match:
            return match.group(1).strip()
        return response.strip()


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
        # Append to existing test module
        formatted_test = '\n'.join(['    ' + line if line.strip() else line
                                     for line in test_code.split('\n')])

    test_lines = formatted_test.split('\n')

    # Calculate context
    context_start = max(0, insert_line - 3)
    context_end = min(len(lines), insert_line + 3)

    before_context = lines[context_start:insert_line]
    after_context = lines[insert_line:context_end]

    # Line numbers for diff header
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

    # Analyze with AST
    print(f"  Analyzing AST...")
    try:
        ast_analysis = analyze_file_ast(file_content)
    except Exception as e:
        print(f"  AST analysis failed: {e}")
        ast_analysis = {'imports': [], 'functions': [], 'structs': [], 'enums': [],
                       'traits': [], 'impl_blocks': [], 'total_lines': len(file_content.split('\n'))}

    # Analyze patch impact
    impact = analyze_patch_impact(patch_text, file_content)

    # Get context around changes
    context_code = get_context_around_changes(file_content, patch_text)

    # Create enhanced prompt
    prompt = create_enhanced_prompt(patch_text, file_path, crate_name,
                                    ast_analysis, context_code, impact)

    # Call LLM
    print(f"  Calling LLM...")
    try:
        response = call_llm(prompt)
    except Exception as e:
        print(f"  LLM call failed: {e}")
        return None

    # Extract test code
    test_code = extract_test_from_response(response)
    if not test_code or ('#[test]' not in test_code and '#[tokio::test]' not in test_code):
        print(f"  Invalid test code received")
        return None

    print(f"  Test code extracted ({len(test_code)} chars)")

    # Find insertion point
    insert_line, is_new_module = find_test_insertion_point(ast_analysis, file_content)

    # Create proper patch
    try:
        test_patch = create_proper_test_patch(file_path, file_content, test_code,
                                              insert_line, is_new_module)
        return test_patch
    except Exception as e:
        print(f"  Failed to create patch: {e}")
        return None


def main():
    """Generate AST-enhanced test patches."""

    dataset_path = Path('logs/bug_gen/juspay__hyperswitch.fece9bc3/pr_mirror/recovered_dataset_with_test_patches.json')
    output_path = Path('logs/bug_gen/juspay__hyperswitch.fece9bc3/pr_mirror/recovered_dataset_ast_enhanced_tests.json')

    print(f"Loading dataset: {dataset_path}")
    with open(dataset_path) as f:
        data = json.load(f)

    print(f"Generating AST-enhanced tests for {len(data)} instances...\n")

    success_count = 0
    fail_count = 0
    skip_count = 0

    for i, inst in enumerate(data):
        instance_id = inst.get('instance_id', '')

        # Skip if already has a good test_patch from our fix
        if inst.get('_fixed_format') or (inst.get('test_patch') and i < 48):
            print(f"[{i+1}/{len(data)}] {instance_id} - Skipping (already has test)")
            skip_count += 1
            continue

        print(f"[{i+1}/{len(data)}] {instance_id}")

        try:
            test_patch = process_instance(inst)
            if test_patch:
                inst['test_patch'] = test_patch
                inst['_ast_enhanced'] = True
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
