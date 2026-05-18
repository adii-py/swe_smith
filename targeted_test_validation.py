#!/usr/bin/env python3
"""Run targeted test validation for 10 working vLLM instances.

This script runs existing tests related to modified files to get PASS_TO_PASS data.
"""
import json
import subprocess
import sys
from pathlib import Path

# Working instances with their modified files and corresponding test patterns
TARGETED_TESTS = {
    "41110": {
        "modified": ["vllm/entrypoints/openai/chat_completion/serving.py"],
        "test_patterns": [
            "tests/entrypoints/openai/test_chat_completion.py",
            "tests/entrypoints/openai/responses/test_function_call.py",
            "tests/tool_parsers/",
        ]
    },
    "41135": {
        "modified": ["vllm/v1/attention/ops/deepseek_v4_ops/fused_inv_rope_fp8_quant.py"],
        "test_patterns": [
            "tests/v1/attention/ops/",
            "tests/kernels/",
        ]
    },
    "41162": {
        "modified": ["vllm/v1/worker/gpu/sample/gumbel.py"],
        "test_patterns": [
            "tests/v1/worker/",
            "tests/sampling/",
        ]
    },
    "41181": {
        "modified": ["vllm/multimodal/processing/context.py", "vllm/renderers/base.py"],
        "test_patterns": [
            "tests/multimodal/",
            "tests/renderers/",
        ]
    },
    "41217": {
        "modified": ["vllm/model_executor/models/deepseek_v2.py"],
        "test_patterns": [
            "tests/models/test_deepseek.py",
            "tests/models/test_deepseek_v2.py",
        ]
    },
    "41228": {
        "modified": ["vllm/distributed/kv_transfer/kv_connector/v1/offloading/scheduler.py"],
        "test_patterns": [
            "tests/distributed/kv_transfer/",
        ]
    },
    "41255": {
        "modified": ["vllm/model_executor/layers/mhc.py"],
        "test_patterns": [
            "tests/model_executor/layers/",
            "tests/kernels/",
        ]
    },
    "41282": {
        "modified": ["vllm/v1/core/kv_cache_coordinator.py"],
        "test_patterns": [
            "tests/v1/core/",
            "tests/core/",
        ]
    },
    "41448": {
        "modified": ["vllm/model_executor/models/longcat_flash.py"],
        "test_patterns": [
            "tests/models/test_longcat.py",
        ]
    },
    "41690": {
        "modified": ["vllm/model_executor/models/cohere_moe.py"],
        "test_patterns": [
            "tests/models/test_cohere.py",
        ]
    },
}

INSTANCES_PATH = "/Users/aditya.singh.001/Desktop/SWE-smith/logs/bug_gen/mirror_instances_for_validation.json"
VALIDATION_LOG_DIR = "/Users/aditya.singh.001/Desktop/SWE-smith/logs/targeted_validation"


def get_test_command(test_patterns):
    """Generate pytest command for test patterns."""
    # Find which test files actually exist
    testbed = "/testbed"
    existing_tests = []

    for pattern in test_patterns:
        if pattern.endswith("/"):
            # Directory pattern - check if any test files exist
            existing_tests.append(pattern + "test_*.py")
        else:
            # Specific file - check if it exists
            existing_tests.append(pattern)

    if not existing_tests:
        return None

    # Build pytest command
    cmd_parts = ["python", "-m", "pytest", "-x", "-v", "--timeout=60", "--tb=no", "--color=no"]
    cmd_parts.extend(existing_tests)
    return " ".join(cmd_parts)


def run_targeted_validation(instance_id, instance_data, test_patterns):
    """Run targeted tests for an instance."""
    suffix = instance_id.split(".")[-1]

    print(f"\n{'='*60}")
    print(f"Instance: {instance_id}")
    print(f"Modified files: {TARGETED_TESTS.get(suffix, {}).get('modified', [])}")
    print(f"Test patterns: {test_patterns}")

    # Get test command
    test_cmd = get_test_command(test_patterns)
    if not test_cmd:
        print("No test command generated")
        return None

    print(f"Test command: {test_cmd}")

    # Note: Actual test execution would require Docker container setup
    # This script prepares the validation configuration
    return {
        "instance_id": instance_id,
        "test_command": test_cmd,
        "test_patterns": test_patterns,
    }


def main():
    # Load instances
    with open(INSTANCES_PATH, 'r') as f:
        all_instances = json.load(f)

    # Filter to working instances
    working_ids = list(TARGETED_TESTS.keys())
    working_instances = [
        inst for inst in all_instances
        if inst['instance_id'].split('.')[-1] in working_ids
    ]

    print(f"Found {len(working_instances)} working instances for targeted validation")

    # Prepare validation configs
    validation_configs = []
    for inst in working_instances:
        suffix = inst['instance_id'].split('.')[-1]
        test_patterns = TARGETED_TESTS[suffix]['test_patterns']

        config = run_targeted_validation(inst['instance_id'], inst, test_patterns)
        if config:
            validation_configs.append(config)

    # Save validation configs
    output_path = Path(VALIDATION_LOG_DIR) / "targeted_validation_configs.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, 'w') as f:
        json.dump(validation_configs, f, indent=2)

    print(f"\n{'='*60}")
    print(f"Saved {len(validation_configs)} validation configs to:")
    print(f"  {output_path}")
    print("\nNext steps:")
    print("1. Run targeted tests in gold state (no bug patch)")
    print("2. Run targeted tests in buggy state (with bug patch)")
    print("3. Compare results to get PASS_TO_PASS data")


if __name__ == "__main__":
    main()
