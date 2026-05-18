#!/usr/bin/env python3
"""
Enhanced targeted test generation with:
1. AST parsing for deeper code understanding
2. Expanded pattern library (15+ bug patterns)
3. LLM integration for edge cases
4. Validation pipeline for generated tests
5. CORRECT IMPORT handling (classes for methods)
6. Bug-specific test generation based on diff analysis

Usage: python design_targeted_tests_v2.py --instances <path> [--use-llm] [--validate]
"""

import argparse
import ast
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Tuple, Optional, Dict, Any, Set
from functools import lru_cache

# Optional LLM support
try:
    import litellm
    from litellm import completion
    HAS_LITELLM = True
except ImportError:
    HAS_LITELLM = False

# Rust/Hyperswitch configuration (modified from Python/vLLM defaults)
DEFAULT_INSTANCES_PATH = "/Users/aditya.singh.001/Desktop/SWE-smith/logs/bug_gen/juspay__hyperswitch.fece9bc3/pr_mirror/recovered_dataset_clean.json"
REPO_PATH = "/tmp/hyperswitch"


@dataclass
class BugAnalysis:
    """Enhanced analysis of a bug patch with AST support."""
    instance_id: str
    changed_files: List[str]
    removed_lines: List[str]
    added_lines: List[str]
    removed_functions: List[str]
    removed_classes: List[str]
    key_behavior_change: str
    test_should_check: str

    # Bug type detection
    bug_type: str = "unknown"
    original_behavior: str = ""
    buggy_behavior: str = ""
    how_to_detect: str = ""
    specific_check: str = ""

    # AST-enhanced fields
    ast_analysis: Dict[str, Any] = field(default_factory=dict)
    bug_patterns: List[str] = field(default_factory=list)
    confidence_score: float = 0.0

    # Import info
    is_method: bool = False
    class_name: Optional[str] = None
    method_name: Optional[str] = None
    module_path: Optional[str] = None

    # Context
    surrounding_context: Dict[str, List[str]] = field(default_factory=dict)


@dataclass
class TestTemplate:
    """Template for generating a test."""
    name: str
    description: str
    imports: List[str]
    setup_code: List[str]
    test_body: List[str]
    fail_to_pass_tests: List[str] = field(default_factory=list)
    assertion_type: str = "source_inspection"


# =============================================================================
# PHASE 1: ENHANCED STATIC ANALYSIS WITH AST PARSING
# =============================================================================

class ASTAnalyzer:
    """Analyze Python code using AST for deeper understanding."""

    @staticmethod
    def parse_module(source: str) -> Optional[ast.AST]:
        """Safely parse Python source code."""
        try:
            return ast.parse(source)
        except SyntaxError:
            return None

    @staticmethod
    def extract_function_signatures(tree: ast.AST) -> Dict[str, Dict]:
        """Extract function names and their signatures."""
        signatures = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                params = []
                defaults_start = len(node.args.args) - len(node.args.defaults)

                for i, arg in enumerate(node.args.args):
                    param_info = {"name": arg.arg, "default": None}
                    if i >= defaults_start:
                        default = node.args.defaults[i - defaults_start]
                        try:
                            param_info["default"] = ast.literal_eval(default)
                        except:
                            param_info["default"] = "<expression>"
                    params.append(param_info)

                signatures[node.name] = {
                    "params": params,
                    "line": node.lineno,
                    "decorators": [d.id if isinstance(d, ast.Name) else str(d) for d in node.decorator_list],
                }
        return signatures

    @staticmethod
    def extract_class_structure(tree: ast.AST) -> Dict[str, Dict]:
        """Extract class names and their methods."""
        classes = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                methods = []
                for item in node.body:
                    if isinstance(item, ast.FunctionDef):
                        methods.append(item.name)
                classes[node.name] = {
                    "methods": methods,
                    "line": node.lineno,
                    "bases": [b.id if isinstance(b, ast.Name) else str(b) for b in node.bases],
                }
        return classes

    @staticmethod
    def find_imports(tree: ast.AST) -> List[Dict]:
        """Find all imports in the code."""
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append({"type": "import", "name": alias.name, "as": alias.asname})
            elif isinstance(node, ast.ImportFrom):
                imports.append({
                    "type": "from",
                    "module": node.module,
                    "names": [alias.name for alias in node.names],
                })
        return imports

    @staticmethod
    def detect_operator_changes(old_line: str, new_line: str) -> Optional[str]:
        """Detect if logical/arithmetic operators changed."""
        operator_patterns = [
            (r'\bor\b', r'\band\b', 'logical_or_to_and'),
            (r'\band\b', r'\bor\b', 'logical_and_to_or'),
            (r'==', r'!=', 'equality_to_inequality'),
            (r'!=', r'==', 'inequality_to_equality'),
            (r'<', r'>', 'less_to_greater'),
            (r'>', r'<', 'greater_to_less'),
            (r'\+', r'-', 'add_to_sub'),
            (r'-', r'\+', 'sub_to_add'),
            (r'\*', r'/', 'mul_to_div'),
            (r'/', r'\*', 'div_to_mul'),
            (r'is not', r'is', 'is_not_to_is'),
        ]

        for old_pat, new_pat, change_type in operator_patterns:
            if re.search(old_pat, old_line) and re.search(new_pat, new_line):
                return change_type
        return None


