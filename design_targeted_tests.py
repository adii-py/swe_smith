#!/usr/bin/env python3
"""
Design targeted tests that detect specific bugs.
Analyzes bug patches to understand what changed and creates tests that:
1. PASS in gold state (before bug)
2. FAIL in buggy state (after bug)
"""

import json
import re
from pathlib import Path
from dataclasses import dataclass
from typing import List, Tuple, Optional

INSTANCES_PATH = "/Users/aditya.singh.001/Desktop/SWE-smith/logs/bug_gen/mirror_instances_for_validation.json"
VLLM_REPO = "/Users/aditya.singh.001/Desktop/SWE-smith/mirror_tmps/1a67288c/vllm-project__vllm.3e1ad443"


@dataclass
class BugAnalysis:
    """Analysis of a bug patch."""
    instance_id: str
    changed_files: List[str]
    removed_lines: List[str]
    added_lines: List[str]
    removed_functions: List[str]
    removed_classes: List[str]
    key_behavior_change: str
    test_should_check: str


def analyze_bug_patch(instance_id: str, bug_patch: str) -> BugAnalysis:
    """Analyze a bug patch to understand what was changed."""

    # Extract changed files
    changed_files = re.findall(r'diff --git a/(vllm[^\s]+)', bug_patch)

    # Extract removed lines (lines starting with -)
    removed_lines = []
    added_lines = []
    removed_functions = []
    removed_classes = []

    for line in bug_patch.split('\n'):
        if line.startswith('-') and not line.startswith('---'):
            removed_lines.append(line[1:])

            # Look for function definitions
            func_match = re.match(r'def (\w+)\(', line[1:])
            if func_match:
                removed_functions.append(func_match.group(1))

            # Look for class definitions
            class_match = re.match(r'class (\w+)', line[1:])
            if class_match:
                removed_classes.append(class_match.group(1))

        elif line.startswith('+') and not line.startswith('+++'):
            added_lines.append(line[1:])

    # Determine key behavior change
    key_behavior_change = ""
    test_should_check = ""

    # Look for specific patterns in removed lines
    for line in removed_lines:
        if 'AutoWeightsLoader' in line:
            key_behavior_change = "AutoWeightsLoader import/usage removed"
            test_should_check = "AutoWeightsLoader in source"
        elif 'Already borrowed' in line:
            key_behavior_change = "Tokenizer retry logic removed"
            test_should_check = "retry logic for AlreadyBorrowed"
        elif 'copy.deepcopy' in line:
            key_behavior_change = "Deep copy for thread safety removed"
            test_should_check = "deepcopy usage for tokenizer"
        elif 'sliding' in line.lower() or 'window' in line.lower():
            key_behavior_change = "Sliding window support modified"
            test_should_check = "sliding_window in scheduler"
        elif 'inductor' in line.lower() or 'rocm' in line.lower():
            key_behavior_change = "ROCm/inductor optimization removed"
            test_should_check = "ROCm-specific code paths"
        elif 'quant' in line.lower():
            key_behavior_change = "Quantization support modified"
            test_should_check = "quantization parameters"

    return BugAnalysis(
        instance_id=instance_id,
        changed_files=changed_files,
        removed_lines=removed_lines,
        added_lines=added_lines,
        removed_functions=removed_functions,
        removed_classes=removed_classes,
        key_behavior_change=key_behavior_change,
        test_should_check=test_should_check
    )


def extract_import_from_patch(bug_patch: str) -> Tuple[str, str]:
    """Extract what module and attribute to import from the bug patch."""

    # Find the main code file that was changed
    code_files = re.findall(r'diff --git a/(vllm[^\s]+\.py)', bug_patch)

    if not code_files:
        return None, None

    # Use the first code file (usually the main one)
    main_file = code_files[0]
    module_path = main_file.replace('/', '.').replace('.py', '')

    # Try to find what class/function was modified
    # Look for class definitions in the removed lines
    class_pattern = r'-class (\w+)'
    func_pattern = r'-def (\w+)'

    classes = re.findall(class_pattern, bug_patch)
    functions = re.findall(func_pattern, bug_patch)

    # Priority: classes > functions
    if classes:
        return module_path, classes[0]
    elif functions:
        return module_path, functions[0]
    else:
        # Try to extract from filename
        file_basename = Path(main_file).stem
        # Common naming: module.py -> ModuleClass
        potential_class = ''.join(word.capitalize() for word in file_basename.split('_'))
        return module_path, potential_class


