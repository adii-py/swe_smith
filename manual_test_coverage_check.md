# Manual Test Coverage Verification Guide

## Overview

This guide provides step-by-step instructions to manually verify if your tests actually cover the bug logic (not just the source code).

---

## Step 1: Identify the Bug Logic

### 1.1 Extract the Exact Bug Pattern

**Example - Bug #502 (W4A8 Validation):**
```python
# FIXED (correct):
if self.group_size != 128 or self.strategy != "group":
    raise ValueError("W4A8 kernels require group quantization with group size 128")

# BUGGY (incorrect):
if self.group_size != 128 and self.strategy != "group":
    raise ValueError("W4A8 kernels require group quantization with group size 128")
```

### 1.2 Draw the Decision Table

Create a truth table showing ALL possible input combinations:

| Case | group_size | strategy | OR (Fixed) | AND (Bug) | Bug Manifests? |
|------|------------|----------|------------|-----------|----------------|
| 1 | 128 | "group" | FALSE (ok) | FALSE (ok) | NO |
| 2 | 64 | "group" | TRUE (raise) | TRUE (raise) | NO |
| 3 | 128 | "channel" | TRUE (raise) | TRUE (raise) | NO |
| 4 | 64 | "channel" | TRUE (raise) | FALSE (ok) | **YES!** |

**Critical Insight:** The bug only manifests in Case 4.

---

## Step 2: Examine the Test Code

### 2.1 Checklist for Test Analysis

```
□ Does the test IMPORT the actual module/function?
□ Does the test INSTANTIATE/CALL the function?
□ Does the test pass ACTUAL PARAMETERS?
□ Does the test ASSERT on RETURN VALUES or SIDE EFFECTS?
□ Does the test cover ALL ROWS from the decision table?
```

### 2.2 Categorize the Test Type

| Test Type | Description | Effectiveness |
|-----------|-------------|---------------|
| **Source Match** | `assert 'pattern' in src` | ❌ Poor |
| **Smoke Test** | Import only, no execution | ❌ Poor |
| **Partial Runtime** | Some paths tested | ⚠️ Medium |
| **Full Runtime** | All decision paths tested | ✅ Good |

---

## Step 3: Verify Runtime Coverage

### Method A: Trace Through the Test

**Original Test (Bad):**
```python
def test_w4a8_validation():
    with open('file.py', 'r') as f:
        src = f.read()
    assert 'if self.group_size != 128 or self.strategy' in src
```

**Manual Verification:**
1. Does this test OPEN the source file? ✓ Yes
2. Does this test READ source content? ✓ Yes
3. Does this test EXECUTE the validation logic? ✗ NO!
4. Does this test check ALL 4 cases? ✗ NO!

**Verdict:** This test does NOT cover the bug logic.

---

### Method B: Inject the Bug and Run

**Step 1:** Temporarily introduce the bug:
```python
# Edit the source to use 'and' instead of 'or'
if self.group_size != 128 and self.strategy != "group":
```

**Step 2:** Run the test:
```bash
pytest tests/test_502.py -v
```

**Step 3:** Check if test fails:
- If test **FAILS** → Test catches the bug ✓
- If test **PASSES** → Test misses the bug ✗

**Example Result for Source Match Test:**
```python
# Even with the bug, this test passes:
assert 'if self.group_size != 128 or self.strategy' in src
# Why? Because we're checking for 'or' but the bug has 'and'
# The assertion fails... but wait, let me check...
```

Actually, with the bug, the source contains `and`, so the check for `or` would FAIL. But this only tells us the source code changed, not that the logic is wrong!

---

### Method C: Create a Runtime Test Matrix

