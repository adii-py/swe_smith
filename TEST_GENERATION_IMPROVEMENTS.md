# Enhanced Test Patch Generation - Implementation Summary

This document describes the 3-phase improvement to test patch generation in SWE-Smith.

## Files Created/Modified

| File | Description |
|------|-------------|
| `design_targeted_tests_v2.py` | **New** - Enhanced test generation with all 3 phases |
| `demo_enhanced_tests.py` | **New** - Demonstration of improvements |
| `design_targeted_tests.py` | Original (kept for reference) |

---

## Phase 1: Enhanced Static Analysis with AST Parsing

### What Was Added

#### 1. AST Analyzer Class
```python
class ASTAnalyzer:
    - parse_module(source)          # Parse Python source safely
    - extract_function_signatures(tree)  # Get function params, decorators
    - extract_class_structure(tree)      # Get class methods, bases
    - find_imports(tree)                 # Extract all imports
    - detect_operator_changes(old, new)  # Detect operator mutations
```

#### 2. Expanded Pattern Library (15+ Patterns)

| Pattern | Description | Detection Method |
|---------|-------------|------------------|
| `AutoWeightsLoader` | Import/usage removal | String match |
| `AlreadyBorrowed` | Retry logic removal | String match |
| `deepcopy` | Thread safety removal | String match |
| `logical_or_to_and` | Boolean operator mutation | AST + regex |
| `parameter_order_swap` | Function arg reordering | Position analysis |
| `null_check_removal` | Null safety removal | String match |
| `exception_handling` | Try/except removal | String match |
| `type_annotation` | Type hint removal | String match |
| `gpu_sync` | GPU synchronization | Count analysis |
| `thread_safety` | Lock/semaphore removal | String match |
| `caching` | Decorator removal | String match |
| `validation_logic` | Input validation removal | String match |
| `assertion_removal` | Assert statement removal | String match |
| `logging_removal` | Logger removal | String match |
| `docstring_removal` | Documentation removal | String match |

### Usage

```python
from design_targeted_tests_v2 import analyze_bug_patch

analysis = analyze_bug_patch(
    instance_id="vllm-project__vllm.1234",
    bug_patch=patch_content,
    repo_path="/path/to/repo"  # Optional, for AST analysis
)

print(analysis.bug_patterns)  # ['logical_or_to_and', 'null_check_removal']
```

---

## Phase 2: LLM Integration for Edge Cases

### What Was Added

#### LLM Test Generator Class
```python
class LLMTestGenerator:
    - __init__(model, api_key)      # Configure LLM
    - generate_test(analysis)       # Generate test via LLM
    - _build_prompt(analysis)       # Construct optimized prompt
    - _clean_response(response)     # Parse LLM output
```

### Features

1. **Smart Prompting**: Builds context-rich prompts with:
   - Bug patch (removed vs added code)
   - Detected patterns
   - Surrounding context (±30 lines)
   - Expected test characteristics

2. **Caching**: `@lru_cache` prevents redundant API calls

3. **Fallback Strategy**: Only used when templates don't match

### Configuration

```bash
# Set API key
export OPENAI_API_KEY=sk-...

# Install dependency
pip install litellm

# Usage
python design_targeted_tests_v2.py --instances data.json --use-llm
```

### Example Prompt Built by System

```
Generate a pytest test to detect this bug.

INSTANCE ID: vllm-project__vllm.3e1ad443.composite_7613
CHANGED FILES: setup.py
BUG PATTERNS: logical_or_to_and, parameter_order_swap

REMOVED CODE:
```python
if _is_cuda() or _is_hip():
```

ADDED CODE (buggy):
```python
if _is_cuda() and _is_hip():
```

Generate a test that:
1. Imports the necessary module(s): setup, get_rocm_version
2. Uses inspection to check for the presence of key code patterns
3. Will FAIL when the buggy code is present
4. Is minimal and doesn't require GPU/hardware

Return ONLY the test code.
```

---

## Phase 3: Validation Pipeline

### What Was Added

#### Test Validator Class
```python
class TestValidator:
    - validate_syntax(test_lines)       # AST-based syntax check
    - validate_imports(test_lines)      # Import resolution check
    - format_test_patch(path, lines)    # Create git diff format
    - fix_common_issues(test_lines)     # Auto-fix typos/formatting
```

### Features

1. **Syntax Validation**: Parses generated code with `ast.parse()`
2. **Auto-Fix**: Corrects common issues:
   - Missing parentheses in assertions
   - Typos (`assrt` → `assert`)
   - Import statement errors
3. **Git Diff Formatting**: Properly formats patches with headers

### Usage

