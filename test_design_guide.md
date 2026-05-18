# Comprehensive Test Design Guide for Bug Detection

## Core Principles

### 1. Test Behavior, Not Implementation
```python
# ❌ BAD: Tests source code pattern
def test_bug_fixed():
    assert 'if x is None' in src

# ✅ GOOD: Tests actual behavior
def test_bug_fixed():
    result = function_under_test(None)
    assert result == expected_value
```

### 2. Cover All Branches
Every `if/else`, `try/except`, `and/or` combination needs a test case.

### 3. Test Boundaries (BVA - Boundary Value Analysis)
Test at the edges: min-1, min, min+1, max-1, max, max+1

### 4. Test Equivalence Classes
Group inputs that should behave similarly, test representative from each class.

---

## Pattern-Specific Test Designs

### Pattern 1: Logical Operator Bugs (OR ↔ AND)

**Bug Example:**
```python
# Fixed: if self.group_size != 128 or self.strategy != "group":
# Bug:   if self.group_size != 128 and self.strategy != "group":
```

**Truth Table Analysis:**
| group_size | strategy | OR (correct) | AND (bug) |
|------------|----------|--------------|-----------|
| 128 | group | False (pass) | False (pass) |
| 64 | group | True (raise) | True (raise) |
| 128 | channel | True (raise) | True (raise) |
| 64 | channel | True (raise) | False (BUG!) |

**Test Design:**
```python
import pytest
from unittest.mock import patch, MagicMock

def test_w4a8_validation_logic():
    """Comprehensive test for W4A8 validation logic.

    Truth table coverage:
    - Case 1: group=128, strategy=group → should PASS
    - Case 2: group≠128, strategy=group → should RAISE
    - Case 3: group=128, strategy≠group → should RAISE
    - Case 4: group≠128, strategy≠group → should RAISE (bug: passes)
    """
    from vllm.model_executor.layers.quantization.compressed_tensors.schemes.compressed_tensors_w4a8_fp8 import CompressedTensorsW4A8Fp8

    # Case 1: Valid config - should NOT raise
    try:
        scheme = CompressedTensorsW4A8Fp8(
            group_size=128,
            strategy="group",
            actorder=None,  # Mock other required params
            dynamic=False,
            quant_type="W4A8_FP8"
        )
        valid_config_passed = True
    except ValueError as e:
        valid_config_passed = False
        print(f"Case 1 FAILED: Valid config rejected: {e}")

    assert valid_config_passed, "BUG: Valid (128, 'group') config was rejected!"

    # Case 2: Wrong group_size - should RAISE
    with pytest.raises(ValueError, match="group size 128"):
        CompressedTensorsW4A8Fp8(
            group_size=64,  # Wrong
            strategy="group",  # Correct
            actorder=None,
            dynamic=False,
            quant_type="W4A8_FP8"
        )

    # Case 3: Wrong strategy - should RAISE
    with pytest.raises(ValueError, match="group quantization"):
        CompressedTensorsW4A8Fp8(
            group_size=128,  # Correct
            strategy="channel",  # Wrong
            actorder=None,
            dynamic=False,
            quant_type="W4A8_FP8"
        )

    # Case 4: Both wrong - should RAISE
    # This is where the bug manifests - with AND, this doesn't raise
    with pytest.raises(ValueError):
        CompressedTensorsW4A8Fp8(
            group_size=64,  # Wrong
            strategy="channel",  # Wrong
            actorder=None,
            dynamic=False,
            quant_type="W4A8_FP8"
        )


def test_w4a8_boundary_values():
    """Test boundary values around 128."""
    from vllm.model_executor.layers.quantization.compressed_tensors.schemes.compressed_tensors_w4a8_fp8 import CompressedTensorsW4A8Fp8

    # Test group_size boundaries
    boundary_values = [127, 128, 129, 0, -1, None, 256]

    for gs in boundary_values:
        if gs == 128:
            # Should pass with strategy="group"
            try:
                CompressedTensorsW4A8Fp8(
                    group_size=gs,
                    strategy="group",
                    actorder=None,
                    dynamic=False,
                    quant_type="W4A8_FP8"
                )
            except ValueError:
                pytest.fail(f"group_size={gs} with strategy='group' should pass")
        else:
            # Should raise
            with pytest.raises(ValueError):
                CompressedTensorsW4A8Fp8(
                    group_size=gs,
                    strategy="group",
                    actorder=None,
                    dynamic=False,
                    quant_type="W4A8_FP8"
                )
```

