#!/usr/bin/env python3
"""
Rust-specific LLM rewrite for generating complex, high-quality bugs.
Generates bugs that compile and have proper test coverage.

Usage: python -m swesmith.bug_gen.llm.rust_rewrite \
    --repo juspay__hyperswitch.fece9bc3 \
    --config configs/bug_gen/lm_rust_complex_bugs.yml \
    --model claude-sonnet-4-5-20251001
"""

import argparse
import json
import litellm
import logging
import os
import re
import subprocess
import tempfile
import yaml
from pathlib import Path
from typing import Any, Optional

from litellm import completion
from litellm.cost_calculator import completion_cost
from swesmith.constants import LOG_DIR_BUG_GEN, PREFIX_BUG, PREFIX_METADATA

logging.getLogger("LiteLLM").setLevel(logging.WARNING)
litellm.drop_params = True
litellm.suppress_debug_info = True


def run_cmd(cmd: list[str], cwd: Optional[str] = None, input_text: Optional[str] = None, timeout: int = 300) -> tuple[int, str, str]:
    """Run a shell command and return result."""
    try:
        result = subprocess.run(
            cmd,
            cwd=cwd,
            input=input_text,
            capture_output=True,
            text=True,
            timeout=timeout
        )
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return -1, "", "Timeout"


def validate_patch_applies(repo_path: str, patch: str) -> bool:
    """Validate that a patch can be applied cleanly."""
    with tempfile.TemporaryDirectory() as tmpdir:
        clone_path = Path(tmpdir) / "repo"
        ret, _, _ = run_cmd(["git", "clone", "--quiet", repo_path, str(clone_path)])
        if ret != 0:
            return False

        ret, _, err = run_cmd(["git", "apply", "-"], cwd=str(clone_path), input_text=patch)
        if ret != 0:
            print(f"  Patch validation failed: {err[:200]}")
            return False

        return True


def validate_compiles(repo_path: str, patch: str, crate_name: Optional[str] = None) -> bool:
    """Validate that patched code compiles."""
    with tempfile.TemporaryDirectory() as tmpdir:
        clone_path = Path(tmpdir) / "repo"
        ret, _, _ = run_cmd(["git", "clone", "--quiet", repo_path, str(clone_path)])
        if ret != 0:
            return False

        ret, _, err = run_cmd(["git", "apply", "-"], cwd=str(clone_path), input_text=patch)
        if ret != 0:
            return False

        # Try to compile
        cmd = ["cargo", "check", "--release"]
        if crate_name:
            cmd.extend(["-p", crate_name])

        ret, _, err = run_cmd(cmd, cwd=str(clone_path), timeout=600)
        if ret != 0:
            print(f"  Compilation failed: {err[:200]}")
            return False

        return True


def generate_test_patch(
    bug_patch: str,
    model: str,
    config: dict
) -> Optional[str]:
    """Generate a test patch that detects the bug."""
    if "test_prompt" not in config:
        return None

    prompt = config["test_prompt"].format(bug_patch=bug_patch)

    try:
        response = completion(
            model=model,
            messages=[{"content": prompt, "role": "user"}],
            n=1,
            temperature=0.3
        )

        content = response.choices[0].message.content

        # Extract diff from response
        diff_match = re.search(r'```diff\s*\n(.*?)```', content, re.DOTALL)
        if diff_match:
            return diff_match.group(1).strip()

        # Try without language marker
        diff_match = re.search(r'```\s*\n(diff.*?)(?=```|$)', content, re.DOTALL)
        if diff_match:
            return diff_match.group(1).strip()

        return None

    except Exception as e:
        print(f"  Test generation failed: {e}")
        return None