**Better Test Design:**
```python
def test_w4a8_all_combinations():
    """Manually verify each decision table row."""
    test_cases = [
        # (group_size, strategy, should_raise)
        (128, "group", False),      # Case 1
        (64, "group", True),        # Case 2
        (128, "channel", True),     # Case 3
        (64, "channel", True),      # Case 4 - BUG HERE!
    ]

    for gs, strat, should_raise in test_cases:
        print(f"\nTesting: group_size={gs}, strategy='{strat}'")
        print(f"  Expected: {'Raise' if should_raise else 'Pass'}")

        try:
            scheme = CompressedTensorsW4A8Fp8(
                group_size=gs,
                strategy=strat,
                actorder=None,
                dynamic=False,
                quant_type="W4A8_FP8"
            )
            actually_raised = False
            print(f"  Actual: Passed")
        except ValueError as e:
            actually_raised = True
            print(f"  Actual: Raised ValueError")

        if should_raise != actually_raised:
            print(f"  ❌ MISMATCH! Bug detected in Case {test_cases.index((gs, strat, should_raise)) + 1}")
            assert False, f"Bug in case group_size={gs}, strategy='{strat}'"
        else:
            print(f"  ✓ Correct behavior")
```

**Manual Verification Output:**
```
Testing: group_size=128, strategy='group'
  Expected: Pass
  Actual: Passed
  ✓ Correct behavior

Testing: group_size=64, strategy='group'
  Expected: Raise
  Actual: Raised ValueError
  ✓ Correct behavior

Testing: group_size=128, strategy='channel'
  Expected: Raise
  Actual: Raised ValueError
  ✓ Correct behavior

Testing: group_size=64, strategy='channel'
  Expected: Raise
  Actual: Passed          <-- BUG! Should have raised but didn't
  ❌ MISMATCH! Bug detected in Case 4
```

---

## Step 4: Check for Edge Cases

### 4.1 Boundary Value Checklist

For numeric parameters, verify tests cover:

| Value Type | Examples | Why Important |
|------------|----------|---------------|
| **Exact boundary** | `group_size=128` | The threshold itself |
| **Just below** | `group_size=127` | Off-by-one errors |
| **Just above** | `group_size=129` | Off-by-one errors |
| **Zero** | `group_size=0` | Division by zero risk |
| **Negative** | `group_size=-1` | Invalid input handling |
| **None/Null** | `group_size=None` | Null pointer exceptions |
| **Very large** | `group_size=999999` | Overflow/buffer issues |
| **Wrong type** | `group_size="128"` | Type coercion bugs |

### 4.2 Manual Verification Script

```python
def verify_edge_case_coverage(param_name, boundary_value):
    """Print which edge cases are covered."""

    edge_cases = {
        'exact': boundary_value,
        'below': boundary_value - 1,
        'above': boundary_value + 1,
        'zero': 0,
        'negative': -1,
        'large': boundary_value * 10,
    }

    print(f"\nEdge Case Coverage for '{param_name}' (boundary={boundary_value}):")
    print("-" * 60)

    for case_name, value in edge_cases.items():
        # Check if test file mentions this value
        test_covers = check_if_test_uses_value(value)

        status = "✓ COVERED" if test_covers else "✗ MISSING"
        print(f"  {case_name:12} = {value:8} {status}")

def check_if_test_uses_value(value):
    """Simple check - look for value in test file."""
    with open('tests/test_502.py', 'r') as f:
        content = f.read()
    return str(value) in content

# Run the check
verify_edge_case_coverage('group_size', 128)
```

**Expected Output:**
```
Edge Case Coverage for 'group_size' (boundary=128):
------------------------------------------------------------
  exact        = 128      ✓ COVERED
  below        = 127      ✗ MISSING
  above        = 129      ✗ MISSING
  zero         = 0        ✗ MISSING
  negative     = -1       ✗ MISSING
  large        = 1280     ✗ MISSING
```

**Verdict:** If boundary values are missing, the test doesn't fully cover edge cases.

---

## Step 5: Verify State and Side Effects

### 5.1 Check Object Mutation

**Does the bug affect object state?**