def analyze_what_bug_was_introduced(original_code: str, buggy_code: str) -> Dict:
    """
    CRITICAL: Analyze the difference between original and buggy code to understand what bug was introduced.
    This is essential for writing a test that detects the bug.
    """
    analysis = {
        "bug_type": "unknown",
        "original_behavior": "",
        "buggy_behavior": "",
        "how_to_detect": "",
        "specific_check": "",
    }

    # Split into lines for comparison
    orig_lines = [l.strip() for l in original_code.split('\n') if l.strip()]
    bug_lines = [l.strip() for l in buggy_code.split('\n') if l.strip()]

    # Find changed lines
    removed = []
    added = []

    for line in orig_lines:
        if line not in bug_lines:
            removed.append(line)

    for line in bug_lines:
        if line not in orig_lines:
            added.append(line)

    # Analyze patterns
    removed_str = '\n'.join(removed)
    added_str = '\n'.join(added)

    # Pattern 1: TODO or pass added
    if 'TODO' in added_str or ("pass" in added_str and "pass" not in removed_str):
        analysis["bug_type"] = "not_implemented"
        analysis["original_behavior"] = "Function had working implementation"
        analysis["buggy_behavior"] = "Function now has TODO/pass, doesn't implement logic"
        analysis["how_to_detect"] = "Check that function actually does work, not just pass"
        analysis["specific_check"] = 'assert "TODO" not in source and source.count("pass") < 2'

    # Pattern 2: Return None added
    elif 'return None' in added_str and 'return None' not in removed_str:
        analysis["bug_type"] = "returns_none"
        analysis["original_behavior"] = "Function returned computed value"
        analysis["buggy_behavior"] = "Function returns None"
        analysis["how_to_detect"] = "Call function and check return value is not None"
        analysis["specific_check"] = "result = func(); assert result is not None"

    # Pattern 3: Wrong variable used
    elif removed and added:
        # Check for variable name changes
        var_pattern = r'\b(\w+)\b'
        orig_vars = set(re.findall(var_pattern, removed_str))
        bug_vars = set(re.findall(var_pattern, added_str))

        # Look for similar variable names (potential typos)
        for ov in orig_vars:
            for bv in bug_vars:
                if ov != bv and len(ov) > 2 and len(bv) > 2:
                    if ov in bv or bv in ov or sum(a!=b for a,b in zip(ov,bv)) <= 2:
                        analysis["bug_type"] = "wrong_variable"
                        analysis["original_behavior"] = f"Used correct variable '{ov}'"
                        analysis["buggy_behavior"] = f"Uses wrong variable '{bv}'"
                        analysis["how_to_detect"] = f"Check that '{ov}' is used, not '{bv}'"
                        analysis["specific_check"] = f'assert "{ov}" in source'
                        break

    # Pattern 4: Operator changed
    operators = [('==', '!='), ('!=', '=='), ('<', '>'), ('>', '<'), ('and', 'or'), ('or', 'and')]
    for orig_op, bug_op in operators:
        if f' {orig_op} ' in removed_str and f' {bug_op} ' in added_str:
            analysis["bug_type"] = "operator_swap"
            analysis["original_behavior"] = f"Used '{orig_op}' operator"
            analysis["buggy_behavior"] = f"Uses '{bug_op}' operator"
            analysis["how_to_detect"] = f"Check that '{orig_op}' is used"
            analysis["specific_check"] = f'assert " {orig_op} " in source'
            break

    # Pattern 5: Method call forgotten
    method_pattern = r'(\w+)\.(\w+)\('
    orig_methods = re.findall(method_pattern, removed_str)
    if orig_methods and '(' not in added_str:
        analysis["bug_type"] = "forgot_method_call"
        analysis["original_behavior"] = "Called method with ()"
        analysis["buggy_behavior"] = "References method without calling it"
        analysis["how_to_detect"] = "Check that method is called with ()"
        analysis["specific_check"] = "assert '.is_valid(' in source or similar"

    return analysis


def extract_surrounding_context(file_path: str, changed_lines: List[int], context_lines: int = 30) -> List[str]:
    """Extract context around changed lines."""
    if not Path(file_path).exists():
        return []

    try:
        with open(file_path, 'r') as f:
            all_lines = f.readlines()
    except Exception:
        return []

    context = []
    for line_num in changed_lines:
        start = max(0, line_num - context_lines - 1)
        end = min(len(all_lines), line_num + context_lines)
        context.extend(all_lines[start:end])

    return context


# =============================================================================
# EXPANDED BUG PATTERN LIBRARY
# =============================================================================

BUG_PATTERNS = {
    # Core bug types that affect F2P
    'not_implemented': {
        'patterns': ['TODO', 'FIXME', 'pass', 'NotImplementedError'],
        'check': 'source_contains',
        'assertion': 'assert "TODO" not in source and source.count("pass") < 2',
        'description': 'Function should be implemented, not just pass/TODO',
    },
    'returns_none': {
        'patterns': ['return None'],
        'check': 'runtime_check',
        'assertion': 'assert result is not None',
        'description': 'Function should return a value, not None',
    },
    'wrong_variable': {
        'patterns': [],  # Detected dynamically
        'check': 'source_contains',
        'assertion': 'assert correct_var in source',
        'description': 'Correct variable name should be used',
    },
    'operator_swap': {
        'patterns': ['==', '!=', '<', '>', 'and', 'or'],
        'check': 'source_contains',
        'assertion': 'assert "==" in source',  # Example
        'description': 'Correct operator should be used',
    },

    # Additional patterns
    'AutoWeightsLoader': {
        'patterns': ['AutoWeightsLoader'],
        'check': 'source_contains',
        'assertion': 'assert "AutoWeightsLoader" in source',
        'description': 'AutoWeightsLoader pattern check',
    },
    'AlreadyBorrowed': {
        'patterns': ['Already borrowed', 'num_tries', 'max_tries'],
        'check': 'source_contains',
        'assertion': 'assert "num_tries" in source or "max_tries" in source',
        'description': 'Retry logic for AlreadyBorrowed errors',
    },
    'deepcopy': {
        'patterns': ['copy.deepcopy'],
        'check': 'source_contains',
        'assertion': 'assert "copy.deepcopy" in source',
        'description': 'deepcopy for thread safety',
    },
    'logical_or_to_and': {
        'patterns': ['or', 'and'],
        'check': 'operator_check',
        'assertion': 'assert " or " in source',
        'description': 'Logical OR operator preserved',
    },
    'null_check_removal': {
        'patterns': ['is not None', 'is None'],
        'check': 'source_contains',
        'assertion': 'assert "is not None" in source',
        'description': 'Null check present',
    },
    'exception_handling': {
        'patterns': ['try:', 'except', 'finally:'],
        'check': 'source_contains',
        'assertion': 'assert "try:" in source and "except" in source',
        'description': 'Exception handling present',
    },
    'type_annotation': {
        'patterns': ['-> ', ': '],
        'check': 'source_contains',
        'assertion': 'assert "-> " in source',
        'description': 'Type annotation present',
    },
    'docstring_removal': {
        'patterns': ['"""', "'''"],
        'check': 'source_contains',
        'assertion': 'assert \'"""\' in source',
        'description': 'Docstring present',
    },
    'return_statement': {
        'patterns': ['return '],
        'check': 'source_contains',
        'assertion': 'assert "return " in source',
        'description': 'Return statement present',
    },
    'assertion_removal': {
        'patterns': ['assert '],
        'check': 'source_contains',
        'assertion': 'assert "assert " in source',
        'description': 'Assertion present',
    },
    'logging_removal': {
        'patterns': ['logger.', 'logging.'],
        'check': 'source_contains',
        'assertion': 'assert "logger." in source or "logging." in source',
        'description': 'Logging present',
    },
    'gpu_sync': {
        'patterns': ['.item()', '.cpu()', 'torch.cuda.synchronize'],
        'check': 'count_check',
        'assertion': 'gpu_sync_count <= threshold',
        'description': 'GPU synchronization count',
    },
    'caching': {
        'patterns': ['@lru_cache', '@cache', 'functools.lru_cache'],
        'check': 'source_contains',
        'assertion': 'assert "@lru_cache" in source or "@cache" in source',
        'description': 'Caching decorator present',
    },
    'validation_logic': {
        'patterns': ['if not', 'raise ValueError', 'raise TypeError', 'validate'],
        'check': 'source_contains',
        'assertion': 'assert "raise ValueError" in source or "raise TypeError" in source',
        'description': 'Validation logic present',
    },
}


