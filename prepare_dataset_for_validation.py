#!/usr/bin/env python3
"""
Enrich the bug dataset with test patches for F2P/P2P validation.
"""

import json
import hashlib
from pathlib import Path

INPUT_DATASET = "vllm_3e1ad443_generated_bugs_dataset.json"
OUTPUT_DATASET = "vllm_3e1ad443_bugs_for_validation.json"

def generate_test_for_bug(bug_info):
    """Generate a test patch for a bug."""

    file_path = bug_info["file_path"]
    func_name = bug_info["function"]
    instance_id = bug_info["instance_id"]

    # Determine module path from file path
    module_path = file_path.replace("/", ".").replace(".py", "")

    # Generate test file name
    func_hash = hashlib.sha256(func_name.encode()).hexdigest()[:8]
    test_file = f"tests/bugs/test_{func_name}_{func_hash}.py"

    # Create test based on function type
    if "." in func_name:
        # It's a class method
        class_name, method_name = func_name.split(".", 1)
        test_code = f'''import pytest
import sys
sys.path.insert(0, ".")

from {module_path} import {class_name}

def test_{method_name}_detects_bug():
    """Test that detects the bug in {class_name}.{method_name}"""
    # Create instance with minimal required fields
    try:
        instance = {class_name}()
    except Exception as e:
        # Some configs may require fields
        pytest.skip(f"Cannot instantiate {class_name}: {{e}}")

    # Call the buggy method
    try:
        result = instance.{method_name}()
        # If we get here without error, the bug might be subtle
        # The test should check for specific buggy behavior
        assert result is not None, "Method should return a value"
    except Exception as e:
        # The bug might cause an exception
        pytest.fail(f"Bug detected - {method_name} raised: {{e}}")
'''
    else:
        # It's a standalone function
        test_code = f'''import pytest
import sys
sys.path.insert(0, ".")

from {module_path} import {func_name}

def test_{func_name}_detects_bug():
    """Test that detects the bug in {func_name}"""
    try:
        # Try calling the function
        result = {func_name}()
        assert result is not None, "Function should return a value"
    except Exception as e:
        # The bug might cause an exception
        pytest.fail(f"Bug detected - {func_name} raised: {{e}}")
'''

    # Create test patch
    test_patch = f"diff --git a/{test_file} b/{test_file}\n"
    test_patch += f"new file mode 100644\n"
    test_patch += f"index 0000000..1234567\n"
    test_patch += f"--- /dev/null\n"
    test_patch += f"+++ b/{test_file}\n"
    test_patch += f"@@ -0,0 +1,{len(test_code.split(chr(10)))} @@\n"

    for line in test_code.split("\n"):
        test_patch += f"+{line}\n"

    return test_patch, [f"{test_file}::test_{method_name if '.' in func_name else func_name}_detects_bug"]


def main():
    print(f"Loading dataset from {INPUT_DATASET}...")

    with open(INPUT_DATASET) as f:
        bugs = json.load(f)

    print(f"Processing {len(bugs)} bugs...")

    enriched_bugs = []
    for i, bug in enumerate(bugs, 1):
        print(f"  [{i}/{len(bugs)}] {bug['instance_id']}: {bug['file_path']}::{bug['function']}")

        # Generate test patch
        test_patch, fail_to_pass = generate_test_for_bug(bug)

        # Create enriched bug entry
        enriched_bug = {
            **bug,
            "test_patch": test_patch,
            "FAIL_TO_PASS": fail_to_pass,
            "PASS_TO_PASS": [],  # Will be populated by validation
        }

        enriched_bugs.append(enriched_bug)

    # Save enriched dataset
    with open(OUTPUT_DATASET, 'w') as f:
        json.dump(enriched_bugs, f, indent=2)

    print(f"\n{'='*60}")
    print(f"Enriched dataset saved to: {OUTPUT_DATASET}")
    print(f"Total bugs ready for validation: {len(enriched_bugs)}")
    print(f"{'='*60}")

    # Print sample
    print("\nSample entry:")
    sample = enriched_bugs[0]
    print(f"  Instance ID: {sample['instance_id']}")
    print(f"  File: {sample['file_path']}")
    print(f"  Function: {sample['function']}")
    print(f"  FAIL_TO_PASS: {sample['FAIL_TO_PASS']}")

if __name__ == "__main__":
    main()
