#!/usr/bin/env python3
"""
Script to check the specific test function for any vLLM bug instance.

Usage:
    python check_instance_test.py <instance_id>

Example:
    python check_instance_test.py vllm-project__vllm.3e1ad443.502
    python check_instance_test.py 502  # Short form
"""

import json
import sys
from pathlib import Path


def load_instances(filepath="/Users/aditya.singh.001/Desktop/SWE-smith/logs/task_insts/vllm-project__vllm.3e1ad443.json"):
    """Load all instances from the JSON file."""
    with open(filepath) as f:
        return json.load(f)


def find_instance(instances, query):
    """Find an instance by ID or partial match."""
    # Try exact match first
    for inst in instances:
        if inst['instance_id'] == query:
            return inst

    # Try matching just the number (e.g., "502" matches "...502")
    for inst in instances:
        if inst['instance_id'].endswith(f".{query}"):
            return inst

    # Try partial match
    matches = [inst for inst in instances if query in inst['instance_id']]
    if len(matches) == 1:
        return matches[0]
    elif len(matches) > 1:
        print(f"Multiple matches found for '{query}':")
        for m in matches:
            print(f"  - {m['instance_id']}")
        return None

    return None


def analyze_test_quality(instance):
    """Analyze the quality of the test function."""
    test_patch = instance.get('test_patch', '')

    checks = {
        'imports_module': False,
        'reads_source_file': False,
        'executes_function': False,
        'instantiates_class': False,
        'asserts_on_behavior': False,
        'asserts_on_state': False,
        'tests_multiple_cases': False,
        'has_edge_cases': False,
    }

    # Check for source file reading (bad pattern)
    if "open(" in test_patch and "'r'" in test_patch:
        checks['reads_source_file'] = True

    # Check for imports
    if "import " in test_patch:
        checks['imports_module'] = True

    # Check for function calls (execution)
    if "(" in test_patch and not test_patch.count("(") == test_patch.count("assert"):
        checks['executes_function'] = True

    # Check for class instantiation
    if "(" in test_patch and any(word[0].isupper() for word in test_patch.split()):
        checks['instantiates_class'] = True

    # Check for meaningful assertions
    if "assert " in test_patch:
        # Check if asserting on source patterns (weak) vs behavior (strong)
        if '"' in test_patch and "'" in test_patch:
            # Likely checking for string patterns in source
            pass
        else:
            checks['asserts_on_behavior'] = True

    # Count test functions
    test_funcs = [line for line in test_patch.split('\n') if line.strip().startswith('def test_')]
    if len(test_funcs) > 1:
        checks['tests_multiple_cases'] = True

    return checks


def display_instance(instance, detailed=False):
    """Display information about an instance."""
    print("=" * 70)
    print(f"INSTANCE: {instance['instance_id']}")
    print("=" * 70)

    print("\n📋 PROBLEM STATEMENT:")
    print("-" * 70)
    problem = instance.get('problem_statement', 'N/A')
    # Print first few lines
    for line in problem.split('\n')[:10]:
        print(f"  {line}")
    if len(problem.split('\n')) > 10:
        print(f"  ... ({len(problem.split(''))} more characters)")

    print("\n🧪 FAIL-TO-PASS TESTS:")
    print("-" * 70)
    for test in instance.get('FAIL_TO_PASS', []):
        print(f"  • {test}")

    print("\n✅ PASS-TO-PASS TESTS:")
    print("-" * 70)
    p2p_tests = instance.get('PASS_TO_PASS', [])
    for test in p2p_tests[:5]:  # Show first 5
        print(f"  • {test}")
    if len(p2p_tests) > 5:
        print(f"  ... and {len(p2p_tests) - 5} more")

    print("\n📝 TEST PATCH (Test Function Code):")
    print("-" * 70)
    test_patch = instance.get('test_patch', 'No test patch available')
    print(test_patch)
    print("-" * 70)

    print("\n🔍 TEST QUALITY ANALYSIS:")
    print("-" * 70)
    quality = analyze_test_quality(instance)

    score = 0
    for check, result in quality.items():
        status = "✓" if result else "✗"
        print(f"  {status} {check.replace('_', ' ').title()}: {'Yes' if result else 'No'}")
        if result:
            score += 1

    # Calculate quality level
    print(f"\n  Quality Score: {score}/{len(quality)}")
    if score <= 2:
        level = "Level 1: Source Check Only (Poor)"
    elif score <= 4:
        level = "Level 2-3: Partial Runtime (Medium)"
    else:
        level = "Level 4-5: Good Runtime Coverage"
    print(f"  Quality Level: {level}")

    if quality['reads_source_file']:
        print("\n  ⚠️  WARNING: Test reads source file instead of testing runtime behavior!")

    print("\n🔧 BUG PATCH (Changes Made):")
    print("-" * 70)
    bug_patch = instance.get('patch', 'N/A')
    lines = bug_patch.split('\n')
    for line in lines[:30]:  # Show first 30 lines
        if line.startswith('+') or line.startswith('-'):
            print(f"  {line}")
    if len(lines) > 30:
        print(f"  ... ({len(lines) - 30} more lines)")
    print("-" * 70)

    # Test generation metadata
    meta = instance.get('test_generation_meta', {})
    if meta:
        print("\n📊 TEST GENERATION METADATA:")
        print("-" * 70)
        print(f"  Method: {meta.get('method', 'N/A')}")
        print(f"  Patterns: {', '.join(meta.get('patterns_detected', []))}")
        print(f"  Files Changed: {', '.join(meta.get('files_changed', [])[:3])}")

    if detailed:
        print("\n📁 FULL INSTANCE DATA:")
        print("-" * 70)
        print(json.dumps(instance, indent=2)[:1000])
        print("... (truncated)")


def main():
    if len(sys.argv) < 2:
        print("Usage: python check_instance_test.py <instance_id>")
        print("\nExamples:")
        print("  python check_instance_test.py vllm-project__vllm.3e1ad443.502")
        print("  python check_instance_test.py 502")
        print("\nAvailable instances:")

        instances = load_instances()
        for inst in instances:
            print(f"  - {inst['instance_id']}")
        return

    query = sys.argv[1]
    detailed = '--detailed' in sys.argv

    print(f"Loading instances and searching for '{query}'...\n")

    try:
        instances = load_instances()
        instance = find_instance(instances, query)

        if instance:
            display_instance(instance, detailed=detailed)
        else:
            print(f"❌ No instance found matching '{query}'")
            print("\nAvailable instances:")
            for inst in instances:
                print(f"  - {inst['instance_id']}")

    except FileNotFoundError:
        print(f"❌ Instance file not found!")
        print(f"Expected: /Users/aditya.singh.001/Desktop/SWE-smith/logs/task_insts/vllm-project__vllm.3e1ad443.json")


if __name__ == "__main__":
    main()