def detect_bug_patterns(analysis: BugAnalysis) -> List[str]:
    """Detect which bug patterns apply to this patch."""
    detected = []
    all_removed = ' '.join(analysis.removed_lines)
    all_added = ' '.join(analysis.added_lines)

    for pattern_name, pattern_info in BUG_PATTERNS.items():
        score = 0
        for pattern in pattern_info['patterns']:
            if pattern in all_removed:
                score += 2
            if pattern in all_added:
                score -= 1

        if score > 0:
            detected.append(pattern_name)

    # Check for operator changes
    for old_line, new_line in zip(analysis.removed_lines, analysis.added_lines):
        op_change = ASTAnalyzer.detect_operator_changes(old_line, new_line)
        if op_change and op_change not in detected:
            detected.append(op_change)

    return detected


# =============================================================================
# CORE ANALYSIS FUNCTIONS
# =============================================================================

def analyze_bug_patch(instance_id: str, bug_patch: str, repo_path: Optional[str] = None,
                      original_code: Optional[str] = None, buggy_code: Optional[str] = None) -> BugAnalysis:
    """Enhanced bug patch analysis with AST support and diff analysis."""

    # Extract changed files
    changed_files = re.findall(r'diff --git a/([^\s]+)', bug_patch)

    # Extract removed and added lines
    removed_lines = []
    added_lines = []
    removed_functions = []
    removed_classes = []

    for line in bug_patch.split('\n'):
        if line.startswith('-') and not line.startswith('---'):
            content = line[1:].strip()
            removed_lines.append(content)

            # Look for function definitions
            func_match = re.match(r'def\s+(\w+)\s*\(', content)
            if func_match:
                removed_functions.append(func_match.group(1))

            # Look for class definitions
            class_match = re.match(r'class\s+(\w+)', content)
            if class_match:
                removed_classes.append(class_match.group(1))

        elif line.startswith('+') and not line.startswith('\+\+\+'):
            added_lines.append(line[1:].strip())

    # Determine key behavior change
    key_behavior_change = ""
    test_should_check = ""

    for line in removed_lines:
        for pattern_name, pattern_info in BUG_PATTERNS.items():
            for pattern in pattern_info['patterns']:
                if pattern in line:
                    key_behavior_change = pattern_info['description']
                    test_should_check = pattern_name
                    break

    # CRITICAL: If we have original and buggy code, analyze what bug was introduced
    bug_type_info = {
        "bug_type": "unknown",
        "original_behavior": "",
        "buggy_behavior": "",
        "how_to_detect": "",
        "specific_check": "",
    }
    if original_code and buggy_code:
        bug_type_info = analyze_what_bug_was_introduced(original_code, buggy_code)

    # Extract import info using improved detection
    module_path, class_name, method_name, is_method = extract_import_from_patch(bug_patch)

    # Fallback if extraction failed
    if not module_path and changed_files:
        main_file = changed_files[0]
        module_path = main_file.replace('/', '.').replace('.py', '')

    if not method_name and removed_functions:
        method_name = removed_functions[0]

    analysis = BugAnalysis(
        instance_id=instance_id,
        changed_files=changed_files,
        removed_lines=removed_lines,
        added_lines=added_lines,
        removed_functions=removed_functions,
        removed_classes=removed_classes,
        key_behavior_change=key_behavior_change,
        test_should_check=test_should_check,
        bug_type=bug_type_info.get("bug_type", "unknown"),
        original_behavior=bug_type_info.get("original_behavior", ""),
        buggy_behavior=bug_type_info.get("buggy_behavior", ""),
        how_to_detect=bug_type_info.get("how_to_detect", ""),
        specific_check=bug_type_info.get("specific_check", ""),
        is_method=is_method,
        class_name=class_name,
        method_name=method_name,
        module_path=module_path,
    )

    # Detect patterns
    analysis.bug_patterns = detect_bug_patterns(analysis)

    # Add bug_type to patterns if known
    if analysis.bug_type != "unknown" and analysis.bug_type not in analysis.bug_patterns:
        analysis.bug_patterns.insert(0, analysis.bug_type)

    # AST analysis if repo_path available
    if repo_path and changed_files:
        ast_data = {}
        for filepath in changed_files:
            full_path = Path(repo_path) / filepath
            if full_path.exists() and filepath.endswith('.py'):
                try:
                    with open(full_path) as f:
                        source = f.read()
                    tree = ASTAnalyzer.parse_module(source)
                    if tree:
                        ast_data[filepath] = {
                            'functions': ASTAnalyzer.extract_function_signatures(tree),
                            'classes': ASTAnalyzer.extract_class_structure(tree),
                            'imports': ASTAnalyzer.find_imports(tree),
                        }
                except Exception:
                    pass
        analysis.ast_analysis = ast_data

    return analysis


