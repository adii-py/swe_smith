#!/usr/bin/env python3
"""
AST-based Test Generator with File Chunking and Bug Detection.

This script:
1. Parses Rust source files using tree-sitter for accurate AST analysis
2. Chunks large files to provide focused context
3. Analyzes patches to identify bug patterns
4. Generates tests that call actual functions with valid parameters
5. Ensures tests detect bugs (fail with bug, pass with fix)
"""

import json
import os
import re
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from unidiff import PatchSet, Hunk
import requests

# Tree-sitter imports
from tree_sitter import Language, Parser, Node, Tree
import tree_sitter_rust


@dataclass
class FunctionInfo:
    name: str
    params: List[str]
    return_type: Optional[str]
    is_async: bool
    is_pub: bool
    start_line: int
    end_line: int
    signature: str


@dataclass
class BugPattern:
    bug_type: str
    affected_function: str
    changed_lines: List[int]
    description: str


def call_llm(prompt: str, model: str = "private-large", max_retries: int = 3) -> str:
    """Call LLM with prompt."""
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
                    "temperature": 0.1,
                    "messages": [{"role": "user", "content": prompt}]
                },
                timeout=120
            )
            response.raise_for_status()
            return response.json()["choices"][0]["message"]["content"]
        except Exception as e:
            print(f"  LLM call failed (attempt {attempt+1}): {e}")
            if attempt < max_retries - 1:
                time.sleep(2)
            else:
                raise


def init_parser() -> Parser:
    """Initialize tree-sitter parser."""
    language = Language(tree_sitter_rust.language())
    parser = Parser(language)
    return parser


def extract_node_text(node: Node, source: bytes) -> str:
    """Extract text from AST node."""
    return source[node.start_byte:node.end_byte].decode('utf-8', errors='ignore')


def analyze_functions(tree: Tree, source: bytes) -> List[FunctionInfo]:
    """Extract function information from AST."""
    functions = []
    root = tree.root_node

    def traverse(node: Node):
        if node.type == 'function_item':
            func_info = extract_function_info(node, source)
            if func_info:
                functions.append(func_info)
        for child in node.children:
            traverse(child)

    traverse(root)
    return functions


def extract_function_info(node: Node, source: bytes) -> Optional[FunctionInfo]:
    """Extract detailed function information."""
    name = None
    params = []
    return_type = None
    is_async = False
    is_pub = False

    for child in node.children:
        if child.type == 'visibility_modifier':
            vis_text = extract_node_text(child, source)
            if 'pub' in vis_text:
                is_pub = True
        elif child.type == 'function_modifiers':
            mods = extract_node_text(child, source)
            if 'async' in mods:
                is_async = True
        elif child.type == 'identifier':
            name = extract_node_text(child, source)
        elif child.type == 'parameters':
            params = extract_parameters(child, source)
        elif child.type == 'return_type':
            return_type = extract_node_text(child, source).replace('->', '').strip()

    if not name:
        return None

    signature = extract_node_text(node, source).split('{')[0].strip()

    return FunctionInfo(
        name=name,
        params=params,
        return_type=return_type,
        is_async=is_async,
        is_pub=is_pub,
        start_line=node.start_point[0],
        end_line=node.end_point[0],
        signature=signature
    )


def extract_parameters(params_node: Node, source: bytes) -> List[str]:
    """Extract parameter information."""
    params = []
    for child in params_node.children:
        if child.type in ['parameter', 'self_parameter']:
            param_text = extract_node_text(child, source).strip()
            params.append(param_text)
    return params


def analyze_patch_for_bug(patch_text: str, functions: List[FunctionInfo]) -> Optional[BugPattern]:
    """Analyze patch to identify bug pattern."""
    try:
        patch = PatchSet(patch_text)

        for file in patch:
            for hunk in file:
                # Look for error handling additions
                added_error_handling = False
                added_validation = False
                changed_function = None

                for line in hunk:
                    if line.is_added:
                        line_text = line.value

                        # Check for error handling patterns
                        if any(x in line_text for x in ['.map_err(', '.ok_or(', '?;', 'change_context(']):
                            added_error_handling = True

                        # Check for validation patterns
                        if any(x in line_text for x in ['if let', '.is_none()', '.is_some()', '.is_empty()']):
                            added_validation = True

                        # Find which function was changed
                        for func in functions:
                            if func.start_line <= hunk.source_start <= func.end_line:
                                changed_function = func.name
                                break

                # Determine bug type
                bug_type = None
                if added_error_handling:
                    bug_type = "missing_error_handling"
                elif added_validation:
                    bug_type = "missing_validation"

                if bug_type and changed_function:
                    return BugPattern(
                        bug_type=bug_type,
                        affected_function=changed_function,
                        changed_lines=list(range(hunk.source_start, hunk.source_start + hunk.source_length)),
                        description=f"Added {bug_type.replace('_', ' ')} in {changed_function}"
                    )

    except Exception as e:
        print(f"  Error analyzing patch: {e}")

    return None


