#!/usr/bin/env python3
"""
Find existing tests in the vLLM repo that can be used as F2P/P2P tests.
Using existing tests is more reliable than generating new ones.
"""

import json
import os
import re
from pathlib import Path

VLLM_REPO = "./tmp_d6b73da0/vllm-project__vllm.3e1ad443"
TESTS_DIR = f"{VLLM_REPO}/tests"
BUGS_FILE = "vllm_bugs_with_f2p_p2p_tests.json"
OUTPUT_FILE = "vllm_bugs_with_existing_tests.json"

def find_tests_for_function(function_name, file_path):
    """Find existing tests that test a specific function."""
    matching_tests = []

    # Search patterns
    patterns = [
        rf"def test.*{function_name}.*\(",  # test_function_name
        rf"{function_name}\(",             # direct function call
        rf"from.*{file_path.replace('/', '.').replace('.py', '')}.*import",  # imports from module
    ]

    # Walk through test files
    for root, dirs, files in os.walk(TESTS_DIR):
        for file in files:
            if not file.endswith('.py'):
                continue

            test_file = os.path.join(root, file)
            rel_path = os.path.relpath(test_file, VLLM_REPO)

            try:
                with open(test_file, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()

                # Check if any pattern matches
                for pattern in patterns:
                    if re.search(pattern, content, re.IGNORECASE):
                        # Find specific test functions
                        test_funcs = re.findall(r'def (test_\w+)\(', content)
                        for func in test_funcs:
                            test_id = f"{rel_path}::{func}"
                            if test_id not in [t['id'] for t in matching_tests]:
                                matching_tests.append({
                                    'id': test_id,
                                    'file': rel_path,
                                    'function': func,
                                    'match_type': 'function_reference'
                                })
                        break

            except Exception as e:
                continue

    return matching_tests

def find_module_tests(file_path):
    """Find tests that test the module containing the buggy function."""
    module_tests = []

    # Extract module name from file path
    # e.g., vllm/config/utils.py -> test_config_utils.py
    parts = file_path.split('/')

    # Possible test file patterns
    possible_test_files = []

    # Pattern 1: tests/<module>/test_<filename>
    if len(parts) >= 2:
        module_name = parts[-2] if len(parts) > 1 else parts[0]
        filename = parts[-1].replace('.py', '')
        possible_test_files.append(f"tests/{module_name}/test_{filename}.py")
        possible_test_files.append(f"tests/{module_name}/test_{filename}_test.py")

    # Pattern 2: tests/test_<module>.py
    if parts:
        possible_test_files.append(f"tests/test_{parts[0]}.py")

    # Check if these test files exist
    for test_file in possible_test_files:
        full_path = os.path.join(VLLM_REPO, test_file)
        if os.path.exists(full_path):
            try:
                with open(full_path, 'r') as f:
                    content = f.read()

                test_funcs = re.findall(r'def (test_\w+)\(', content)
                for func in test_funcs:
                    module_tests.append({
                        'id': f"{test_file}::{func}",
                        'file': test_file,
                        'function': func,
                        'match_type': 'module_match'
                    })
            except:
                pass

    return module_tests

def find_related_tests(bug):
    """Find all related tests for a bug."""
    function_name = bug['function_name']
    file_path = bug['file_path']
    bug_type = bug.get('bug_type', '')

    print(f"\n🔍 Finding tests for: {function_name}")

    # Strategy 1: Direct function name match
    tests = find_tests_for_function(function_name, file_path)

    # Strategy 2: Module-level tests
    if len(tests) < 3:
        module_tests = find_module_tests(file_path)
        for mt in module_tests:
            if mt['id'] not in [t['id'] for t in tests]:
                tests.append(mt)

    # Strategy 3: Bug-type specific tests
    bug_type_keywords = {
        'pooling': ['pooling', 'embed', 'sequence'],
        'quantization': ['quant', 'fp8', 'scale'],
        'moe': ['moe', 'expert', 'routing'],
        'config': ['config', 'utils'],
        'sampling': ['sampl', 'token', 'logits'],
        'tensor': ['tensor', 'shape', 'dim'],
        'attention': ['attn', 'attention', 'mask'],
    }

    # Find keywords from bug type
    keywords = []
    for key, words in bug_type_keywords.items():
        if key in bug_type.lower():
            keywords.extend(words)

    if keywords and len(tests) < 5:
        # Search for tests matching keywords
        for root, dirs, files in os.walk(TESTS_DIR):
            for file in files:
                if not file.endswith('.py'):
                    continue

                test_file = os.path.join(root, file)
                rel_path = os.path.relpath(test_file, VLLM_REPO)

                try:
                    with open(test_file, 'r', encoding='utf-8', errors='ignore') as f:
                        content = f.read()

                    # Check if content matches keywords
                    content_lower = content.lower()
                    if any(kw in content_lower for kw in keywords):
                        test_funcs = re.findall(r'def (test_\w+)\(', content)
                        for func in test_funcs[:3]:  # Limit to first 3
                            test_id = f"{rel_path}::{func}"
                            if test_id not in [t['id'] for t in tests]:
                                tests.append({
                                    'id': test_id,
                                    'file': rel_path,
                                    'function': func,
                                    'match_type': 'keyword_match'
                                })
                except:
                    pass

    print(f"   Found {len(tests)} potential tests")
    for t in tests[:5]:
        print(f"     - {t['id']} ({t['match_type']})")

    return tests

def categorize_tests(tests):
    """Categorize tests into F2P and P2P candidates."""
    # Heuristic: tests that directly reference the function are good F2P candidates
    # Tests from the same module but not directly referencing are P2P candidates

    f2p_candidates = []
    p2p_candidates = []

    for test in tests:
        if test['match_type'] == 'function_reference':
            f2p_candidates.append(test['id'])
        else:
            p2p_candidates.append(test['id'])

    return f2p_candidates, p2p_candidates

def main():
    print("="*70)
    print("FINDING EXISTING TESTS IN VLLM REPO")
    print("="*70)
    print(f"\nRepository: {VLLM_REPO}")
    print(f"Tests directory: {TESTS_DIR}")

    if not os.path.exists(VLLM_REPO):
        print(f"\n❌ Error: Repository not found at {VLLM_REPO}")
        return

    if not os.path.exists(BUGS_FILE):
        print(f"\n❌ Error: Bugs file not found at {BUGS_FILE}")
        return

    with open(BUGS_FILE) as f:
        bugs = json.load(f)

    print(f"\nProcessing {len(bugs)} bugs...")

    enhanced_bugs = []

    for i, bug in enumerate(bugs, 1):
        print(f"\n{'='*70}")
        print(f"Bug {i}/{len(bugs)}: {bug['function_name']}")
        print(f"File: {bug['file_path']}")

        # Find existing tests
        related_tests = find_related_tests(bug)

        if related_tests:
            f2p_list, p2p_list = categorize_tests(related_tests)

            print(f"\n✅ Found {len(f2p_list)} F2P candidates")
            print(f"✅ Found {len(p2p_list)} P2P candidates")

            # Always populate both F2P and P2P when we have tests
            if f2p_list:
                # Use first 3 function-reference tests as F2P
                bug['EXISTING_FAIL_TO_PASS'] = f2p_list[:3]
                # Use REMAINING tests as P2P (other function refs + module/keyword matches)
                p2p_candidates = f2p_list[3:] + p2p_list
                # If still no P2P, use last 2 from f2p_list
                if not p2p_candidates and len(f2p_list) > 1:
                    p2p_candidates = f2p_list[-2:]
                # Cap P2P at 10 for faster validation
                bug['EXISTING_PASS_TO_PASS'] = p2p_candidates[:10]
            else:
                # No direct F2P candidates - use best available tests
                print(f"\n⚠️  No direct F2P candidates found")
                print(f"   Using module/keyword matches")
                bug['EXISTING_FAIL_TO_PASS'] = []
                # Cap at 10 for faster validation
                bug['EXISTING_PASS_TO_PASS'] = [t['id'] for t in related_tests[:10]]

            bug['all_related_tests'] = [t['id'] for t in related_tests]
        else:
            print(f"\n❌ No existing tests found")
            print(f"   Will need generated tests")
            bug['EXISTING_FAIL_TO_PASS'] = []
            bug['EXISTING_PASS_TO_PASS'] = []

        enhanced_bugs.append(bug)

    # Save results
    with open(OUTPUT_FILE, 'w') as f:
        json.dump(enhanced_bugs, f, indent=2)

    print("\n" + "="*70)
    print("SUMMARY")
    print("="*70)

    with_f2p = sum(1 for b in enhanced_bugs if b.get('EXISTING_FAIL_TO_PASS'))
    with_p2p = sum(1 for b in enhanced_bugs if b.get('EXISTING_PASS_TO_PASS'))

    print(f"\n✅ Bugs with existing F2P tests: {with_f2p}/{len(bugs)}")
    print(f"✅ Bugs with existing P2P tests: {with_p2p}/{len(bugs)}")

    print(f"\n💾 Results saved to: {OUTPUT_FILE}")

    print("\n" + "="*70)
    print("RECOMMENDATION")
    print("="*70)
    print("""
For bugs WITH existing F2P tests:
  → Use EXISTING_FAIL_TO_PASS and EXISTING_PASS_TO_PASS
  → These tests are already in the repo and validated
  → More likely to work during validation

For bugs WITHOUT existing F2P tests:
  → Use the generated tests (FAIL_TO_PASS, PASS_TO_PASS)
  → OR manually write tests based on the bug behavior
  → Validate with diagnose_f2p_issues.py first

Hybrid approach (recommended):
  → F2P: Use existing test that should fail with bug
  → P2P: Use existing tests from same module that should still pass
  → This avoids 0 F2P issues
""")

if __name__ == "__main__":
    main()