def extract_import_from_patch(bug_patch: str) -> Tuple[Optional[str], Optional[str], Optional[str], bool]:
    """Extract what module and attribute to import from the bug patch.
    Returns: (module_path, class_name, method_name, is_method)
    """
    code_files = re.findall(r'diff --git a/([^\s]+\.py)', bug_patch)

    if not code_files:
        return None, None, None, False

    # Filter to Python files, prefer source files over tests
    source_files = [f for f in code_files if not f.startswith('test') and not f.endswith('_test.py')]
    main_file = source_files[0] if source_files else code_files[0]

    # Convert file path to module path
    module_path = main_file.replace('/', '.').replace('.py', '')

    # Look for class definitions in the patch
    # Classes appear in:
    # - hunk headers: @@ ... @@ class ClassName:
    # - context lines:  class ClassName:
    # - removed lines: -class ClassName:
    class_pattern = r'(?:^@@.+@@\s+|^[ -]*)class\s+(\w+)'
    # Functions in removed lines (original code)
    func_pattern = r'^-[\s]*def\s+(\w+)'

    classes = []
    functions = []
    func_indents = {}  # Track indentation of each function

    for line in bug_patch.split('\n'):
        # Look for class definitions
        class_match = re.match(class_pattern, line)
        if class_match:
            classes.append(class_match.group(1))

        # Look for function definitions in removed lines
        func_match = re.match(func_pattern, line)
        if func_match:
            func_name = func_match.group(1)
            functions.append(func_name)
            # Calculate indentation
            stripped = line[1:]  # Remove '-' prefix
            indent = len(stripped) - len(stripped.lstrip())
            func_indents[func_name] = indent

    # Determine if it's a method
    is_method = False
    class_name = None
    method_name = None

    if functions:
        method_name = functions[0]
        indent = func_indents.get(method_name, 0)

        # If indented 4+ spaces, it's a method
        if indent >= 4:
            is_method = True

    # Assign class name if we found one and it's a method
    if is_method and classes:
        class_name = classes[0]
    elif classes and not functions:
        # Class-level change
        class_name = classes[0]
        is_method = False

    return module_path, class_name, method_name, is_method


# =============================================================================
# PHASE 2: LLM INTEGRATION FOR EDGE CASES
# =============================================================================

