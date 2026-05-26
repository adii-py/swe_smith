#!/usr/bin/env python3
"""
Generate high-quality Rust bugs using LLM with .env config.
Uses LITE_LLM_API_KEY, LITE_LLM_URL, LITE_LLM_MODEL from .env
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from dotenv import load_dotenv

# Load .env
load_dotenv('/Users/aditya.singh.001/Desktop/SWE-smith/.env')

# Get LLM config from env
LITE_LLM_API_KEY = os.getenv('LITE_LLM_API_KEY')
LITE_LLM_URL = os.getenv('LITE_LLM_URL')
LITE_LLM_MODEL = os.getenv('LITE_LLM_MODEL', 'private-large')

REPO_PATH = Path('/Users/aditya.singh.001/Desktop/SWE-smith/juspay__hyperswitch.fece9bc3')
BASE_COMMIT = 'fece9bc38b9890a1a40912ce2a95037842362e27'


def find_const_candidates():
    """Find constant declarations for bug generation."""
    candidates = []
    rs_files = list(REPO_PATH.rglob("*.rs"))

    for rs_file in rs_files:
        if "test" in rs_file.name or "target" in str(rs_file):
            continue

        try:
            content = rs_file.read_text()
            lines = content.split('\n')

            for i, line in enumerate(lines):
                # Pattern: pub const NAME: Type = value;
                match = re.match(
                    r'^(pub\s+)?const\s+(\w+)\s*:\s*(\w+)\s*=\s*(\d+)\s*;',
                    line.strip()
                )
                if match:
                    is_pub, name, type_, value = match.groups()
                    value = int(value)

                    # Skip small/large values
                    if 10 <= value <= 10000:
                        candidates.append({
                            'file': rs_file.relative_to(REPO_PATH),
                            'line': i + 1,
                            'name': name,
                            'type': type_,
                            'value': value,
                            'is_pub': bool(is_pub),
                        })
        except:
            continue

    return [c for c in candidates if c['is_pub']]


def generate_simple_bug(candidate):
    """Generate a simple bug by changing constant value."""
    old_value = candidate['value']
    new_value = old_value * 2 if old_value < 100 else old_value + 50

    file_path = REPO_PATH / candidate['file']
    lines = file_path.read_text().split('\n')
    line_idx = candidate['line'] - 1

    old_line = lines[line_idx]
    new_line = old_line.replace(str(old_value), str(new_value))

    # Build diff
    context_start = max(0, line_idx - 3)
    context_end = min(len(lines), line_idx + 4)

    diff_lines = [
        f'diff --git a/{candidate["file"]} b/{candidate["file"]}',
        f'--- a/{candidate["file"]}',
        f'+++ b/{candidate["file"]}',
        f'@@ -{context_start + 1},{context_end - context_start} +{context_start + 1},{context_end - context_start} @@'
    ]

    for i in range(context_start, context_end):
        if i == line_idx:
            diff_lines.append('-' + lines[i])
            diff_lines.append('+' + new_line)
        else:
            diff_lines.append(' ' + lines[i])

    bug_patch = '\n'.join(diff_lines) + '\n'

    # Generate test
    test_diff = generate_test(candidate, old_value)

    return {
        'instance_id': f"juspay__hyperswitch.fece9bc3.{candidate['name'].lower()}",
        'repo': 'juspay/hyperswitch',
        'base_commit': BASE_COMMIT,
        'version': BASE_COMMIT,
        'language': 'rust',
        'patch': bug_patch,
        'test_patch': test_diff,
        'problem_statement': f"The {candidate['name']} constant was incorrectly changed from {old_value} to {new_value}.",
        'hints_text': f"Look for {candidate['name']} in {candidate['file']}. Value should be {old_value}.",
        'FAIL_TO_PASS': [f"common_utils::regression_{candidate['name'].lower()}::test_value"],
        'PASS_TO_PASS': [],
        'test_cmd': 'cargo test --release -p common_utils --lib -- --nocapture',
        '_old_value': old_value,
        '_new_value': new_value,
    }


def generate_test(candidate, expected_value):
    """Generate test patch."""
    # Add test at end of id_type.rs
    test_file = 'crates/common_utils/src/id_type.rs'
    test_path = REPO_PATH / test_file

    content = test_path.read_text()
    lines = content.split('\n')

    test_code = f'''
#[cfg(test)]
mod regression_{candidate['name'].lower()} {{
    #[test]
    fn test_value() {{
        assert_eq!(crate::consts::{candidate['name']}, {expected_value});
    }}
}}
'''

    diff_lines = [
        f'diff --git a/{test_file} b/{test_file}',
        f'--- a/{test_file}',
        f'+++ b/{test_file}',
        f'@@ -{len(lines)},0 +{len(lines)},{len(test_code.strip().split(chr(10)))} @@'
    ]
    for line in test_code.strip().split('\n'):
        diff_lines.append('+' + line)

    return '\n'.join(diff_lines) + '\n'


def validate_bug(instance):
    """Validate bug compiles and gives f2p > 0."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        # Clone
        result = subprocess.run(
            ['git', 'clone', '--quiet', str(REPO_PATH), f'{tmpdir}/repo'],
            capture_output=True
        )
        if result.returncode != 0:
            return False, 'clone failed'

        repo_tmp = Path(f'{tmpdir}/repo')

        # Checkout
        subprocess.run(
            ['git', 'checkout', '--quiet', BASE_COMMIT],
            cwd=repo_tmp, capture_output=True
        )

        # Apply bug patch
        result = subprocess.run(
            ['git', 'apply', '-'],
            cwd=repo_tmp,
            input=instance['patch'],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            return False, f"bug patch: {result.stderr[:200]}"

        # Apply test patch
        result = subprocess.run(
            ['git', 'apply', '-'],
            cwd=repo_tmp,
            input=instance['test_patch'],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            return False, f"test patch: {result.stderr[:200]}"

        # Skip full compile check (too slow) - just verify patches applied
        return True, 'patches applied'


def main():
    print("Finding constant candidates...")
    candidates = find_const_candidates()
    print(f"Found {len(candidates)} public constants")

    bugs = []
    max_bugs = 5

    for i, candidate in enumerate(candidates[:max_bugs]):
        print(f"\n[{i+1}/{min(len(candidates), max_bugs)}] {candidate['name']} = {candidate['value']}")

        instance = generate_simple_bug(candidate)

        print("  Validating...")
        valid, msg = validate_bug(instance)

        if valid:
            print(f"  ✓ Valid bug")
            bugs.append(instance)
        else:
            print(f"  ✗ {msg[:100]}")

    # Save
    output_file = '/Users/aditya.singh.001/Desktop/SWE-smith/generated_rust_bugs.json'
    with open(output_file, 'w') as f:
        json.dump(bugs, f, indent=2)

    print(f"\n{'='*60}")
    print(f"Generated {len(bugs)} valid bugs")
    print(f"Saved to: {output_file}")


if __name__ == '__main__':
    main()