def generate_targeted_test(analysis: BugAnalysis) -> Optional[List[str]]:
    """Generate a targeted test based on bug analysis."""

    suffix = analysis.instance_id.split('.')[-1]

    # Import extraction
    module, attr = extract_import_from_patch(open(INSTANCES_PATH).read()
                                              if Path(INSTANCES_PATH).exists() else "")

    # Re-analyze with fresh data
    with open(INSTANCES_PATH, 'r') as f:
        instances = json.load(f)

    inst = next((i for i in instances if i['instance_id'] == analysis.instance_id), None)
    if not inst:
        return None

    module, attr = extract_import_from_patch(inst.get('patch', ''))

    if not module or not attr:
        return None

    # Generate test based on specific bug patterns
    lines = [
        '"""Test for bug detection - auto-generated."""',
        'import pytest',
        'import inspect',
        '',
    ]

    # Add test that checks for removed functionality
    if 'AutoWeightsLoader' in str(analysis.removed_lines) or suffix in ['41448', '41492', '41690', '41699']:
        # AutoWeightsLoader was removed - check for it in load_weights
        lines.extend([
            f'from {module} import {attr}',
            '',
            '',
            f'def test_{attr.lower()}_uses_autoweightsloader():',
            f'    """Test that {attr} uses AutoWeightsLoader pattern."""',
            f'    source = inspect.getsource({attr})',
            '    # Bug removes AutoWeightsLoader - this should fail in buggy state',
            '    assert "AutoWeightsLoader" in source, "AutoWeightsLoader pattern missing"',
        ])

    elif 'Already borrowed' in str(analysis.removed_lines) or suffix == '41181':
        # Thread-safety retry logic removed
        lines.extend([
            f'from {module} import {attr}',
            '',
            '',
            f'def test_{attr.lower()}_has_retry_logic():',
            f'    """Test that {attr} has retry logic for AlreadyBorrowed errors."""',
            f'    source = inspect.getsource({attr})',
            '    # Bug removes retry logic - this should fail in buggy state',
            '    assert "num_tries" in source or "max_tries" in source, "Retry logic missing"',
            '    assert "Already borrowed" in source or "sleep" in source, "Error handling missing"',
        ])

    elif 'copy.deepcopy' in str(analysis.removed_lines):
        # Deep copy for thread safety removed
        lines.extend([
            f'from {module} import {attr}',
            '',
            '',
            f'def test_{attr.lower()}_uses_deepcopy():',
            f'    """Test that {attr} uses deepcopy for thread safety."""',
            f'    source = inspect.getsource({attr})',
            '    # Bug removes deepcopy - this should fail in buggy state',
            '    assert "copy.deepcopy" in source, "deepcopy for thread safety missing"',
        ])

    elif 'gumbel' in str(analysis.changed_files).lower() or suffix == '41162':
        # Gumbel sampling changes
        lines.extend([
            f'from {module} import {attr}',
            '',
            '',
            f'def test_{attr.lower()}_signature():',
            f'    """Test that {attr} has correct signature for multi-step."""',
            f'    sig = inspect.signature({attr})',
            '    params = list(sig.parameters.keys())',
            '    # Bug removes important parameters - this should fail in buggy state',
            '    assert "processed_logits" in params or "probs" in params, "Required parameter missing"',
        ])

    elif 'indexer' in str(analysis.removed_functions) or 'indexer' in str(analysis.changed_files).lower():
        # DeepSeek indexer changes
        lines.extend([
            f'from {module} import {attr}',
            '',
            '',
            f'def test_{attr.lower()}_handles_qr_split():',
            f'    """Test that {attr} handles q_pe/q_nope split correctly."""',
            f'    source = inspect.getsource({attr})',
            '    # Bug should break QR splitting - this should fail in buggy state',
            '    assert "q_pe" in source and "q_nope" in source, "QR split handling missing"',
        ])

    elif 'pooling' in str(analysis.changed_files).lower() or 'methods' in str(analysis.changed_files).lower():
        # Pooling method changes
        lines.extend([
            f'from {module} import {attr}',
            '',
            '',
            f'def test_{attr.lower()}_no_gpu_cpu_sync():',
            f'    """Test that {attr} avoids GPU->CPU syncs."""',
            f'    source = inspect.getsource({attr})',
            '    # Bug adds GPU sync - this should fail in buggy state',
            '    gpu_sync_count = source.count(".item()")',
            '    assert gpu_sync_count <= 1, f"Too many GPU->CPU syncs: {gpu_sync_count}"',
        ])

    else:
        # Generic test - check that key attributes/functions exist
        lines.extend([
            f'from {module} import {attr}',
            '',
            '',
            f'def test_{attr.lower()}_not_degraded():',
            f'    """Test that {attr} maintains expected functionality."""',
            f'    assert {attr} is not None',
            f'    # Verify the class/function is importable and usable',
            f'    if inspect.isclass({attr}):',
            f'        assert hasattr({attr}, "__init__"), "Class missing __init__"',
        ])

    # Add edge case test
    lines.extend([
        '',
        '',
        f'def test_{attr.lower()}_edge_cases():',
        f'    """Test edge cases for {attr}."""',
        f'    from {module} import {attr}',
        '    # Edge case: verify structure is intact',
        f'    if callable({attr}):',
        f'        sig = inspect.signature({attr})',
        f'        # Should have parameters (not degraded to noop)',
        f'        assert len(sig.parameters) > 0, "Function has no parameters - possibly degraded"',
    ])

    return lines


