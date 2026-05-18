#!/usr/bin/env python3
"""
Extract actual functions and classes from vLLM codebase at commit 3e1ad443.
Filter for targets that can be tested without GPU dependencies.
"""

import ast
import os
import json
from pathlib import Path
from typing import Dict, List, Tuple, Optional

VLLM_PATH = "/Users/aditya.singh.001/Desktop/SWE-smith/tmp_d6b73da0/vllm-project__vllm.3e1ad443"
OUTPUT_FILE = "/Users/aditya.singh.001/Desktop/SWE-smith/vllm_valid_targets_3e1ad443.json"

# Patterns to exclude (GPU-dependent, platform-specific, or test-only)
EXCLUDE_PATTERNS = [
    # GPU/CUDA specific paths
    "cuda",
    "gpu",
    "triton",
    "rocm",
    "intel",
    "tpu",
    "xpu",
    "openvino",
    "ascend",
    "neuron",
    "outlines",
    # Model-specific execution (heavy)
    "model_executor/layers/quantization",
    "model_executor/layers/fused_moe",
    "model_executor/layers/fp8",
    "model_executor/layers/mamba",
    "model_executor/layers/attention",
    # Kernel-level code
    "kernels",
    "_kernels",
    # Platform/device specific
    "platforms/",
    "device_util.py",
    "current_platform.py",
    # Benchmarks and tests
    "benchmarks/",
    "tests/",
    "test_",
    "_test.py",
    # C extensions and compiled code
    "_C",
    "_core",
    "_flash_attn",
    # Entry points (mainly orchestration)
    "entrypoints/openai",
    "entrypoints/cli",
    # V1 experimental (may change)
    "v1/",
]

# Include patterns for safe targets
INCLUDE_PATTERNS = [
    # Config classes (usually safe)
    "config.py",
    "configs/",
    "arg_utils.py",
    # Utility functions
    "utils.py",
    "_utils.py",
    # Transformers utils
    "transformers_utils/",
    # Sampling and logits (mostly pure Python)
    "sampling/",
    "logits_process.py",
    "logits_processor.py",
    # Sequence and data structures
    "sequence.py",
    "pooling_params.py",
    "sampling_params.py",
    # Tokenizer utils
    "tokenizer_utils",
    # Inputs and outputs
    "inputs/",
    "outputs/",
    # Core model definitions (not execution)
    "models/",  # Model architectures, but we'll filter individual files
]

# Known GPU-dependent modules to exclude even if in included paths
GPU_DEPENDENT_MODULES = {
    'torch.cuda',
    'triton',
    'vllm._C',
    'vllm._core',
    'vllm._flash_attn',
    'vllm._fp8',
    'vllm._ipex_ops',
    'vllm._moe',
    'vllm._custom_ops',
    'vllm.model_executor.layers.fused_moe',
    'vllm.model_executor.layers.fp8',
    'vllm.model_executor.layers.rotary_embedding',
    'vllm.platforms.cuda',
    'vllm.platforms.rocm',
    'vllm.platforms.tpu',
}


def has_gpu_dependencies(file_path: str) -> bool:
    """Check if a Python file imports GPU-dependent modules."""
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()

        # Quick string checks for GPU patterns
        gpu_patterns = [
            'torch.cuda',
            'import triton',
            'from triton',
            'from vllm._C',
            'from vllm._core',
            'from vllm._moe',
            'from vllm._custom_ops',
            'current_platform.is_cuda',
            'get_device_capability',
            '.cuda()',
        ]

        for pattern in gpu_patterns:
            if pattern in content:
                return True

        # Parse imports more carefully
        try:
            tree = ast.parse(content)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if any(alias.name.startswith(mod) for mod in GPU_DEPENDENT_MODULES):
                            return True
                elif isinstance(node, ast.ImportFrom):
                    if node.module and any(node.module.startswith(mod) for mod in GPU_DEPENDENT_MODULES):
                        return True
        except SyntaxError:
            pass

        return False
    except Exception as e:
        print(f"Error checking {file_path}: {e}")
        return True  # Exclude on error