```python
validator = TestValidator()

# Validate
test_lines = generate_test(...)
is_valid, error = validator.validate_syntax(test_lines)

# Auto-fix if needed
if not is_valid:
    test_lines = validator.fix_common_issues(test_lines)

# Create patch
test_patch = validator.format_test_patch("tests/test_bug.py", test_lines)
```

---

## Complete Usage Examples

### 1. Basic (Templates Only)

```python
from design_targeted_tests_v2 import process_instances

process_instances(
    instances_path="instances.json",
    use_llm=False,
    validate=True
)
```

### 2. With LLM Fallback

```python
process_instances(
    instances_path="instances.json",
    use_llm=True,          # Enable LLM for edge cases
    validate=True,
    repo_path="/path/to/vllm"
)
```

### 3. Command Line

```bash
# Basic
python design_targeted_tests_v2.py --instances instances.json

# Full featured
python design_targeted_tests_v2.py \
  --instances instances.json \
  --use-llm \
  --validate \
  --repo-path /path/to/vllm \
  --output instances_with_tests.json
```

---

## Comparison: Old vs New

| Aspect | Old System | New System |
|--------|-----------|------------|
| **Patterns** | 6 hardcoded | 15+ extensible |
| **Detection** | Regex only | AST + semantic |
| **Operators** | Not detected | Full mutation detection |
| **Edge Cases** | Silent failure | LLM fallback |
| **Validation** | None | Syntax + auto-fix |
| **Extensibility** | Edit source | Template-based |
| **Coverage** | Specific bugs | Generic + specific |

---

## Architecture Flow

```
┌─────────────────┐
│  Bug Patch In   │
└────────┬────────┘
         ▼
┌─────────────────────┐
│  Phase 1: Analysis  │
│  - Extract changes  │
│  - AST parsing      │
│  - Pattern matching │
└────────┬────────────┘
         ▼
┌─────────────────────┐
│  Has Matching       │
│  Template?          │
└────────┬────────────┘
    Yes /   \ No
        ▼     ▼
┌──────────┐ ┌──────────────┐
│ Use      │ │ Phase 2:     │
│ Template │ │ LLM Fallback │
└────┬─────┘ └──────┬───────┘
     │              │
     └──────┬───────┘
            ▼
┌─────────────────────┐
│  Phase 3: Validate  │
│  - Syntax check     │
│  - Auto-fix         │
│  - Format patch     │
└────────┬────────────┘
         ▼
┌─────────────────┐
│  Test Patch Out │
└─────────────────┘
```

---

## Demo Output

Running `python demo_enhanced_tests.py` demonstrates the system on a composite bug:

```
DEMO: Enhanced Test Generation Analysis
========================================
Input: Composite bug patch with two changes:
  1. Logical OR → AND change
  2. Parameter order swap in function call

PHASE 1: Enhanced Static Analysis
----------------------------------
📁 Changed files: ['setup.py']
📊 Removed lines: 2
📊 Added lines: 2
🔍 Detected bug patterns:
   • parameter_order_swap
   • logical_or_to_and

AST Analysis - Operator Changes
----------------------------------
✓ Detected: logical_or_to_and
  Old: if _is_cuda() or _is_hip():...
  New: if _is_cuda() and _is_hip():...

PHASE 2: Test Generation
----------------------------------
✅ Generated test:

  1| """Test for bug detection - auto-generated."""
  2| from setup import Setup
  3|
  4| import inspect
  5|
  6| def test_setup_logical_operators():
  7|     """Test that Setup uses correct logical operators."""
  8|     source = inspect.getsource(Setup)
  9|     # Bug changes OR to AND - check that OR is still present
 10|     or_count = source.count(" or ")
 11|     and_count = source.count(" and ")
 12|     assert or_count > 0, "OR operator missing"

PHASE 3: Validation
----------------------------------
✅ Syntax validation: PASSED
✅ Generated patch length: 625 characters
```

---

## Future Extensions

1. **Coverage-Guided Generation**: Use code coverage to ensure tests hit buggy lines
2. **Multi-File Bugs**: Handle bugs spanning multiple files better
3. **Integration Tests**: Generate integration tests, not just unit tests
4. **Property-Based Tests**: Use Hypothesis for fuzzing-style tests
5. **Continuous Learning**: Feed back validation results to improve templates

---

## Migration Guide

To migrate from the old system to the new:

```bash
# Backup old file
cp design_targeted_tests.py design_targeted_tests_legacy.py

# Use new file (no changes needed to calling code)
python design_targeted_tests_v2.py --instances <same_args>
```

The new system is backwards-compatible - it accepts the same inputs and produces the same output format (instances with `test_patch` field populated).