---

### Pattern 2: Parameter Order Bugs

**Bug Example:**
```python
# Fixed: ctypes.byref(major), ctypes.byref(minor), ctypes.byref(patch)
# Bug:   ctypes.byref(patch), ctypes.byref(minor), ctypes.byref(major)
```

**Test Design:**
```python
import ctypes
from unittest.mock import Mock, patch

def test_rocm_version_parameter_order():
    """Test that version components are passed in correct order.

    Strategy: Mock the C function to capture arguments
    """
    captured_args = []

    def mock_get_rocm_core_version(*args):
        """Capture the order of arguments passed."""
        for arg in args:
            if hasattr(arg, '_obj'):  # ctypes.byref object
                captured_args.append(arg._obj.value)
        return 0  # Success

    with patch('ctypes.CDLL') as mock_cdll:
        mock_lib = Mock()
        mock_lib.get_rocm_core_version = mock_get_rocm_core_version
        mock_cdll.return_value = mock_lib

        # Import and call after patching
        from setup import get_rocm_version

        # Set up return values for major, minor, patch
        major = ctypes.c_int(6)
        minor = ctypes.c_int(2)
        patch = ctypes.c_int(0)

        mock_lib.get_rocm_core_version.side_effect = lambda *args: (
            setattr(args[0]._obj, 'value', 6) or
            setattr(args[1]._obj, 'value', 2) or
            setattr(args[2]._obj, 'value', 0) or
            0
        )

        version = get_rocm_version()

        # Verify the function was called with correct argument order
        call_args = mock_lib.get_rocm_core_version.call_args[0]
        assert len(call_args) == 3

        # The first argument should receive major (6)
        # If bug exists, first arg receives patch (0)
        first_arg_value = ctypes.c_int()
        call_args[0](ctypes.byref(first_arg_value))

        # Verify order: major, minor, patch
        assert first_arg_value.value == 6, "First arg should be major"


def test_rocm_version_return_value():
    """Integration test: Verify version is parsed correctly."""
    with patch('ctypes.CDLL') as mock_cdll:
        mock_lib = Mock()

        def simulate_correct_order(major_ptr, minor_ptr, patch_ptr):
            """Simulate filling in correct order."""
            major_ptr._obj.value = 6
            minor_ptr._obj.value = 2
            patch_ptr._obj.value = 0
            return 0

        def simulate_bug_order(patch_ptr, minor_ptr, major_ptr):
            """Simulate bug: wrong order."""
            patch_ptr._obj.value = 6  # Bug: major goes to patch
            minor_ptr._obj.value = 2
            major_ptr._obj.value = 0  # Bug: patch goes to major
            return 0

        # Test fixed version
        mock_lib.get_rocm_core_version.side_effect = simulate_correct_order
        mock_cdll.return_value = mock_lib

        from setup import get_rocm_version
        version = get_rocm_version()

        # With correct order: should get 6.2.0
        # With bug order: would get 0.2.6
        assert version.startswith("6.2"), f"Expected 6.2.x, got {version}"
```

---

### Pattern 3: Null/None Check Inversion

**Bug Example:**
```python
# Fixed: quant_config.a1_scale if a1q_scale is None else a1q_scale
# Bug:   quant_config.a1_scale if a1q_scale is not None else a1q_scale
```