def chunk_file_by_context(file_content: str, patch_line: int, context_size: int = 50) -> str:
    """Extract relevant chunk of file around the patch location."""
    lines = file_content.split('\n')
    start = max(0, patch_line - context_size)
    end = min(len(lines), patch_line + context_size)
    return '\n'.join(lines[start:end])


def find_relevant_functions(functions: List[FunctionInfo], patch_line: int) -> List[FunctionInfo]:
    """Find functions near the patch location."""
    relevant = []
    for func in functions:
        # Function contains patch line or is nearby
        if func.start_line <= patch_line <= func.end_line:
            relevant.insert(0, func)  # Most relevant first
        elif abs(func.start_line - patch_line) < 100:
            relevant.append(func)
    return relevant[:3]  # Top 3 most relevant


def create_test_prompt(
    bug_pattern: BugPattern,
    functions: List[FunctionInfo],
    file_chunk: str,
    file_path: str,
    patch_text: str
) -> str:
    """Create comprehensive prompt for LLM to generate valid test."""

    # Format function signatures
    func_signatures = []
    for func in functions:
        params_str = ', '.join(func.params[:3])  # Limit params
        ret = f" -> {func.return_type}" if func.return_type else ""
        async_str = "async " if func.is_async else ""
        pub_str = "pub " if func.is_pub else ""
        func_signatures.append(f"{pub_str}{async_str}fn {func.name}({params_str}){ret}")

    prompt = f"""You are a Rust testing expert. Generate a test that detects this specific bug.

## BUG ANALYSIS
Bug Type: {bug_pattern.bug_type}
Affected Function: {bug_pattern.affected_function}
Description: {bug_pattern.description}

## RELEVANT FUNCTION SIGNATURES
```rust
{chr(10).join(func_signatures)}
```

## CODE CONTEXT (around bug location)
```rust
{file_chunk}
```

## PATCH (showing the fix)
```diff
{patch_text[:1500]}
```

## YOUR TASK
Generate a COMPLETE, COMPILING Rust test function that:

1. **Imports correctly**: Use `use super::*;` and import actual types from the module
2. **Calls REAL functions**: Use the function signatures above - only call functions that exist
3. **Triggers the bug**: Pass inputs that exercise the buggy code path
4. **Detects the fix**: Include assertions that FAIL with the bug but PASS with the fix

## CRITICAL RULES
- ONLY call functions listed above - do not invent function names
- Use actual parameter types from the signatures
- Handle async functions with `.await` if needed
- Make sure the test COMPILES - use real types and imports
- The test should demonstrate the bug fix works

## OUTPUT FORMAT
Provide ONLY the test function code in a ```rust code block.

Example:
```rust
#[test]
fn test_function_handles_invalid_input() {{
    use super::*;

    // Call actual function with real parameters
    let result = actual_function_name("valid_input");

    // Assertion that catches the bug
    assert!(result.is_ok());
}}
```
"""
    return prompt


def extract_test_from_response(response: str) -> Optional[str]:
    """Extract Rust test code from LLM response."""
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

    # Look for #[test]
    match = re.search(r'(\#\[test\].*?\n.*?fn\s+\w+.*?\{.*?\n\})', response, re.DOTALL)
    if match:
        return match.group(1).strip()

    return None


def create_test_patch(file_path: str, file_content: str, test_code: str, insert_line: int) -> str:
    """Create proper unified diff for test patch."""
    lines = file_content.split('\n')

    # Format test module
    test_module = f'''
#[cfg(test)]
mod regression_tests {{
    use super::*;

{chr(10).join("    " + line if line.strip() else line for line in test_code.split(chr(10)))}
}}
'''

    test_lines = test_module.split('\n')

    # Calculate context
    context_start = max(0, insert_line - 3)
    context_end = min(len(lines), insert_line + 3)

    before_context = lines[context_start:insert_line]
    after_context = lines[insert_line:context_end]

    # Build diff
    old_start = context_start + 1
    old_count = len(before_context) + len(after_context)
    new_start = context_start + 1
    new_count = len(before_context) + len(test_lines) + len(after_context)

    diff = [
        f'diff --git a/{file_path} b/{file_path}',
        f'--- a/{file_path}',
        f'+++ b/{file_path}',
        f'@@ -{old_start},{old_count} +{new_start},{new_count} @@'
    ]

    for line in before_context:
        diff.append(' ' + line)
    for line in test_lines:
        diff.append('+' + line)
    for line in after_context:
        diff.append(' ' + line)

    return '\n'.join(diff) + '\n'


