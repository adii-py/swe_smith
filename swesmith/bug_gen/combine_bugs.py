#!/usr/bin/env python3
"""
Combine multiple simple bugs into complex composite bugs.

Usage:
    python -m swesmith.bug_gen.combine_bugs \
        --repo vllm-project__vllm.3e1ad443 \
        --input_dir logs/bug_gen/vllm-project__vllm.3e1ad443 \
        --n_composites 4 \
        --complexity medium
"""

import argparse
import json
import random
import re
import shutil
from pathlib import Path
from typing import Literal

from swesmith.bug_gen.utils import apply_code_change, get_patch
from swesmith.constants import LOG_DIR_BUG_GEN, PREFIX_BUG, PREFIX_METADATA, BugRewrite
from swesmith.profiles import registry


def parse_diff_lines(diff_content: str) -> list[tuple[int, str]]:
    """Parse diff to get (line_number, content) pairs."""
    lines = []
    current_line = 0
    for line in diff_content.split('\n'):
        if line.startswith('@@'):
            # Parse @@ -old_start,old_count +new_start,new_count @@
            match = re.match(r'@@ -\d+,\d+ \+(\d+),\d+ @@', line)
            if match:
                current_line = int(match.group(1))
        elif line.startswith('+') and not line.startswith('+++'):
            lines.append((current_line, line[1:]))
            current_line += 1
        elif not line.startswith('-') and not line.startswith('---'):
            if line:  # Skip empty context lines sometimes
                current_line += 1
    return lines


def combine_bugs(
    repo: str,
    input_dir: Path,
    n_composites: int,
    complexity: Literal["medium", "hard", "expert"]
):
    """Combine simple bugs into composite bugs."""

    rp = registry.get(repo)
    rp.clone()

    # Find all existing bug directories
    bug_dirs = list(input_dir.glob("vllm-project__vllm.3e1ad443__*/*"))
    print(f"Found {len(bug_dirs)} existing bug directories")

    if len(bug_dirs) < 6:
        print("Need at least 6 bugs to create composites")
        return

    # Group by file
    by_file: dict[str, list[Path]] = {}
    for bug_dir in bug_dirs:
        # Extract file path from directory name
        parts = bug_dir.name.split('_')
        if len(parts) >= 2:
            file_key = bug_dir.parent.name.replace('vllm-project__vllm.3e1ad443__', '')
            by_file.setdefault(file_key, []).append(bug_dir)

    print(f"Bugs grouped into {len(by_file)} files")

    # Create composite bugs
    composites_created = 0
    log_dir = LOG_DIR_BUG_GEN / repo

    for i in range(n_composites):
        # Reset repo to clean state
        import subprocess
        subprocess.run(f"cd {repo} && git reset --hard", shell=True, capture_output=True)

        # Select bugs based on complexity
        if complexity == "medium":
            # 2 bugs from same file
            file_with_most = max(by_file.keys(), key=lambda k: len(by_file[k]))
            candidates = by_file[file_with_most]
            if len(candidates) < 2:
                continue
            selected = random.sample(candidates, 2)
        elif complexity == "hard":
            # 2-3 bugs from different files
            if len(by_file) < 2:
                continue
            files = random.sample(list(by_file.keys()), min(2, len(by_file)))
            selected = [random.choice(by_file[f]) for f in files]
        else:  # expert
            # 3 bugs from different files + different abstraction levels
            if len(by_file) < 3:
                continue
            files = random.sample(list(by_file.keys()), min(3, len(by_file)))
            selected = [random.choice(by_file[f]) for f in files]

        print(f"\nCreating {complexity} composite {i+1} from {len(selected)} bugs")

        # Apply each bug
        applied_bugs = []
        for bug_dir in selected:
            # Find the diff file
            diff_files = list(bug_dir.glob("bug__*.diff"))
            if not diff_files:
                continue

            # Read original bug info
            metadata_files = list(bug_dir.glob("metadata__*.json"))
            if metadata_files:
                with open(metadata_files[0]) as f:
                    metadata = json.load(f)

            # Apply the patch
            with open(diff_files[0]) as f:
                patch_content = f.read()

            # Apply patch manually
            result = subprocess.run(
                f"cd {repo} && git apply --allow-empty",
                shell=True,
                input=patch_content,
                capture_output=True,
                text=True
            )

            if result.returncode == 0:
                applied_bugs.append({
                    'dir': bug_dir,
                    'metadata': metadata if metadata_files else {},
                    'patch': patch_content
                })
                print(f"  Applied: {bug_dir.name}")

        if len(applied_bugs) < 2:
            print("  Failed: couldn't apply enough bugs")
            continue

        # Get combined patch
        patch = get_patch(repo, reset_changes=False)
        if not patch:
            print("  Failed: no patch generated")
            continue

        # Create composite record
        composite_dir = log_dir / f"composite_{complexity}_{i+1}"
        composite_dir.mkdir(parents=True, exist_ok=True)

        explanations = [b['metadata'].get('explanation', 'Unknown')[:100] + '...' for b in applied_bugs]
        composite_metadata = {
            'complexity': complexity,
            'component_bugs': [b['dir'].name for b in applied_bugs],
            'explanation': f"Composite {complexity} bug combining:\n" + "\n".join(f"  - {e}" for e in explanations),
            'strategy': f'{complexity}_composite',
            'cost': sum(b['metadata'].get('cost', 0) for b in applied_bugs),
        }

        uuid_str = f"composite_{complexity}_{i+1}_{hash(patch) % 10000:04d}"

        with open(composite_dir / f"{PREFIX_METADATA}__{uuid_str}.json", 'w') as f:
            json.dump(composite_metadata, f, indent=2)

        with open(composite_dir / f"{PREFIX_BUG}__{uuid_str}.diff", 'w') as f:
            f.write(patch)

        print(f"  Created: {composite_dir}")
        composites_created += 1

    print(f"\nTotal composites created: {composites_created}")

    # Cleanup
    shutil.rmtree(repo, ignore_errors=True)


def main():
    parser = argparse.ArgumentParser(description="Combine bugs into composites")
    parser.add_argument("--repo", required=True, help="Repository name")
    parser.add_argument("--input_dir", required=True, help="Input directory with existing bugs")
    parser.add_argument("--n_composites", type=int, default=4, help="Number of composites to create")
    parser.add_argument("--complexity", choices=["medium", "hard", "expert"], default="medium")
    args = parser.parse_args()

    combine_bugs(
        repo=args.repo,
        input_dir=Path(args.input_dir),
        n_composites=args.n_composites,
        complexity=args.complexity
    )


if __name__ == "__main__":
    main()