**Test Design:**
```python
import torch
from unittest.mock import MagicMock

def test_scale_selection_null_check():
    """Test that scale selection correctly handles None values.

    Logic analysis:
    - When a1q_scale is None: should use quant_config.a1_scale (static)
    - When a1q_scale is not None: should use a1q_scale (dynamic)

    Bug: Inverted logic causes wrong scale selection
    """
    # We'll need to instrument the function to observe the choice
    import vllm.model_executor.layers.fused_moe.rocm_aiter_fused_moe as moe_module

    # Create a mock config
    mock_config = MagicMock()
    mock_config.a1_scale = torch.tensor([1.0])  # Static scale
    mock_config.a2_scale = torch.tensor([1.0])
    mock_config.w1_scale = torch.tensor([1.0])
    mock_config.w2_scale = torch.tensor([1.0])

    # Test Case 1: a1q_scale is None - should use static (quant_config.a1_scale)
    dynamic_scale_none = None

    # Instrument the function to capture which scale is used
    used_scales = []

    def capture_scale_call(*args, **kwargs):
        if 'a1_scale' in kwargs:
            used_scales.append(('a1_scale', kwargs['a1_scale']))
        return Mock()

    with patch.object(moe_module, 'rocm_aiter_fused_experts', capture_scale_call):
        # Call with None dynamic scale
        moe_module.rocm_aiter_fused_experts(
            hidden_states=torch.randn(10, 512),
            w1=torch.randn(512, 512),
            w2=torch.randn(512, 512),
            topk_ids=torch.randint(0, 8, (10, 2)),
            quant_config=mock_config,
            a1q_scale=None  # No dynamic scale provided
        )

        # Should use static scale from quant_config
        assert len(used_scales) == 1
        scale_name, scale_value = used_scales[0]
        assert torch.allclose(scale_value, mock_config.a1_scale), \
            "When a1q_scale is None, should use quant_config.a1_scale"

    # Test Case 2: a1q_scale is provided - should use dynamic
    used_scales.clear()
    dynamic_scale = torch.tensor([2.0])

    with patch.object(moe_module, 'rocm_aiter_fused_experts', capture_scale_call):
        moe_module.rocm_aiter_fused_experts(
            hidden_states=torch.randn(10, 512),
            w1=torch.randn(512, 512),
            w2=torch.randn(512, 512),
            topk_ids=torch.randint(0, 8, (10, 2)),
            quant_config=mock_config,
            a1q_scale=dynamic_scale
        )

        # Should use provided dynamic scale
        assert len(used_scales) == 1
        scale_name, scale_value = used_scales[0]
        assert torch.allclose(scale_value, dynamic_scale), \
            "When a1q_scale is provided, should use dynamic scale"
```

---

### Pattern 4: Code Removal Bugs

**Bug Example:** TileLang kernel removed, falls back to PyTorch

**Test Design:**
```python
import time
import torch

def test_tilelang_performance():
    """Test that optimized kernel meets performance requirements.

    Bug detection: If kernel is removed, performance degrades 3-5x
    """
    from vllm.model_executor.layers.mhc import hc_head_fuse_tilelang
    from vllm.model_executor.models.deepseek_v4 import hc_head

    # Create test inputs
    batch_size = 128
    hidden_size = 4096
    hc_mult = 4

    hidden_states = torch.randn(batch_size, hc_mult, hidden_size,
                                 dtype=torch.bfloat16, device='cuda')
    hc_fn = torch.randn(hc_mult, hc_mult * hidden_size, dtype=torch.float32, device='cuda')
    hc_scale = torch.tensor([0.5], dtype=torch.float32, device='cuda')
    hc_base = torch.randn(hc_mult, dtype=torch.float32, device='cuda')

    # Warmup
    for _ in range(10):
        _ = hc_head(hidden_states, hc_fn, hc_scale, hc_base,
                    rms_norm_eps=1e-6, hc_eps=1e-6)

    # Benchmark
    torch.cuda.synchronize()
    start = time.time()

    for _ in range(100):
        _ = hc_head(hidden_states, hc_fn, hc_scale, hc_base,
                    rms_norm_eps=1e-6, hc_eps=1e-6)

    torch.cuda.synchronize()
    elapsed = time.time() - start

    avg_time_ms = (elapsed / 100) * 1000

    # Performance threshold: TileLang should be < 1ms, pure PyTorch ~3-5ms
    assert avg_time_ms < 2.0, \
        f"Performance regression detected: {avg_time_ms:.2f}ms avg. " \
        f"TileLang kernel may be missing (expect < 2ms, pure PyTorch ~5ms)"


def test_kernel_existence_and_attributes():
    """Verify kernel function exists with correct attributes."""
    import vllm.model_executor.layers.mhc as mhc_module

    # Check function exists
    assert hasattr(mhc_module, 'hc_head_fuse_tilelang'), \
        "hc_head_fuse_tilelang function missing - kernel removed"

    func = mhc_module.hc_head_fuse_tilelang

    # Check it's decorated with tilelang.jit
    assert hasattr(func, '__wrapped__') or 'tilelang' in str(type(func)), \
        "Function not wrapped by tilelang.jit"

    # Check function signature has expected parameters
    import inspect
    sig = inspect.signature(func)
    expected_params = ['residual', 'fn', 'hc_scale', 'hc_base', 'out',
                       'hidden_size', 'rms_eps', 'hc_eps']

    for param in expected_params:
        assert param in sig.parameters, \
            f"Required parameter '{param}' missing from kernel signature"
```