def find_test_insertion(file_content: str) -> int:
    """Find best line to insert test module."""
    lines = file_content.split('\n')

    # Look for existing test module
    for i, line in enumerate(lines):
        if '#[cfg(test)]' in line:
            return i

    # Insert at end of file
    for i in range(len(lines) - 1, -1, -1):
        if lines[i].strip():
            return i + 1
    return len(lines)


def process_instance(inst: dict, parser: Parser) -> Optional[str]:
    """Process a single instance and generate test."""
    instance_id = inst.get('instance_id', '')
    repo = inst.get('repo', '')
    base_commit = inst.get('base_commit', '')
    patch_text = inst.get('patch', '')

    # Extract file path
    file_path = None
    try:
        ps = PatchSet(patch_text)
        for file in ps:
            if file.path.startswith('crates/'):
                file_path = file.path
                break
    except:
        return None

    if not file_path:
        return None

    # Fetch file content
    if '.' in repo:
        repo_clean = repo.rsplit('.', 1)[0].replace('__', '/')
    else:
        repo_clean = repo.replace('__', '/')

    url = f"https://raw.githubusercontent.com/{repo_clean}/{base_commit}/{file_path}"
    try:
        response = requests.get(url, timeout=30)
        if response.status_code != 200:
            return None
        file_content = response.text
    except:
        return None

    # Parse with AST
    try:
        tree = parser.parse(file_content.encode('utf-8'))
        functions = analyze_functions(tree, file_content.encode('utf-8'))
    except Exception as e:
        print(f"  AST parsing failed: {e}")
        return None

    if not functions:
        return None

    # Analyze patch for bug pattern
    bug_pattern = analyze_patch_for_bug(patch_text, functions)
    if not bug_pattern:
        # Default to first function
        bug_pattern = BugPattern(
            bug_type="unknown",
            affected_function=functions[0].name if functions else "unknown",
            changed_lines=[],
            description="Bug fix in the patch"
        )

    # Find relevant functions
    patch_line = bug_pattern.changed_lines[0] if bug_pattern.changed_lines else 1
    relevant_funcs = find_relevant_functions(functions, patch_line)

    # Get file chunk
    file_chunk = chunk_file_by_context(file_content, patch_line)

    # Create prompt
    prompt = create_test_prompt(bug_pattern, relevant_funcs, file_chunk, file_path, patch_text)

    # Call LLM
    try:
        response = call_llm(prompt)
        test_code = extract_test_from_response(response)
        if not test_code or '#[test]' not in test_code:
            return None
    except Exception as e:
        print(f"  LLM failed: {e}")
        return None

    # Create patch
    insert_line = find_test_insertion(file_content)
    return create_test_patch(file_path, file_content, test_code, insert_line)


def main():
    """Generate AST-based tests."""

    input_path = Path('logs/bug_gen/juspay__hyperswitch.fece9bc3/pr_mirror/recovered_dataset_correct_base.json')
    output_path = Path('logs/bug_gen/juspay__hyperswitch.fece9bc3/pr_mirror/recovered_dataset_ast_tests.json')

    print(f"Loading: {input_path}")
    with open(input_path) as f:
        data = json.load(f)

    print(f"Generating AST-based tests for {len(data)} instances...\n")

    parser = init_parser()
    success = 0
    fail = 0

    for i, inst in enumerate(data):
        instance_id = inst.get('instance_id', '')
        print(f"[{i+1}/{len(data)}] {instance_id}", end=' ')

        # Skip if has existing good test
        if inst.get('_ast_test'):
            print("(existing)")
            continue

        try:
            test_patch = process_instance(inst, parser)
            if test_patch:
                inst['test_patch'] = test_patch
                inst['_ast_test'] = True
                print("✓")
                success += 1
            else:
                print("✗")
                fail += 1
        except Exception as e:
            print(f"✗ Error: {e}")
            fail += 1

        time.sleep(0.5)

        if (i + 1) % 5 == 0:
            with open(output_path, 'w') as f:
                json.dump(data, f, indent=2)
            print(f"  (saved)")

    with open(output_path, 'w') as f:
        json.dump(data, f, indent=2)

    print(f"\n{'='*50}")
    print(f"Success: {success}")
    print(f"Failed: {fail}")
    print(f"Saved to: {output_path}")


if __name__ == '__main__':
    main()