class LLMTestGenerator:
    """Generate tests using LLM for complex edge cases."""

    SYSTEM_PROMPT = """You are an expert Python test engineer specializing in detecting regression bugs.
Your task is to generate a pytest test that detects a specific bug.

Rules:
1. The test MUST use `inspect.getsource()` to check code patterns when possible
2. The test should NOT require heavy imports or GPU resources
3. The test should be deterministic
4. Use environment variables to mock hardware if needed (e.g., VLLM_TARGET_DEVICE=cpu)
5. Return ONLY the test code, no explanations

CRITICAL: If testing a method (function inside a class), import the CLASS, not the method directly.
Example: `from vllm.config.model import ModelConfig` then use `inspect.getsource(ModelConfig.compute_hash)`

The test must:
- PASS when the bug is NOT present (gold state)
- FAIL when the bug IS present (buggy state)"""

    def __init__(self, model: str = "openai/kimi-latest", api_key: Optional[str] = None):
        if not HAS_LITELLM:
            raise ImportError("litellm is required for LLM test generation")

        self.model = model
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        self.cache: Dict[str, str] = {}

    def _build_prompt(self, analysis: BugAnalysis, context: str = "") -> str:
        """Build a prompt for the LLM."""

        removed_str = '\n'.join(analysis.removed_lines[:20])
        added_str = '\n'.join(analysis.added_lines[:20])

        # Determine correct import
        if analysis.is_method and analysis.class_name:
            import_stmt = f"from {analysis.module_path} import {analysis.class_name}"
            inspect_target = f"{analysis.class_name}.{analysis.method_name}"
        else:
            import_stmt = f"from {analysis.module_path} import {analysis.method_name or 'TARGET'}"
            inspect_target = analysis.method_name or "TARGET"

        context_section = f"CONTEXT:\n{context}" if context else ""

        prompt = f"""
Generate a pytest test to detect this bug.

INSTANCE ID: {analysis.instance_id}
BUG TYPE: {analysis.bug_type}
BUGGY BEHAVIOR: {analysis.buggy_behavior}
HOW TO DETECT: {analysis.how_to_detect}
SPECIFIC CHECK: {analysis.specific_check}

IMPORT (use this EXACTLY):
```python
{import_stmt}
import inspect
```

INSPECT TARGET: {inspect_target}

REMOVED CODE (gold):
```python
{removed_str}
```

ADDED CODE (buggy):
```python
{added_str}
```

{context_section}

Generate a test that:
1. Uses the import statement provided above
2. Uses `inspect.getsource({inspect_target})` to get source code
3. Checks for the bug using: {analysis.specific_check or 'appropriate assertions'}
4. Will FAIL when the buggy code is present
5. Is minimal and doesn't require GPU/hardware

Return ONLY the test code (function definitions starting with test_).
"""
        return prompt

    def generate_test(self, analysis: BugAnalysis, context: str = "") -> Optional[List[str]]:
        """Generate test using LLM with caching."""

        if not self.api_key:
            return None

        # Build cache key
        cache_key = hashlib.sha256(
            f"{analysis.instance_id}:{analysis.bug_type}".encode()
        ).hexdigest()

        if cache_key in self.cache:
            return self.cache[cache_key].split('\n')

        prompt = self._build_prompt(analysis, context)

        try:
            response = completion(
                model=self.model,
                messages=[
                    {"role": "system", "content": self.SYSTEM_PROMPT},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.2,
                max_tokens=1000,
            )

            test_code = response.choices[0].message.content

            # Clean up the response
            test_code = self._clean_response(test_code)

            # Cache the result
            self.cache[cache_key] = test_code

            return test_code.split('\n')

        except Exception as e:
            print(f"LLM generation failed: {e}")
            return None

    def _clean_response(self, response: str) -> str:
        """Clean up LLM response."""
        if "```python" in response:
            response = response.split("```python", 1)[1]
        if "```" in response:
            response = response.rsplit("```", 1)[0]
        return response.strip()


# =============================================================================
# TEST GENERATION ENGINE
# =============================================================================

def get_module_and_attr(analysis: BugAnalysis) -> Tuple[Optional[str], Optional[str], Optional[str], bool]:
    """Extract module, class_name, method_name, and whether it's a method."""
    if not analysis.changed_files:
        return None, None, None, False

    py_files = [f for f in analysis.changed_files if f.endswith('.py')]
    if not py_files:
        return None, None, None, False

    main_file = py_files[0]
    module_path = main_file.replace('/', '.').replace('.py', '')

    # Use analysis info if available (from improved extraction)
    if analysis.is_method and analysis.class_name:
        return module_path, analysis.class_name, analysis.method_name, True

    if analysis.removed_functions:
        # Check if we detected it's a method
        if analysis.is_method and analysis.class_name:
            return module_path, analysis.class_name, analysis.method_name, True
        return module_path, None, analysis.method_name or analysis.removed_functions[0], False
    elif analysis.removed_classes:
        return module_path, analysis.removed_classes[0], None, False
    else:
        file_basename = Path(main_file).stem
        potential_class = ''.join(word.capitalize() for word in file_basename.split('_'))
        return module_path, potential_class, None, False


def generate_test_from_template(analysis: BugAnalysis, template_name: str) -> Optional[TestTemplate]:
    """Generate a test from a predefined template with CORRECT imports."""

    suffix = analysis.instance_id.split('.')[-1]
    module, class_name, method_name, is_method = get_module_and_attr(analysis)

    if not module:
        return None

    # Get function/method name - prefer extracted method_name
    func_name = method_name or analysis.method_name or class_name

    # Handle bug-type-specific patterns
    if analysis.bug_type == "not_implemented":
        if is_method and analysis.class_name:
            return TestTemplate(
                name=f"test_{func_name}_is_implemented",
                description=f"Test that {analysis.class_name}.{func_name} is properly implemented, not just pass/TODO",
                imports=[f"from {module} import {analysis.class_name}", "import inspect"],
                setup_code=[],
                test_body=[
                    f'    """Test that {func_name} is properly implemented."""',
                    f'    source = inspect.getsource({analysis.class_name}.{func_name})',
                    '    assert "TODO" not in source, "Bug: TODO found in code"',
                    '    assert source.count("pass") < 2, "Bug: Multiple pass statements (likely stub)"',
                    '    # Check it actually does something',
                    '    lines = [l.strip() for l in source.split("\\n") if l.strip() and not l.strip().startswith("#")]',
                    '    assert len(lines) > 3, f"Bug: Function too short ({len(lines)} lines), likely just pass"',
                ],
                fail_to_pass_tests=[f"tests/bugs/test_{func_name}_is_implemented.py::test_{func_name}_is_implemented"],
            )
        else:
            return TestTemplate(
                name=f"test_{func_name}_is_implemented",
                description=f"Test that {func_name} is properly implemented",
                imports=[f"from {module} import {func_name}", "import inspect"],
                setup_code=[],
                test_body=[
                    f'    """Test that {func_name} is properly implemented."""',
                    f'    source = inspect.getsource({func_name})',
                    '    assert "TODO" not in source, "Bug: TODO found in code"',
                    '    assert source.count("pass") < 2, "Bug: Multiple pass statements"',
                ],
                fail_to_pass_tests=[f"tests/bugs/test_{func_name}_is_implemented.py::test_{func_name}_is_implemented"],
            )

    elif analysis.bug_type == "returns_none":
        if is_method and analysis.class_name:
            return TestTemplate(
                name=f"test_{func_name}_returns_value",
                description=f"Test that {func_name} returns a value, not None",
                imports=[f"from {module} import {analysis.class_name}", "import pytest"],
                setup_code=[],
                test_body=[
                    f'    """Test that {func_name} returns a value."""',
                    f'    try:',
                    f'        instance = {analysis.class_name}()',
                    f'    except Exception as e:',
                    f'        pytest.skip(f"Cannot instantiate: {{e}}")',
                    f'    result = instance.{func_name}()',
                    f'    assert result is not None, "Bug: {func_name} returns None"',
                    f'    assert result != "", "Bug: {func_name} returns empty string"',
                ],
                fail_to_pass_tests=[f"tests/bugs/test_{func_name}_returns_value.py::test_{func_name}_returns_value"],
            )

    elif analysis.bug_type == "operator_swap":
        if is_method and analysis.class_name:
            specific_check = analysis.specific_check
            return TestTemplate(
                name=f"test_{func_name}_correct_operator",
                description=f"Test that {func_name} uses correct logical operators",
                imports=[f"from {module} import {analysis.class_name}", "import inspect"],
                setup_code=[],
                test_body=[
                    f'    """Test that {func_name} uses correct operators."""',
                    f'    source = inspect.getsource({analysis.class_name}.{func_name})',
                    '    # Bug: operator was swapped',
                    f'    {specific_check if specific_check else "assert True  # Check manually"}',
                ],
                fail_to_pass_tests=[f"tests/bugs/test_{func_name}_correct_operator.py::test_{func_name}_correct_operator"],
            )

    elif analysis.bug_type == "wrong_variable":
        if is_method and analysis.class_name:
            correct_var = analysis.original_behavior.replace("Used correct variable '", "").replace("'", "")
            return TestTemplate(
                name=f"test_{func_name}_uses_correct_variables",
                description=f"Test that {func_name} uses correct variable names",
                imports=[f"from {module} import {analysis.class_name}", "import inspect"],
                setup_code=[],
                test_body=[
                    f'    """Test that {func_name} uses correct variables."""',
                    f'    source = inspect.getsource({analysis.class_name}.{func_name})',
                    f'    assert "{correct_var}" in source, "Bug: Should use {correct_var}"',
                ],
                fail_to_pass_tests=[f"tests/bugs/test_{func_name}_uses_correct_variables.py::test_{func_name}_uses_correct_variables"],
            )

    # Handle named patterns
    if template_name == 'AutoWeightsLoader':
        if is_method and analysis.class_name:
            return TestTemplate(
                name=f"test_{func_name}_uses_autoweightsloader",
                description=f"Test that {func_name} uses AutoWeightsLoader pattern",
                imports=[f"from {module} import {analysis.class_name}", "import inspect"],
                setup_code=[],
                test_body=[
                    f'    """Test AutoWeightsLoader pattern."""',
                    f'    source = inspect.getsource({analysis.class_name}.{func_name})',
                    '    assert "AutoWeightsLoader" in source, "AutoWeightsLoader pattern missing"',
                ],
                fail_to_pass_tests=[f"tests/bugs/test_{func_name}_uses_autoweightsloader.py::test_{func_name}_uses_autoweightsloader"],
            )

    elif template_name == 'AlreadyBorrowed':
        if is_method and analysis.class_name:
            return TestTemplate(
                name=f"test_{func_name}_has_retry_logic",
                description=f"Test that {func_name} has retry logic for AlreadyBorrowed errors",
                imports=[f"from {module} import {analysis.class_name}", "import inspect"],
                setup_code=[],
                test_body=[
                    f'    """Test retry logic."""',
                    f'    source = inspect.getsource({analysis.class_name}.{func_name})',
                    '    assert "num_tries" in source or "max_tries" in source, "Retry logic missing"',
                ],
                fail_to_pass_tests=[f"tests/bugs/test_{func_name}_has_retry_logic.py::test_{func_name}_has_retry_logic"],
            )

    elif template_name == 'null_check_removal':
        if is_method and analysis.class_name:
            return TestTemplate(
                name=f"test_{func_name}_has_null_checks",
                description=f"Test that {func_name} has proper null checks",
                imports=[f"from {module} import {analysis.class_name}", "import inspect"],
                setup_code=[],
                test_body=[
                    f'    """Test null checks."""',
                    f'    source = inspect.getsource({analysis.class_name}.{func_name})',
                    '    assert "is not None" in source, "Null check missing"',
                ],
                fail_to_pass_tests=[f"tests/bugs/test_{func_name}_has_null_checks.py::test_{func_name}_has_null_checks"],
            )

    elif template_name == 'logical_or_to_and':
        if is_method and analysis.class_name:
            return TestTemplate(
                name=f"test_{func_name}_logical_operators",
                description=f"Test that {func_name} uses correct logical operators",
                imports=[f"from {module} import {analysis.class_name}", "import inspect"],
                setup_code=[],
                test_body=[
                    f'    """Test logical operators."""',
                    f'    source = inspect.getsource({analysis.class_name}.{func_name})',
                    '    # Bug changes OR to AND - check that OR is still present',
                    '    or_count = source.count(" or ")',
                    '    assert or_count > 0, "OR operator missing (may have been changed to AND)"',
                ],
                fail_to_pass_tests=[f"tests/bugs/test_{func_name}_logical_operators.py::test_{func_name}_logical_operators"],
            )

    return None


def validate_patched_syntax(file_path: str, patch_content: str) -> Tuple[bool, Optional[str]]:
    """
    Validate that applying a patch results in syntactically valid code.
    This catches indentation errors that occur when the patch is applied.
    """
    import tempfile
    import subprocess

    # Create a temp copy of the file
    with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False) as f:
        temp_path = f.name
        try:
            with open(file_path, 'r') as orig:
                f.write(orig.read())
        except Exception as e:
            return False, f"Could not read original file: {e}"

    try:
        # Try to apply the patch using git apply
        result = subprocess.run(
            ['git', 'apply', '-'],
            cwd=tempfile.gettempdir(),
            input=patch_content,
            capture_output=True,
            text=True
        )

        if result.returncode != 0:
            return False, f"Patch apply failed: {result.stderr[:200]}"

        # Read the patched content
        with open(temp_path, 'r') as f:
            patched_content = f.read()

        # Validate Python syntax
        try:
            ast.parse(patched_content)
            return True, None
        except SyntaxError as e:
            return False, f"Syntax error in patched file: {e}"

    finally:
        # Cleanup
        import os
        try:
            os.unlink(temp_path)
        except:
            pass