---

### Pattern 5: Race Conditions / Thread Safety

**Bug Example:** Tokenizer retry logic removed

**Test Design:**
```python
import threading
import concurrent.futures
from unittest.mock import patch

def test_tokenizer_thread_safety():
    """Test concurrent tokenizer access doesn't cause 'Already borrowed' errors.

    Bug: Without retry logic or deep copy, concurrent access fails.
    """
    from vllm.tokenizers.hf import maybe_make_thread_pool
    from vllm.multimodal.processing import MultiModalProcessor

    # Create a mock processor with tokenizer
    mock_tokenizer = MagicMock()
    mock_tokenizer.encode = MagicMock(return_value=[1, 2, 3])
    mock_tokenizer.decode = MagicMock(return_value="decoded")

    # Simulate race condition
    errors = []

    def access_tokenizer(thread_id):
        try:
            for _ in range(100):  # Multiple accesses
                _ = mock_tokenizer.encode(f"test {thread_id}")
                _ = mock_tokenizer.decode([1, 2, 3])
        except RuntimeError as e:
            if "Already borrowed" in str(e):
                errors.append((thread_id, str(e)))

    # Run concurrent accesses
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(access_tokenizer, i) for i in range(10)]
        concurrent.futures.wait(futures)

    # Check for race condition errors
    assert len(errors) == 0, \
        f"Race condition detected: {len(errors)} threads got 'Already borrowed' errors\n" \
        f"Sample: {errors[:3]}"


def test_tokenizer_retry_mechanism():
    """Verify retry logic exists and handles transient failures."""
    import vllm.multimodal.processing.context as ctx_module

    # Check that retry logic exists in source
    import inspect
    source = inspect.getsource(ctx_module.call_hf_processor)

    # Should have retry loop structure
    assert 'num_tries' in source or 'max_tries' in source, \
        "Retry counter missing"
    assert 'time.sleep' in source, \
        "Backoff delay missing - will spin-wait on retry"
    assert 'Already borrowed' in source or 'RuntimeError' in source, \
        "Specific error handling missing"

    # Verify retry is actually invoked on failure
    call_count = [0]

    def failing_processor(*args, **kwargs):
        call_count[0] += 1
        if call_count[0] < 3:
            raise RuntimeError("Already borrowed")
        return {"input_ids": [1, 2, 3]}

    with patch.object(ctx_module, 'call_hf_processor', side_effect=failing_processor):
        # Should retry and eventually succeed
        result = ctx_module.call_hf_processor(
            failing_processor,
            {"text": "test"},
            {}
        )

        assert call_count[0] >= 3, \
            f"Expected at least 3 calls (2 failures + 1 success), got {call_count[0]}"
        assert result is not None
```

---

## General Edge Case Testing Strategies

### 1. Input Validation Matrix
```python
def generate_edge_cases(base_value, dtype):
    """Generate comprehensive edge cases for a value."""
    cases = []

    if dtype == int:
        cases = [
            0,              # Zero
            -1,             # Negative
            1,              # One
            base_value - 1, # Just below
            base_value,     # Exact
            base_value + 1, # Just above
            base_value * 2, # Double
            base_value // 2,# Half
            None,           # Null
            float('inf'),   # Infinity (if applicable)
        ]
    elif dtype == str:
        cases = [
            "",             # Empty
            base_value,     # Exact
            base_value.upper(),  # Case variation
            base_value + "_extra",  # Extra suffix
            "extra_" + base_value,  # Extra prefix
            None,           # Null
        ]

    return cases
```