def extract_functions_and_classes(file_path: str) -> Tuple[List[Dict], List[Dict]]:
    """Extract function and class definitions from a Python file."""
    functions = []
    classes = []

    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()

        tree = ast.parse(content)
        file_rel_path = os.path.relpath(file_path, VLLM_PATH)

        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                # Skip private functions (single underscore) and test functions
                if node.name.startswith('_') and not node.name.startswith('__'):
                    continue
                if node.name.startswith('test_'):
                    continue

                # Get function signature
                args = node.args
                arg_count = len(args.args) + len(args.posonlyargs) + len(args.kwonlyargs)
                has_varargs = args.vararg is not None
                has_kwargs = args.kwarg is not None

                # Skip functions that are too simple
                if arg_count == 0 and not has_varargs and not has_kwargs:
                    # Count actual logic lines (non-empty, non-comment)
                    body_lines = [ast.unparse(stmt) for stmt in node.body if not isinstance(stmt, ast.Pass)]
                    if len(body_lines) < 3:
                        continue

                # Get function source lines
                func_lines = content.split('\n')[node.lineno-1:node.end_lineno]
                func_source = '\n'.join(func_lines)

                functions.append({
                    'name': node.name,
                    'file': file_rel_path,
                    'line': node.lineno,
                    'end_line': node.end_lineno,
                    'arg_count': arg_count,
                    'has_varargs': has_varargs,
                    'has_kwargs': has_kwargs,
                    'docstring': ast.get_docstring(node),
                    'source': func_source,
                    'decorators': [ast.unparse(d) for d in node.decorator_list],
                })

            elif isinstance(node, ast.ClassDef):
                # Skip private classes and test classes
                if node.name.startswith('_'):
                    continue
                if node.name.startswith('Test'):
                    continue
                if 'unittest' in [ast.unparse(base) for base in node.bases]:
                    continue

                # Skip dataclass-style classes that are just containers
                methods = [n for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
                meaningful_methods = [m for m in methods if m.name not in ('__init__', '__post_init__', '__repr__')]

                # Get class source
                class_lines = content.split('\n')[node.lineno-1:node.end_lineno]
                class_source = '\n'.join(class_lines)

                # Count methods
                method_names = [m.name for m in methods]

                classes.append({
                    'name': node.name,
                    'file': file_rel_path,
                    'line': node.lineno,
                    'end_line': node.end_lineno,
                    'method_count': len(methods),
                    'meaningful_methods': len(meaningful_methods),
                    'method_names': method_names,
                    'bases': [ast.unparse(base) for base in node.bases],
                    'docstring': ast.get_docstring(node),
                    'source': class_source,
                })

    except SyntaxError as e:
        print(f"Syntax error in {file_path}: {e}")
    except Exception as e:
        print(f"Error parsing {file_path}: {e}")

    return functions, classes


def score_target(target: Dict, target_type: str) -> float:
    """Score a target for suitability as a bug injection candidate."""
    score = 0.0

    if target_type == 'function':
        # Prefer functions with moderate complexity
        if target['arg_count'] >= 2:
            score += 1
        if target['arg_count'] >= 3:
            score += 1

        # Longer functions are more likely to have meaningful logic
        line_count = target['end_line'] - target['line']
        if 10 <= line_count <= 100:
            score += 2
        elif 5 <= line_count < 10:
            score += 1

        # Prefer functions with docstrings (public API)
        if target.get('docstring'):
            score += 1

        # Avoid property getters/setters
        if 'property' in target.get('decorators', []):
            score -= 2

        # Avoid abstract methods
        if 'abstractmethod' in str(target.get('decorators', [])):
            score -= 2

    elif target_type == 'class':
        # Prefer classes with methods
        if target['meaningful_methods'] >= 2:
            score += 2
        elif target['meaningful_methods'] >= 1:
            score += 1

        # Config-like classes are good targets
        if 'Config' in target['name']:
            score += 1

        # Prefer classes with reasonable size
        line_count = target['end_line'] - target['line']
        if 20 <= line_count <= 200:
            score += 1

        # Has docstring
        if target.get('docstring'):
            score += 1

    return score


def main():
    print(f"Scanning vLLM codebase at {VLLM_PATH}...")

    all_functions = []
    all_classes = []
    scanned_files = 0
    excluded_gpu = 0

    vllm_root = Path(VLLM_PATH)
    python_files = list(vllm_root.rglob("*.py"))

    # Filter out test files and other exclusions first
    valid_files = []
    for file_path in python_files:
        rel_path = str(file_path.relative_to(vllm_root))

        # Skip obvious exclusions
        if any(pattern in rel_path for pattern in EXCLUDE_PATTERNS):
            continue
        if rel_path.startswith('.') or 'test' in rel_path.lower():
            continue

        valid_files.append(file_path)

    print(f"Found {len(python_files)} Python files, {len(valid_files)} after initial filtering")

    for file_path in valid_files:
        rel_path = str(file_path.relative_to(vllm_root))

        # Check for GPU dependencies
        if has_gpu_dependencies(str(file_path)):
            excluded_gpu += 1
            continue

        scanned_files += 1
        functions, classes = extract_functions_and_classes(str(file_path))

        all_functions.extend(functions)
        all_classes.extend(classes)

    print(f"\nScanned {scanned_files} files (excluded {excluded_gpu} GPU-dependent)")
    print(f"Found {len(all_functions)} functions and {len(all_classes)} classes")

    # Score and filter targets
    scored_functions = [(f, score_target(f, 'function')) for f in all_functions]
    scored_classes = [(c, score_target(c, 'class')) for c in all_classes]

    # Sort by score (descending)
    scored_functions.sort(key=lambda x: x[1], reverse=True)
    scored_classes.sort(key=lambda x: x[1], reverse=True)

    # Filter to reasonable candidates (score >= 2)
    good_functions = [f for f, score in scored_functions if score >= 2]
    good_classes = [c for c, score in scored_classes if score >= 2]

    print(f"\nAfter scoring: {len(good_functions)} good functions, {len(good_classes)} good classes")

    # Build module paths for easy importing
    for f in good_functions:
        f['module_path'] = f['file'].replace('/', '.').replace('.py', '')
        f['full_name'] = f"{f['module_path']}.{f['name']}"
        f['source'] = None  # Remove source to save space

    for c in good_classes:
        c['module_path'] = c['file'].replace('/', '.').replace('.py', '')
        c['full_name'] = f"{c['module_path']}.{c['name']}"
        c['source'] = None  # Remove source to save space

    # Build simplified lists for all targets
    all_func_sigs = []
    for f in all_functions:
        mod_path = f['file'].replace('/', '.').replace('.py', '')
        all_func_sigs.append({'name': f['name'], 'full_name': f"{mod_path}.{f['name']}", 'file': f['file']})

    all_class_sigs = []
    for c in all_classes:
        mod_path = c['file'].replace('/', '.').replace('.py', '')
        all_class_sigs.append({'name': c['name'], 'full_name': f"{mod_path}.{c['name']}", 'file': c['file']})

    # Create final output
    output = {
        'metadata': {
            'commit': '3e1ad443',
            'total_files_scanned': scanned_files,
            'gpu_excluded_files': excluded_gpu,
            'total_functions_found': len(all_functions),
            'total_classes_found': len(all_classes),
            'filtered_functions': len(good_functions),
            'filtered_classes': len(good_classes),
        },
        'functions': good_functions[:500],  # Top 500 functions
        'classes': good_classes[:200],  # Top 200 classes
        'all_function_signatures': all_func_sigs,
        'all_class_names': all_class_sigs,
    }

    with open(OUTPUT_FILE, 'w') as f:
        json.dump(output, f, indent=2)

    print(f"\n✅ Results saved to {OUTPUT_FILE}")
    print(f"   - Top 500 functions for bug generation")
    print(f"   - Top 200 classes for bug generation")
    print(f"   - Full lists in 'all_function_signatures' and 'all_class_names'")

    # Print top 10 examples
    print("\n" + "="*80)
    print("TOP 10 FUNCTION TARGETS:")
    print("="*80)
    for f in good_functions[:10]:
        print(f"  {f['full_name']} (line {f['line']})")
        if f.get('docstring'):
            doc_preview = f['docstring'][:80].replace('\n', ' ')
            print(f"    Doc: {doc_preview}...")

    print("\n" + "="*80)
    print("TOP 10 CLASS TARGETS:")
    print("="*80)
    for c in good_classes[:10]:
        print(f"  {c['full_name']} ({c['method_count']} methods)")
        if c.get('docstring'):
            doc_preview = c['docstring'][:80].replace('\n', ' ')
            print(f"    Doc: {doc_preview}...")


if __name__ == "__main__":
    main()