def generate_targeted_test(
    analysis: BugAnalysis,
    use_llm: bool = False,
    llm_generator: Optional[LLMTestGenerator] = None,
) -> Optional[Tuple[List[str], List[str]]]:
    """Generate a targeted test using multiple strategies.
    Returns: (test_lines, fail_to_pass_tests)
    """

    suffix = analysis.instance_id.split('.')[-1]

    # Try template-based generation first based on bug_type
    if analysis.bug_type != "unknown":
        template = generate_test_from_template(analysis, analysis.bug_type)
        if template:
            lines = ['"""Test for bug detection - auto-generated."""']
            lines.extend(template.imports)
            lines.append('')
            lines.extend(template.setup_code)
            lines.append('')
            lines.append(f'def {template.name}():')
            lines.extend(template.test_body)
            return lines, template.fail_to_pass_tests

    # Try pattern-based templates
    for pattern in analysis.bug_patterns:
        template = generate_test_from_template(analysis, pattern)
        if template:
            lines = ['"""Test for bug detection - auto-generated."""']
            lines.extend(template.imports)
            lines.append('')
            lines.extend(template.setup_code)
            lines.append('')
            lines.append(f'def {template.name}():')
            lines.extend(template.test_body)
            return lines, template.fail_to_pass_tests

    # Fall back to LLM if available
    if use_llm and llm_generator and HAS_LITELLM:
        llm_result = llm_generator.generate_test(analysis)
        if llm_result:
            # Extract test name for fail_to_pass
            test_name = None
            for line in llm_result:
                match = re.match(r'def\s+(test_\w+)\s*\(', line)
                if match:
                    test_name = match.group(1)
                    break
            if test_name:
                fail_to_pass = [f"tests/bugs/{test_name}.py::{test_name}"]
            else:
                fail_to_pass = [f"tests/bugs/test_bug_{suffix}.py::test_bug_detected"]
            return llm_result, fail_to_pass

    # Last resort: generic test
    return generate_generic_test(analysis)