```python
# Example: Bug removes quant_config storage
# Fixed: self.quant_config = quant_config
# Bug:   (line removed)

def test_quant_config_stored_in_object():
    """Verify the object's internal state."""

    # Create instance
    model = FlashModel(vllm_config=mock_config)

    # Check if attribute exists
    has_attr = hasattr(model, 'quant_config')
    print(f"Has quant_config attribute: {has_attr}")

    if has_attr:
        attr_value = model.quant_config
        print(f"quant_config value: {attr_value}")
        print(f"quant_config type: {type(attr_value)}")
    else:
        print("❌ BUG: quant_config not stored in object!")
        raise AssertionError("quant_config attribute missing")

    # Also verify it's the RIGHT value
    assert model.quant_config == mock_config.quant_config, \
        "Wrong quant_config stored"
```

### 5.2 Check Function Side Effects

**Does the bug affect external state?**

```python
# Example: ROCm version detection bug
# Changes version from 6.2.0 to 0.2.6

def test_rocm_version_side_effect():
    """Verify the version detected is actually used."""

    import ctypes
    from unittest.mock import patch, MagicMock

    # Capture what version gets stored
    stored_version = []

    with patch('ctypes.CDLL') as mock_cdll:
        def capture_version(major_ptr, minor_ptr, patch_ptr):
            # Simulate filling version (correct order)
            major_ptr._obj.value = 6
            minor_ptr._obj.value = 2
            patch_ptr._obj.value = 0

            # Record what was passed
            stored_version.append({
                'major': major_ptr._obj.value,
                'minor': minor_ptr._obj.value,
                'patch': patch_ptr._obj.value
            })
            return 0

        mock_lib = MagicMock()
        mock_lib.get_rocm_core_version = capture_version
        mock_cdll.return_value = mock_lib

        # Call the function
        from setup import get_rocm_version
        version = get_rocm_version()

        # Verify
        print(f"Stored version components: {stored_version}")
        print(f"Returned version string: {version}")

        if stored_version[0]['major'] == 0:  # Bug indicator
            print("❌ BUG: Major version is 0, expected 6")
            print("   Version components likely scrambled")
            assert False, "Version component order bug detected"
```

---

## Step 6: Concurrency Testing (if applicable)

### 6.1 Manual Race Condition Detection

**Does the bug involve thread safety?**

```python
import threading
import time

def test_concurrent_access_manually():
    """Manually verify thread safety."""

    errors = []
    success_count = [0]

    def worker(thread_id, iterations=100):
        try:
            for i in range(iterations):
                # Access the shared resource
                result = access_shared_tokenizer(f"text_{thread_id}_{i}")
                success_count[0] += 1
        except RuntimeError as e:
            errors.append((thread_id, str(e)))

    # Start multiple threads
    threads = []
    num_threads = 10
    iterations = 50

    print(f"Starting {num_threads} threads, {iterations} iterations each...")

    for i in range(num_threads):
        t = threading.Thread(target=worker, args=(i, iterations))
        threads.append(t)
        t.start()

    # Wait for completion
    for t in threads:
        t.join()

    print(f"\nResults:")
    print(f"  Successful accesses: {success_count[0]}")
    print(f"  Errors: {len(errors)}")

    if errors:
        print("\n  First 3 errors:")
        for tid, err in errors[:3]:
            print(f"    Thread {tid}: {err}")
        print("\n❌ BUG: Race condition detected!")
        return False
    else:
        print("\n✓ No race condition detected")
        return True
```

---

## Step 7: Performance Testing (if applicable)

### 7.1 Detect Performance Regression

**Does the bug remove an optimization?**