def process_entity(
    entity: dict,
    config: dict,
    model: str,
    repo_path: str,
    log_dir: Path
) -> dict[str, Any]:
    """Process a single entity to generate a bug."""
    result = {"n_bugs_generated": 0, "n_generation_failed": 0, "cost": 0.0}

    # Prepare prompt - use full file content for accurate line numbers
    file_content = entity.get("full_file") or entity.get("file_content", "")
    prompt_vars = {
        "file_src_code": file_content,
        "func_signature": entity.get("signature", ""),
        "func_to_write": entity.get("src_code", ""),
        "related_files": entity.get("related_files", ""),
        "line_number": entity.get("line_number", 1),
        "file_path": entity.get("file_path", ""),
    }

    prompt = config["instance"].format(**prompt_vars)

    try:
        # Load API config from environment
        api_key = os.getenv("LITE_LLM_API_KEY", "")
        api_base = os.getenv("LITE_LLM_URL", "")

        response = completion(
            model=f"openai/{model}",
            messages=[
                {"content": config.get("system", ""), "role": "system"},
                {"content": prompt, "role": "user"}
            ],
            n=1,
            temperature=0.7,
            max_tokens=4000,
            api_key=api_key,
            base_url=api_base
        )

        try:
            cost = completion_cost(completion_response=response)
        except Exception:
            cost = 0.0

        result["cost"] = cost

        content = response.choices[0].message.content

        # Extract bug patch - try multiple patterns
        # Some LLMs wrap diffs in ```diff blocks, others output raw diff
        bug_patch = None

        # Pattern 1: Standard markdown code block with diff
        diff_blocks = re.findall(r'```diff\s*\n(.*?)```', content, re.DOTALL)
        if diff_blocks:
            bug_patch = '\n\n'.join(b.strip() for b in diff_blocks if b.strip())

        # Pattern 2: Raw diff output (starts with "diff --git")
        if not bug_patch:
            diff_match = re.search(r'(diff --git.*)', content, re.DOTALL)
            if diff_match:
                # Extract everything from "diff --git" to end (before Explanation)
                raw_diff = diff_match.group(1)
                # Cut off at "Explanation:" if present
                expl_match = re.search(r'\n\n*Explanation:', raw_diff, re.DOTALL)
                if expl_match:
                    raw_diff = raw_diff[:expl_match.start()]
                bug_patch = raw_diff.strip()

        # Pattern 3: Alternative markdown format
        if not bug_patch:
            diff_blocks = re.findall(r'```\s*\n(diff.*?)```', content, re.DOTALL)
            if diff_blocks:
                bug_patch = '\n\n'.join(b.strip() for b in diff_blocks if b.strip())

        if not bug_patch:
            print(f"  No patch found in response for {entity.get('name', 'unknown')}")
            result["n_generation_failed"] += 1
            return result

        # Fix hunk headers (LLM often generates incorrect line counts)
        bug_patch = fix_hunk_headers(bug_patch)

        # Validate all files in patch exist
        if not validate_patch_files_exist(repo_path, bug_patch):
            print(f"  Patch references non-existent files for {entity.get('name', 'unknown')}")
            result["n_generation_failed"] += 1
            return result

        # Validate patch applies
        if not validate_patch_applies(repo_path, bug_patch):
            print(f"  Patch does not apply cleanly for {entity.get('name', 'unknown')}")
            result["n_generation_failed"] += 1
            return result

        # Validate compilation
        crate_name = entity.get("crate_name")
        if not validate_compiles(repo_path, bug_patch, crate_name):
            print(f"  Patch does not compile for {entity.get('name', 'unknown')}")
            result["n_generation_failed"] += 1
            return result

        # Generate test patch
        test_patch = generate_test_patch(bug_patch, model, config)

        # Get actual base commit from repo
        ret, commit_hash, _ = run_cmd(["git", "rev-parse", "HEAD"], cwd=repo_path)
        if ret != 0 or not commit_hash:
            commit_hash = entity.get("base_commit", "HEAD")
        else:
            commit_hash = commit_hash.strip()

        # Extract repo name (without commit suffix)
        repo_name = os.path.basename(repo_path)
        if '.' in repo_name:
            repo_name = repo_name.split('.')[0].replace('__', '/')

        # Create instance
        instance = {
            "instance_id": f"juspay__{repo_name.replace('/', '__')}.{commit_hash[:7]}.{entity.get('name', 'bug')}",
            "repo": f"juspay/{repo_name}",
            "base_commit": commit_hash,
            "version": commit_hash,
            "language": "rust",
            "patch": bug_patch,
            "test_patch": test_patch or "",
            "problem_statement": extract_explanation(content),
            "hints_text": f"Look for {entity.get('name', 'the function')} in {entity.get('file_path', 'the source code')}",
            "FAIL_TO_PASS": [f"regression_{entity.get('name', 'bug')}::test_bug"],
            "PASS_TO_PASS": [],
            "test_cmd": f"cargo test --release -p {crate_name or 'router'} --lib --no-fail-fast -- --nocapture"
        }

        # Save to log directory
        bug_dir = log_dir / f"{entity.get('name', 'unknown')}_{entity.get('id', '0')}"
        bug_dir.mkdir(parents=True, exist_ok=True)

        uuid_str = f"{config['name']}__{hash(bug_patch) & 0xFFFFFFFF:08x}"

        with open(bug_dir / f"{PREFIX_METADATA}__{uuid_str}.json", "w") as f:
            json.dump(instance, f, indent=2)

        with open(bug_dir / f"{PREFIX_BUG}__{uuid_str}.diff", "w") as f:
            f.write(bug_patch)

        if test_patch:
            with open(bug_dir / f"test__{uuid_str}.diff", "w") as f:
                f.write(test_patch)

        print(f"  ✓ Generated bug: {instance['instance_id']}")
        result["n_bugs_generated"] += 1

    except Exception as e:
        print(f"  Error processing {entity.get('name', 'unknown')}: {e}")
        result["n_generation_failed"] += 1

    return result