def generate_p2p_tests(analysis: BugAnalysis) -> Optional[Dict]:
    """Generate P2P tests that pass in both buggy and fixed states."""
    module, class_name, method_name, is_method = get_module_and_attr(analysis)
    if not module:
        return None

    p2p_code = []
    p2p_tests = []

    if is_method and class_name:
        # P2P: Test that class can be imported and instantiated
        test_name = f"test_{class_name.lower()}_can_be_imported"
        p2p_code.extend([
            f'def {test_name}():',
            f'    """P2P: Verify {class_name} can be imported (smoke test)."""',
            f'    from {module} import {class_name}',
            f'    assert {class_name} is not None',
        ])
        p2p_tests.append(f"tests/bugs/test_{class_name.lower()}_p2p.py::{test_name}")

        # P2P: Test basic class structure
        test_name = f"test_{class_name.lower()}_has_required_attributes"
        p2p_code.extend([
            '',
            f'def {test_name}():',
            f'    """P2P: Verify {class_name} has basic structure."""',
            f'    from {module} import {class_name}',
            f'    # Basic sanity checks that pass in both buggy and fixed states',
            f'    assert hasattr({class_name}, "__doc__")',
            f'    assert callable({class_name}) or hasattr({class_name}, "__init__")',
        ])
        p2p_tests.append(f"tests/bugs/test_{class_name.lower()}_p2p.py::{test_name}")

    else:
        # P2P for functions/modules
        import_name = method_name or class_name or "target"
        test_name = f"test_{import_name.lower()}_can_be_imported"
        p2p_code.extend([
            f'def {test_name}():',
            f'    """P2P: Verify {import_name} can be imported (smoke test)."""',
            f'    from {module} import {import_name}',
            f'    assert {import_name} is not None',
        ])
        p2p_tests.append(f"tests/bugs/test_{import_name.lower()}_p2p.py::{test_name}")

    return {'code': p2p_code, 'tests': p2p_tests}


def generate_generic_test(analysis: BugAnalysis) -> Optional[Tuple[List[str], List[str]]]:
    """Generate a generic test when specific patterns don't match."""

    module, class_name, method_name, is_method = get_module_and_attr(analysis)
    if not module:
        return None

    # Determine what to import and test
    if is_method and class_name:
        import_name = class_name
        test_target = f"{class_name}.{method_name}" if method_name else class_name
    else:
        import_name = method_name or class_name
        test_target = import_name

    suffix = analysis.instance_id.split('.')[-1]

    if is_method and analysis.class_name and analysis.method_name:
        # Method test - import class
        lines = [
            '"""Test for bug detection - auto-generated."""',
            'import pytest',
            'import inspect',
            '',
            f'from {module} import {analysis.class_name}',
            '',
            '',
            f'def test_{analysis.method_name}_not_degraded():',
            f'    """Test that {analysis.method_name} maintains expected functionality."""',
            f'    source = inspect.getsource({analysis.class_name}.{analysis.method_name})',
            '    # Basic structural checks',
            '    assert len(source) > 50, "Source code suspiciously short"',
            '    assert source.count("def ") > 0, "No function definition found"',
        ]
        fail_to_pass = [f"tests/bugs/test_{analysis.method_name}_not_degraded_{suffix}.py::test_{analysis.method_name}_not_degraded"]
    else:
        # Function test or fallback
        test_name = (method_name or class_name or "bug").lower()
        lines = [
            '"""Test for bug detection - auto-generated."""',
            'import pytest',
            'import inspect',
            '',
            f'from {module} import {import_name}',
            '',
            '',
            f'def test_{test_name}_not_degraded():',
            f'    """Test that {import_name} maintains expected functionality."""',
            f'    assert {import_name} is not None',
            f'    if inspect.isclass({import_name}):',
            f'        assert hasattr({import_name}, "__init__"), "Class missing __init__"',
            f'    elif inspect.isfunction({import_name}):',
            f'        sig = inspect.signature({import_name})',
            f'        assert len(sig.parameters) > 0, "Function has no parameters"',
            '',
            '',
            f'def test_{test_name}_structure_intact():',
            f'    """Test that {import_name} structure is not degraded."""',
            f'    source = inspect.getsource({import_name})',
            '    # Basic structural checks',
            '    assert len(source) > 50, "Source code suspiciously short"',
            '    assert source.count("def ") > 0 or source.count("class ") > 0, "No definitions found"',
        ]
        fail_to_pass = [
            f"tests/bugs/test_{test_name}_not_degraded_{suffix}.py::test_{test_name}_not_degraded",
            f"tests/bugs/test_{test_name}_not_degraded_{suffix}.py::test_{test_name}_structure_intact",
        ]

    return lines, fail_to_pass


# =============================================================================
# PHASE 3: VALIDATION PIPELINE
# =============================================================================

class TestValidator:
    """Validate generated tests for correctness."""

    @staticmethod
    def validate_syntax(test_lines: List[str]) -> Tuple[bool, Optional[str]]:
        """Check if test code is syntactically valid Python."""
        try:
            code = '\n'.join(test_lines)
            ast.parse(code)
            return True, None
        except SyntaxError as e:
            return False, str(e)

    @staticmethod
    def validate_imports(test_lines: List[str]) -> Tuple[bool, List[str]]:
        """Check if all imports can be resolved (basic check)."""
        code = '\n'.join(test_lines)
        tree = ast.parse(code)

        imports_to_check = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports_to_check.append(alias.name.split('.')[0])
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imports_to_check.append(node.module.split('.')[0])

        stdlib_modules = {'os', 'sys', 'inspect', 'json', 're', 'typing', 'pathlib', 'functools', 'pytest'}

        missing = []
        for imp in imports_to_check:
            if imp in stdlib_modules:
                continue
            try:
                __import__(imp)
            except ImportError:
                missing.append(imp)

        return len(missing) == 0, missing

    @staticmethod
    def format_test_patch(filepath: str, content_lines: List[str]) -> str:
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

    @staticmethod
    def fix_common_issues(test_lines: List[str]) -> List[str]:
        """Fix common issues in generated tests."""
        fixed = []

        for line in test_lines:
            # Fix missing parentheses in assertions
            if 'assert ' in line and not line.strip().endswith(')'):
                if line.count('(') != line.count(')'):
                    line = line + ')'

            # Fix common typos
            line = line.replace('assrt ', 'assert ')
            line = line.replace('imoprt ', 'import ')
            line = line.replace('frm ', 'from ')

            fixed.append(line)

        return fixed


def determine_test_filepath(analysis: BugAnalysis) -> str:
    """Determine appropriate test file path based on changed files."""

    suffix = analysis.instance_id.split('.')[-1]
    func_name = analysis.method_name or "bug"

    return f"tests/bugs/test_{func_name}_{suffix}.py"


# =============================================================================
# MAIN ENTRY POINT
# =============================================================================

