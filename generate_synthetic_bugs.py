#!/usr/bin/env python3
"""
Generate synthetic bugs using LLM that compile and are detectable.

Uses the lm_unified_bugs.yml configuration to generate bugs that:
1. Are syntactically correct (compile)
2. Have proper patch format
3. Are detectable by tests
"""

import json
import os
import re
import yaml
import requests
from pathlib import Path
from difflib import unified_diff


def load_config(config_path: str) -> dict:
    """Load bug generation config."""
    with open(config_path) as f:
        return yaml.safe_load(f)


def call_llm_for_bug(func_code: str, func_signature: str, config: dict) -> dict:
    """Call LLM to generate a bug for the given function."""

    # Load .env file
    from dotenv import load_dotenv
    load_dotenv('/Users/aditya.singh.001/Desktop/SWE-smith/.env')

    system_prompt = config.get('system', '')
    instance_template = config.get('instance', '')

    # Format the prompt
    prompt = instance_template.format(
        func_signature=func_signature,
        file_src_code=func_code
    )

    # Call LLM via LiteLLM proxy (from .env)
    lite_llm_url = os.getenv("LITE_LLM_URL", "https://grid.ai.juspay.net")
    api_key = os.getenv("LITE_LLM_API_KEY")

    if not api_key:
        print("  No API key found, returning None")
        return None

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }

    try:
        response = requests.post(
            f"{lite_llm_url}/chat/completions",
            headers=headers,
            json={
                "model": "private-large",
                "max_tokens": 2000,
                "temperature": 0.2,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt}
                ]
            },
            timeout=120
        )
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]
    except Exception as e:
        print(f"  LLM call failed: {e}")
        return None


def extract_bug_from_response(response: str) -> dict:
    """Extract bug type, explanation, and code from LLM response."""
    result = {
        'bug_type': '',
        'explanation': '',
        'buggy_code': ''
    }

    # Extract bug type
    bug_type_match = re.search(r'Bug Type:\s*(.+?)(?:\n|$)', response, re.IGNORECASE)
    if bug_type_match:
        result['bug_type'] = bug_type_match.group(1).strip()

    # Extract explanation
    expl_match = re.search(r'Explanation:\s*(.+?)(?:\n\n|\n```|$)', response, re.DOTALL | re.IGNORECASE)
    if expl_match:
        result['explanation'] = expl_match.group(1).strip()

    # Extract code block
    code_match = re.search(r'```(?:python|rust)?\n(.*?)\n```', response, re.DOTALL)
    if code_match:
        result['buggy_code'] = code_match.group(1).strip()

    return result


def create_unified_diff(original_code: str, buggy_code: str, file_path: str) -> str:
    """Create a proper unified diff patch."""
    original_lines = original_code.splitlines(keepends=True)
    buggy_lines = buggy_code.splitlines(keepends=True)

    # Ensure lines end with newlines
    if original_lines and not original_lines[-1].endswith('\n'):
        original_lines[-1] += '\n'
    if buggy_lines and not buggy_lines[-1].endswith('\n'):
        buggy_lines[-1] += '\n'

    diff = unified_diff(
        original_lines,
        buggy_lines,
        fromfile=f'a/{file_path}',
        tofile=f'b/{file_path}',
    )

    return ''.join(diff)


def generate_synthetic_bug_for_instance(instance: dict, config: dict) -> str:
    """Generate a synthetic bug for an instance."""
    instance_id = instance.get('instance_id', '')
    print(f"\nProcessing: {instance_id}")

    # Get file info from patch
    patch = instance.get('patch', '')
    file_path = None

    # Extract file path from patch
    for line in patch.split('\n'):
        if line.startswith('+++ b/'):
            file_path = line[6:].strip()
            break
        elif line.startswith('--- a/'):
            file_path = line[6:].strip()
            break

    if not file_path:
        print("  No file path found in patch")
        return None

    print(f"  File: {file_path}")

    # Fetch original file content
    repo = 'juspay/hyperswitch'
    commit = 'fece9bc3'
    url = f"https://raw.githubusercontent.com/{repo}/{commit}/{file_path}"

    try:
        resp = requests.get(url, timeout=30)
        if resp.status_code != 200:
            print(f"  Failed to fetch file: {resp.status_code}")
            return None
        original_code = resp.text
    except Exception as e:
        print(f"  Error fetching file: {e}")
        return None

    # Extract a function from the file
    # For now, use a simple heuristic: find the first function
    func_match = re.search(r'(pub\s+)?(async\s+)?fn\s+(\w+)\s*\([^)]*\)\s*(->\s*[^{]+)?\s*\{', original_code)
    if not func_match:
        print("  No function found in file")
        return None

    func_name = func_match.group(3)
    func_signature = func_match.group(0).rstrip('{').strip()

    print(f"  Function: {func_name}")
    print(f"  Calling LLM to generate bug...")

    # Call LLM
    response = call_llm_for_bug(original_code, func_signature, config)
    if not response:
        return None

    # Extract bug info
    bug_info = extract_bug_from_response(response)
    if not bug_info['buggy_code']:
        print("  No buggy code extracted from response")
        return None

    print(f"  Bug type: {bug_info['bug_type']}")
    print(f"  Explanation: {bug_info['explanation'][:80]}...")

    # Create patch
    patch = create_unified_diff(original_code, bug_info['buggy_code'], file_path)

    if not patch:
        print("  Failed to create patch")
        return None

    print(f"  ✓ Created patch ({len(patch)} chars)")
    return patch


def main():
    """Generate synthetic bugs for instances."""

    config_path = Path('configs/bug_gen/lm_unified_bugs.yml')
    input_path = Path('logs/bug_gen/juspay__hyperswitch.fece9bc3/pr_mirror/final_5_with_tests.json')
    output_path = Path('logs/bug_gen/juspay__hyperswitch.fece9bc3/pr_mirror/synthetic_bugs.json')

    print("=" * 70)
    print("GENERATING SYNTHETIC BUGS WITH LLM")
    print("=" * 70)

    # Load config
    print(f"\nLoading config: {config_path}")
    config = load_config(config_path)

    # Load instances
    print(f"Loading instances: {input_path}")
    with open(input_path) as f:
        data = json.load(f)

    print(f"Found {len(data)} instances\n")

    # Generate bugs
    success = 0
    for inst in data:
        bug_patch = generate_synthetic_bug_for_instance(inst, config)

        if bug_patch:
            inst['patch'] = bug_patch
            inst['_synthetic_bug'] = True
            success += 1

    # Save
    with open(output_path, 'w') as f:
        json.dump(data, f, indent=2)

    print(f"\n{'=' * 70}")
    print(f"Results: {success}/{len(data)} synthetic bugs generated")
    print(f"Saved to: {output_path}")

    if success > 0:
        print("\nNext: Run validation on synthetic bugs")
    else:
        print("\nNo synthetic bugs were generated. Check LLM availability.")


if __name__ == '__main__':
    main()