def fix_hunk_headers(patch_content: str) -> str:
    """Fix hunk headers to have correct line counts.

    LLMs often generate incorrect line counts in hunk headers.
    This function recalculates the correct counts based on actual content.
    """
    lines = patch_content.split('\n')
    result = []

    i = 0
    while i < len(lines):
        line = lines[i]

        # Check if this is a hunk header
        hunk_match = re.match(r'^@@ -(\d+),(\d+) \+(\d+),(\d+) @@', line)
        if hunk_match:
            old_start = int(hunk_match.group(1))
            new_start = int(hunk_match.group(3))

            # Get rest of line after @@ (context)
            header_end = line.find('@@', line.find('@@') + 2) + 2
            rest = line[header_end:]

            # Collect hunk lines
            i += 1
            hunk_lines = []
            old_actual = 0
            new_actual = 0

            while i < len(lines):
                hunk_line = lines[i]

                # Check for next hunk, next file, or end
                if re.match(r'^@@ -', hunk_line) or hunk_line.startswith('diff --git'):
                    break
                if hunk_line.startswith('--- ') or hunk_line.startswith('+++ '):
                    break

                hunk_lines.append(hunk_line)

                if hunk_line.startswith('-') and not hunk_line.startswith('---'):
                    old_actual += 1
                elif hunk_line.startswith('+') and not hunk_line.startswith('+++'):
                    new_actual += 1
                elif hunk_line.startswith('\\'):
                    pass  # No newline marker
                else:
                    old_actual += 1
                    new_actual += 1

                i += 1

            # Write corrected header
            new_header = f'@@ -{old_start},{old_actual} +{new_start},{new_actual} @@{rest}'
            result.append(new_header)

            # Add hunk content
            result.extend(hunk_lines)

            # Don't increment i again
            continue
        else:
            result.append(line)

        i += 1

    return '\n'.join(result)


def validate_patch_files_exist(repo_path: str, patch: str) -> bool:
    """Validate that all files referenced in the patch exist in the repo."""
    # Extract all file paths from the patch
    file_paths = re.findall(r'diff --git a/(\S+) b/\1', patch)

    for file_path in file_paths:
        full_path = Path(repo_path) / file_path
        if not full_path.exists():
            print(f"    File does not exist: {file_path}")
            return False

    return True


def extract_explanation(content: str) -> str:
    """Extract explanation from model response."""
    match = re.search(r'Explanation:\s*(.+?)(?=```|$)', content, re.DOTALL)
    if match:
        return match.group(1).strip()
    return "Bug introduced in the code"


def extract_rust_entities(repo_path: str) -> list[dict]:
    """Extract Rust entities (functions, consts) from the codebase."""
    entities = []

    # Look for const declarations and functions
    rust_files = list(Path(repo_path).rglob("*.rs"))

    for file_path in rust_files:
        # Skip test files and generated files
        if "test" in str(file_path) or "target" in str(file_path):
            continue

        try:
            with open(file_path) as f:
                content = f.read()

            # Build line number mapping
            lines = content.split('\n')
            line_starts = [0]
            for line in lines[:-1]:
                line_starts.append(line_starts[-1] + len(line) + 1)  # +1 for newline

            def get_line_number(pos):
                """Get 1-based line number from character position."""
                for i, start in enumerate(line_starts):
                    if start > pos:
                        return i
                return len(line_starts)

            # Extract const declarations with numeric values
            const_pattern = r'pub\s+const\s+(\w+)\s*:\s*(\w+)\s*=\s*(\d+)\s*;'
            for match in re.finditer(const_pattern, content):
                line_num = get_line_number(match.start())
                entities.append({
                    "name": match.group(1),
                    "type": "const",
                    "file_path": str(file_path.relative_to(repo_path)),
                    "file_content": content,
                    "signature": f"pub const {match.group(1)}: {match.group(2)} = {match.group(3)};",
                    "src_code": match.group(0),
                    "line_number": line_num,
                    "crate_name": detect_crate(repo_path, file_path),
                    "base_commit": "HEAD"
                })

            # Extract function signatures
            func_pattern = r'pub\s+(?:async\s+)?fn\s+(\w+)\s*\([^)]*\)(?:\s*->\s*[^\{]+)?'
            for match in re.finditer(func_pattern, content):
                # Get full function body
                start = match.start()
                line_num = get_line_number(start)
                brace_count = 0
                end = start
                for i, c in enumerate(content[start:]):
                    if c == '{':
                        brace_count += 1
                    elif c == '}':
                        brace_count -= 1
                        if brace_count == 0:
                            end = start + i + 1
                            break

                func_body = content[start:end]

                # Extract context around function (200 lines before for imports/context)
                context_start = max(0, start - 5000)  # ~100 lines of context
                file_context = content[context_start:start] + func_body

                entities.append({
                    "name": match.group(1),
                    "type": "function",
                    "file_path": str(file_path.relative_to(repo_path)),
                    "file_content": file_context,
                    "full_file": content,  # Keep full file for accurate patches
                    "signature": match.group(0),
                    "src_code": func_body,
                    "line_number": line_num,
                    "crate_name": detect_crate(repo_path, file_path),
                    "base_commit": "HEAD"
                })

        except Exception as e:
            print(f"Error reading {file_path}: {e}")

    return entities