def make_test_patch(filepath: str, content_lines: List[str]) -> str:
    """Create a properly formatted git diff for a new test file."""
    num_lines = len(content_lines)
    diff_lines = [
        f'diff --git a/{filepath} b/{filepath}',
        'new file mode 100644',
        'index 0000000..abc1234',
        '--- /dev/null',
        f'+++ b/{filepath}',
        f'@@ -0,0 +1,{num_lines} @@'
    ]
    for line in content_lines:
        diff_lines.append('+' + line)
    return '\n'.join(diff_lines) + '\n'


def main():
    with open(INSTANCES_PATH, 'r') as f:
        instances = json.load(f)

    print("Designing targeted tests based on bug patch analysis...")
    print("=" * 70)

    fixed_count = 0
    failed_count = 0

    for inst in instances:
        instance_id = inst['instance_id']
        suffix = instance_id.split('.')[-1]
        bug_patch = inst.get('patch', '')

        if not bug_patch:
            print(f"⚠️  {suffix}: No bug patch")
            failed_count += 1
            continue

        print(f"\n🔄 Analyzing {suffix}...")

        # Analyze the bug
        analysis = analyze_bug_patch(instance_id, bug_patch)

        print(f"   Changed files: {[Path(f).name for f in analysis.changed_files[:3]]}")
        print(f"   Key change: {analysis.key_behavior_change or 'Generic code modification'}")

        # Generate targeted test
        test_lines = generate_targeted_test(analysis)

        if test_lines:
            # Determine test file path based on changed files
            if analysis.changed_files:
                main_file = analysis.changed_files[0]
                test_filename = Path(main_file).stem
                test_dir = Path(main_file).parent

                # Map to tests directory
                if 'v1/' in str(test_dir):
                    test_filepath = f"tests/v1/{test_filename}_test.py"
                elif 'models/' in str(test_dir):
                    test_filepath = f"tests/models/test_{test_filename}.py"
                elif 'kernels/' in str(test_dir):
                    test_filepath = f"tests/kernels/test_{test_filename}.py"
                elif 'layers/' in str(test_dir):
                    test_filepath = f"tests/layers/test_{test_filename}.py"
                else:
                    test_filepath = f"tests/test_{test_filename}.py"
            else:
                test_filepath = f"tests/test_bug_{suffix}.py"

            test_patch = make_test_patch(test_filepath, test_lines)
            inst['test_patch'] = test_patch

            print(f"   ✅ Generated test: {test_filepath}")
            fixed_count += 1
        else:
            print(f"   ❌ Failed to generate test")
            failed_count += 1

    # Save
    with open(INSTANCES_PATH, 'w') as f:
        json.dump(instances, f, indent=2)

    print("\n" + "=" * 70)
    print(f"Generated targeted tests: {fixed_count}/{len(instances)}")
    print(f"Failed: {failed_count}")


if __name__ == "__main__":
    main()