```python
import time
import statistics

def test_performance_manually():
    """Verify performance hasn't regressed."""

    # Setup test data
    input_data = create_test_data(size=1000)

    # Warmup
    for _ in range(10):
        function_under_test(input_data)

    # Benchmark
    times = []
    for _ in range(100):
        start = time.perf_counter()
        function_under_test(input_data)
        elapsed = time.perf_counter() - start
        times.append(elapsed * 1000)  # Convert to ms

    # Statistics
    avg_time = statistics.mean(times)
    median_time = statistics.median(times)
    min_time = min(times)
    max_time = max(times)

    print(f"\nPerformance Results (100 iterations):")
    print(f"  Average: {avg_time:.3f} ms")
    print(f"  Median:  {median_time:.3f} ms")
    print(f"  Min:     {min_time:.3f} ms")
    print(f"  Max:     {max_time:.3f} ms")

    # Define threshold (based on expected performance)
    threshold_ms = 5.0  # Adjust based on requirements

    if avg_time > threshold_ms:
        print(f"\n❌ PERFORMANCE REGRESSION!")
        print(f"   Expected < {threshold_ms} ms, got {avg_time:.3f} ms")
        print(f"   Likely cause: Optimized kernel removed")
        return False
    else:
        print(f"\n✓ Performance acceptable")
        return True
```

---

## Step 8: Complete Coverage Checklist

### Final Verification Checklist

For each bug, verify:

```
□ Decision Table Coverage
  □ Every row in the truth table is tested
  □ The specific buggy case is explicitly tested

□ Boundary Value Coverage
  □ Exact boundary values tested
  □ Just below/above boundaries tested
  □ Zero/None/Null cases tested
  □ Extreme values tested

□ Runtime Execution
  □ Test actually calls/runs the buggy function
  □ Test doesn't just check source code
  □ Assertions verify behavior, not presence

□ State Verification
  □ Object attributes verified after operation
  □ Side effects verified
  □ Global/shared state correctly modified

□ Error Handling
  □ Correct exceptions raised for invalid inputs
  □ Error messages are helpful
  □ No silent failures

□ Concurrency (if applicable)
  □ Multi-threaded test passes
  □ No race conditions detected

□ Performance (if applicable)
  □ Meets performance baseline
  □ No significant regression

□ Documentation
  □ Test explains what it's checking
  □ Comments reference the bug/issue
  □ Expected vs actual behavior documented
```

---

## Quick Reference: Test Quality Levels

| Level | Description | How to Verify |
|-------|-------------|---------------|
| **Level 0: No Test** | No test exists | File missing |
| **Level 1: Source Check** | `assert 'text' in src` | Search for file reading |
| **Level 2: Import Only** | Imports but doesn't call | Check for function calls |
| **Level 3: Partial Runtime** | Some paths tested | Check decision table coverage |
| **Level 4: Full Runtime** | All paths tested | Verify with bug injection |
| **Level 5: Full Coverage** | + edge cases + concurrency + perf | Complete checklist |

---

## Example: Evaluating a Real Test

**Test Being Evaluated:**
```python
def test_w4a8_validation():
    with open('file.py', 'r') as f:
        src = f.read()
    assert 'if self.group_size != 128 or self.strategy' in src
```

**Step-by-Step Evaluation:**

```
Step 1: Does it import the module?              → NO
Step 2: Does it instantiate the class?          → NO
Step 3: Does it pass actual parameters?         → NO
Step 4: Does it test all decision table rows?   → NO (checks only 1 pattern)
Step 5: Does it verify behavior?                → NO (verifies text presence)
Step 6: Does it check edge cases?               → NO
Step 7: Does it test runtime execution?         → NO
```

**Final Score: 0/7**

**Quality Level: Level 1 (Source Check)**

**Recommendation:** Rewrite as runtime test covering all 4 decision table rows.

---

## Summary

**The Golden Rule:**
> If you inject the bug and the test still passes, the test does NOT cover the bug.

**Quick Test:**
1. Introduce the bug intentionally
2. Run your test
3. If it fails → Test covers the bug ✓
4. If it passes → Test misses the bug ✗

**Always Remember:**
- Source code matching ≠ Bug coverage
- Importing ≠ Executing
- One test case ≠ All edge cases
- Static analysis ≠ Runtime behavior