def detect_crate(repo_path: str, file_path: Path) -> Optional[str]:
    """Detect the crate name for a file."""
    # Walk up to find Cargo.toml
    current = file_path.parent
    while current != Path(repo_path) and current != current.parent:
        cargo_toml = current / "Cargo.toml"
        if cargo_toml.exists():
            try:
                with open(cargo_toml) as f:
                    content = f.read()
                match = re.search(r'^name\s*=\s*"([^"]+)"', content, re.MULTILINE)
                if match:
                    return match.group(1)
            except Exception:
                pass
        current = current.parent
    return None


def main(
    repo: str,
    config: str,
    model: str,
    n_workers: int = 1,
    max_bugs: int = 10,
):
    """Main entry point for Rust bug generation."""
    # Load config
    with open(config) as f:
        config = yaml.safe_load(f)

    # Resolve repo path
    repo_path = os.path.abspath(repo)
    if not os.path.exists(repo_path):
        print(f"Repository not found: {repo_path}")
        return

    print(f"Processing repository: {repo}")
    print(f"Using model: {model}")
    print(f"Config: {config['name']}")

    # Extract entities
    print("\nExtracting Rust entities...")
    entities = extract_rust_entities(repo_path)
    print(f"Found {len(entities)} entities")

    # Prefer complex function targets (skip trivial const-only entities)
    complex_entities = [
        e for e in entities
        if e.get("type") == "function"
        and len(e.get("src_code", "")) >= 200
        and any(
            kw in e.get("src_code", "").lower()
            for kw in ("validate", "async", "result", "error", "match", "if ")
        )
    ]
    if complex_entities:
        print(f"Filtered to {len(complex_entities)} complex function entities")
        entities = complex_entities
    else:
        entities = [e for e in entities if e.get("type") == "function"]

    # Limit entities
    if max_bugs and len(entities) > max_bugs:
        import random
        random.seed(42)
        entities = random.sample(entities, max_bugs)
        print(f"Limited to {max_bugs} entities")

    # Setup logging
    log_dir = LOG_DIR_BUG_GEN / os.path.basename(repo)
    log_dir.mkdir(parents=True, exist_ok=True)
    print(f"\nLogging bugs to: {log_dir}")

    # Process entities
    stats = {"n_bugs_generated": 0, "n_generation_failed": 0, "cost": 0.0}

    for i, entity in enumerate(entities):
        print(f"\n[{i+1}/{len(entities)}] Processing {entity.get('name', 'unknown')}...")
        result = process_entity(entity, config, model, repo_path, log_dir)

        for k, v in result.items():
            stats[k] += v

    # Print summary
    print(f"\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")
    print(f"Entities processed: {len(entities)}")
    print(f"Bugs generated: {stats['n_bugs_generated']}")
    print(f"Generation failed: {stats['n_generation_failed']}")
    print(f"Total cost: ${stats['cost']:.4f}")
    print(f"\nOutput directory: {log_dir}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Generate complex Rust bugs with proper compilation validation."
    )
    parser.add_argument("--repo", type=str, required=True,
                        help="Path to the Rust repository")
    parser.add_argument("--config", type=str, required=True,
                        help="Path to config YAML file")
    parser.add_argument("--model", type=str, default="claude-sonnet-4-5-20251001",
                        help="Model to use for generation")
    parser.add_argument("--n_workers", type=int, default=1,
                        help="Number of parallel workers")
    parser.add_argument("--max_bugs", type=int, default=10,
                        help="Maximum number of bugs to generate")

    args = parser.parse_args()
    main(**vars(args))