### 2. State Machine Testing
```python
def test_state_transitions():
    """Test all valid and invalid state transitions."""
    states = ['INIT', 'CONFIGURING', 'READY', 'RUNNING', 'STOPPED']

    valid_transitions = [
        ('INIT', 'CONFIGURING'),
        ('CONFIGURING', 'READY'),
        ('READY', 'RUNNING'),
        ('RUNNING', 'STOPPED'),
        ('READY', 'STOPPED'),
    ]

    # Test all possible transitions
    for from_state in states:
        for to_state in states:
            is_valid = (from_state, to_state) in valid_transitions

            # Reset to from_state
            obj.reset_to_state(from_state)

            if is_valid:
                # Should succeed
                obj.transition(to_state)
                assert obj.state == to_state
            else:
                # Should fail
                with pytest.raises(InvalidTransitionError):
                    obj.transition(to_state)
```

### 3. Fuzzing Light
```python
import random
import string

def test_fuzz_inputs():
    """Test with randomized inputs to catch unexpected edge cases."""
    for _ in range(1000):
        # Random string
        random_str = ''.join(random.choices(string.printable, k=random.randint(0, 100)))

        # Random int
        random_int = random.randint(-1000000, 1000000)

        # Random float
        random_float = random.uniform(-1e10, 1e10)

        # Should not crash (may return error, but not segfault/infinite loop)
        try:
            result = function_under_test(random_str, random_int, random_float)
            # Result should be valid type
            assert result is None or isinstance(result, (int, float, str, bool))
        except (ValueError, TypeError):
            pass  # Expected for invalid inputs
```

---

## Test Organization Best Practices

### File Structure
```
tests/
├── unit/                           # Individual function tests
│   ├── test_compressed_tensors_w4a8_fp8.py
│   ├── test_rocm_aiter_fused_moe.py
│   └── test_scheduler.py
├── integration/                    # Multi-component tests
│   ├── test_quantization_pipeline.py
│   └── test_moe_end_to_end.py
├── regression/                     # Bug-specific tests (F2P)
│   ├── test_pr_502.py
│   ├── test_pr_508.py
│   └── test_pr_41181.py
├── performance/                    # Benchmarks
│   ├── test_tilelang_performance.py
│   └── test_throughput.py
└── fixtures/                       # Test data
    ├── sample_models/
    └── expected_outputs/
```

### Test Naming Convention
```python
# Format: test_<component>_<scenario>_<expected_result>

def test_w4a8_validation_valid_config_accepts():
    """GIVEN: group_size=128, strategy='group' WHEN: creating W4A8 scheme THEN: succeeds"""
    pass

def test_w4a8_validation_invalid_group_size_rejects():
    """GIVEN: group_size=64, strategy='group' WHEN: creating W4A8 scheme THEN: raises ValueError"""
    pass

def test_w4a8_validation_both_invalid_rejects():
    """GIVEN: group_size=64, strategy='channel' WHEN: creating W4A8 scheme THEN: raises ValueError"""
    pass
```

---

## Coverage Requirements Checklist

For each bug fix, tests should cover:

- [ ] **Happy path**: Normal valid inputs work correctly
- [ ] **Direct bug scenario**: The specific case that was broken
- [ ] **Boundary values**: Min, max, and edge values
- [ ] **Null/None handling**: Where applicable
- [ ] **Type variations**: Different input types if polymorphic
- [ ] **Concurrency**: Thread safety where relevant
- [ ] **Performance**: Regression benchmarks for perf-critical code
- [ ] **Error messages**: Verify helpful error messages for invalid inputs
- [ ] **State cleanup**: Resources properly released
- [ ] **Determinism**: Same input → same output (no randomness)

---

## Summary

**Golden Rules:**
1. **Test runtime behavior**, not source code patterns
2. **Use parameterized tests** for exhaustive coverage
3. **Mock external dependencies** but test real logic
4. **Include negative assertions** (what should NOT happen)
5. **Document test intent** with clear docstrings
6. **Fail with helpful messages** showing expected vs actual

**Anti-Patterns to Avoid:**
- ❌ Source code string matching
- ❌ Testing implementation details
- ❌ Missing boundary cases
- ❌ No concurrency tests for thread-sensitive code
- ❌ No performance baselines for optimized code
