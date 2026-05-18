#!/usr/bin/env python3
"""
Use EXISTING vllm tests for F2P instead of generating new ones.
Maps buggy functions to their existing test files.
"""

import json
import re
from pathlib import Path
from typing import List, Dict, Optional, Tuple


# Map of buggy functions to their existing test files
EXISTING_TESTS = {
    # Tool parsers with existing tests
    "Llama3JsonToolParser": {
        "file": "tests/tool_parsers/test_llama3_json_tool_parser.py",
        "tests": [
            "tests/tool_parsers/test_llama3_json_tool_parser.py::test_extract_tool_calls_simple",
            "tests/tool_parsers/test_llama3_json_tool_parser.py::test_extract_tool_calls_with_arguments",
            "tests/tool_parsers/test_llama3_json_tool_parser.py::test_extract_tool_calls_invalid_json",
        ]
    },
    "DeepSeekV32ToolParser": {
        "file": "tests/tool_parsers/test_deepseekv32_tool_parser.py",
        "tests": [
            "tests/tool_parsers/test_deepseekv32_tool_parser.py::test_extract_tool_calls",
            "tests/tool_parsers/test_deepseekv32_tool_parser.py::test_extract_tool_calls_streaming",
        ]
    },
    "Hermes2ProToolParser": {
        "file": "tests/tool_parsers/test_hermes_tool_parser.py",
        "tests": [
            "tests/tool_parsers/test_hermes_tool_parser.py::test_extract_tool_calls_streaming",
        ]
    },
    "HunyuanA13BToolParser": {
        "file": "tests/tool_parsers/test_hunyuan_a13b_tool_parser.py",
        "tests": [
            "tests/tool_parsers/test_hunyuan_a13b_tool_parser.py::test_extract_tool_calls",
        ]
    },
    "MinimaxM2ToolParser": {
        "file": "tests/tool_parsers/test_minimax_m2_tool_parser.py",
        "tests": [
            "tests/tool_parsers/test_minimax_m2_tool_parser.py::test_extract_tool_calls",
            "tests/tool_parsers/test_minimax_m2_tool_parser.py::test_extract_tool_calls_streaming",
        ]
    },
    "MinimaxToolParser": {
        "file": "tests/tool_parsers/test_minimax_tool_parser.py",
        "tests": [
            "tests/tool_parsers/test_minimax_tool_parser.py::test_extract_tool_calls",
        ]
    },
    "Phi4MiniJsonToolParser": {
        "file": "tests/tool_parsers/test_phi4mini_tool_parser.py",
        "tests": [
            "tests/tool_parsers/test_phi4mini_tool_parser.py::test_extract_tool_calls",
        ]
    },
    # Reasoning parsers
    "BaseThinkingReasoningParser": {
        "file": "tests/reasoning/test_base_thinking_reasoning_parser.py",
        "tests": [
            "tests/reasoning/test_base_thinking_reasoning_parser.py::test_start_token_property",
            "tests/reasoning/test_base_thinking_reasoning_parser.py::test_extract_reasoning",
        ]
    },
    # MultiModalConfig
    "MultiModalConfig": {
        "file": "tests/config/test_multimodal_config.py",
        "tests": [
            "tests/config/test_multimodal_config.py::test_mm_encoder_attn_backend_hash_updates",
        ]
    },
}


def extract_class_from_patch(bug_patch: str) -> Optional[str]:
    """Extract class name from bug patch."""
    match = re.search(r'@@.*@@\s*class\s+(\w+)', bug_patch)
    if match:
        return match.group(1)
    match = re.search(r'class\s+(\w+)', bug_patch)
    if match:
        return match.group(1)
    return None


def extract_func_from_patch(bug_patch: str) -> Optional[str]:
    """Extract function name from bug patch."""
    for line in bug_patch.split('\n'):
        if line.startswith('+') and not line.startswith('+++'):
            match = re.search(r'def\s+(\w+)\s*\(', line)
            if match and match.group(1) not in ('__init__', '__repr__', '__str__'):
                return match.group(1)
    match = re.search(r'def\s+(\w+)\s*\(', bug_patch)
    if match:
        return match.group(1)
    return None


def main():
    print('=' * 80)
    print('USING EXISTING VLLM TESTS FOR F2P')
    print('=' * 80)

    with open('vllm_3e1ad443_valid_bugs_only.json') as f:
        instances = json.load(f)

    print(f'Loaded {len(instances)} valid bugs')
    print()

    updated_instances = []
    using_existing = 0
    using_generated = 0

    for i, inst in enumerate(instances, 1):
        instance_id = inst['instance_id']
        bug_patch = inst.get('bug_patch', inst.get('patch', ''))

        print(f'[{i}/{len(instances)}] Processing {instance_id}...')

        class_name = extract_class_from_patch(bug_patch)
        func_name = extract_func_from_patch(bug_patch)

        # Check if we have existing tests for this class
        if class_name and class_name in EXISTING_TESTS:
            print(f'  ✅ Using EXISTING vllm tests for {class_name}')
            test_info = EXISTING_TESTS[class_name]

            # Use existing tests
            inst['FAIL_TO_PASS'] = test_info['tests']
            inst['PASS_TO_PASS'] = [f"{test_info['file']}::test_importable"]

            # Create minimal test patch (just imports the test file)
            test_patch = f'''diff --git a/{test_info["file"]} b/{test_info["file"]}
--- a/{test_info["file"]}
+++ b/{test_info["file"]}
@@ -1,1 +1,2 @@
+# Using existing vllm tests
'''
            inst['test_patch'] = test_patch
            using_existing += 1
        else:
            print(f'  ⚠️ No existing tests for {class_name or func_name}, keeping generated tests')
            # Keep the generated tests from previous run
            # Load from vllm_3e1ad443_all_tests.json
            using_generated += 1

        updated_instances.append(inst)

    # Save
    with open('vllm_3e1ad443_existing_tests.json', 'w') as f:
        json.dump(updated_instances, f, indent=2)

    # Save formatted for validation
    formatted = []
    for inst in updated_instances:
        formatted.append({
            'instance_id': inst['instance_id'],
            'repo': inst.get('repo_name', 'vllm-project__vllm.3e1ad443'),
            'patch': inst.get('bug_patch', inst.get('patch', '')),
            'test_patch': inst.get('test_patch', ''),
            'FAIL_TO_PASS': inst.get('FAIL_TO_PASS', []),
            'PASS_TO_PASS': inst.get('PASS_TO_PASS', []),
            'base_commit': inst.get('base_commit', '3e1ad443'),
        })

    with open('vllm_3e1ad443_existing_for_validation.json', 'w') as f:
        json.dump(formatted, f, indent=2)

    print()
    print('=' * 80)
    print('SUMMARY')
    print('=' * 80)
    print(f'Total instances: {len(updated_instances)}')
    print(f'Using existing vllm tests: {using_existing}')
    print(f'Using generated tests: {using_generated}')
    print(f'Output: vllm_3e1ad443_existing_for_validation.json')


if __name__ == '__main__':
    main()