def process_instances(
    instances_path: str,
    use_llm: bool = False,
    validate: bool = False,
    repo_path: Optional[str] = None,
    output_path: Optional[str] = None,
):
    """Process all instances and generate test patches."""

    with open(instances_path, 'r') as f:
        instances = json.load(f)

    print("=" * 80)
    print("ENHANCED TARGETED TEST GENERATION")
    print("=" * 80)
    print(f"Total instances: {len(instances)}")
    print(f"LLM enabled: {use_llm}")
    print(f"Validation enabled: {validate}")
    print("=" * 80)

    # Initialize LLM generator if requested
    llm_generator = None
    if use_llm and HAS_LITELLM:
        try:
            llm_generator = LLMTestGenerator()
            print(f"✅ LLM generator initialized (model: {llm_generator.model})")
        except Exception as e:
            print(f"⚠️  Failed to initialize LLM generator: {e}")
            llm_generator = None
    elif use_llm and not HAS_LITELLM:
        print("⚠️  litellm not installed, LLM features disabled")
        print("    Install with: pip install litellm")

    validator = TestValidator()

    stats = {
        'generated': 0,
        'template_based': 0,
        'llm_based': 0,
        'generic': 0,
        'syntax_errors': 0,
        'import_errors': 0,
        'fixed_auto': 0,
        'failed': 0,
        'f2p_tests': 0,
    }

    for i, inst in enumerate(instances):
        instance_id = inst.get('instance_id', f'unknown_{i}')
        suffix = instance_id.split('.')[-1]
        bug_patch = inst.get('bug_patch', '') or inst.get('patch', '')

        print(f"\n[{i+1}/{len(instances)}] Processing {suffix}...")

        if not bug_patch:
            print(f"   ⚠️  No bug patch found")
            stats['failed'] += 1
            continue

        # Get original and buggy code if available
        original_code = inst.get('original_code', '')
        buggy_code = inst.get('buggy_code', '')

        # Analyze the bug
        analysis = analyze_bug_patch(instance_id, bug_patch, repo_path, original_code, buggy_code)

        print(f"   Changed files: {len(analysis.changed_files)}")
        print(f"   Bug type: {analysis.bug_type}")
        print(f"   Bug patterns: {', '.join(analysis.bug_patterns) or 'None detected'}")
        print(f"   Is method: {analysis.is_method} (class: {analysis.class_name})")

        # Generate test
        result = generate_targeted_test(analysis, use_llm=use_llm, llm_generator=llm_generator)

        if not result:
            print(f"   ❌ Failed to generate test")
            stats['failed'] += 1
            continue

        test_lines, fail_to_pass = result

        # Track generation method
        if analysis.bug_type != "unknown":
            stats['template_based'] += 1
        elif use_llm and llm_generator:
            stats['llm_based'] += 1
        else:
            stats['generic'] += 1

        # Validate if requested
        if validate:
            # Check syntax
            is_valid, error = validator.validate_syntax(test_lines)
            if not is_valid:
                print(f"   ⚠️  Syntax error: {error}")
                stats['syntax_errors'] += 1

                # Try to fix
                test_lines = validator.fix_common_issues(test_lines)
                is_valid_fixed, _ = validator.validate_syntax(test_lines)
                if is_valid_fixed:
                    print(f"   🔧 Auto-fixed syntax issues")
                    stats['fixed_auto'] += 1
                else:
                    print(f"   ❌ Could not auto-fix")
                    stats['failed'] += 1
                    continue

        # Determine test file path
        test_filepath = determine_test_filepath(analysis)
        print(f"   📄 Test file: {test_filepath}")
        print(f"   ✅ F2P tests: {len(fail_to_pass)}")

        # Generate P2P tests (pass in both buggy and fixed states)
        p2p_result = generate_p2p_tests(analysis)

        # Combine F2P and P2P test code
        if p2p_result:
            test_lines.extend(['', ''])  # Separator
            test_lines.extend(p2p_result['code'])

        # Create test patch
        test_patch = validator.format_test_patch(test_filepath, test_lines)
        inst['test_patch'] = test_patch
        inst['FAIL_TO_PASS'] = fail_to_pass  # Only F2P tests
        inst['PASS_TO_PASS'] = p2p_result.get('tests', []) if p2p_result else []  # Only P2P tests
        inst['test_generation_meta'] = {
            'patterns_detected': analysis.bug_patterns,
            'bug_type': analysis.bug_type,
            'method': 'template' if analysis.bug_type != "unknown" else ('llm' if use_llm and llm_generator else 'generic'),
            'files_changed': analysis.changed_files,
            'is_method': analysis.is_method,
            'class_name': analysis.class_name,
        }

        stats['generated'] += 1
        stats['f2p_tests'] += len(fail_to_pass)
        print(f"   ✅ Generated test ({stats['generated']} total)")

    # Save results
    output = output_path or instances_path
    with open(output, 'w') as f:
        json.dump(instances, f, indent=2)

    # Print summary
    print("\n" + "=" * 80)
    print("SUMMARY")
    print("=" * 80)
    print(f"Tests generated: {stats['generated']}/{len(instances)}")
    print(f"  - Template-based: {stats['template_based']}")
    print(f"  - LLM-based: {stats['llm_based']}")
    print(f"  - Generic: {stats['generic']}")
    print(f"Syntax errors: {stats['syntax_errors']} (auto-fixed: {stats['fixed_auto']})")
    print(f"Failed: {stats['failed']}")
    print(f"Total F2P tests: {stats['f2p_tests']}")
    print(f"\nOutput saved to: {output}")

    return stats


def main():
    parser = argparse.ArgumentParser(description="Enhanced targeted test generation")
    parser.add_argument(
        "--instances",
        type=str,
        default=DEFAULT_INSTANCES_PATH,
        help="Path to instances JSON file",
    )
    parser.add_argument(
        "--use-llm",
        action="store_true",
        help="Enable LLM-based test generation for edge cases",
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help="Validate generated tests for syntax correctness",
    )
    parser.add_argument(
        "--repo-path",
        type=str,
        default=VLLM_REPO,
        help="Path to repository for AST analysis",
    )
    parser.add_argument(
        "--output",
        type=str,
        help="Output path (defaults to input path)",
    )
    args = parser.parse_args()

    process_instances(
        instances_path=args.instances,
        use_llm=args.use_llm,
        validate=args.validate,
        repo_path=args.repo_path,
        output_path=args.output,
    )


if __name__ == "__main__":
    main()
