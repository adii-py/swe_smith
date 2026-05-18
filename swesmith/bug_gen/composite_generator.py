#!/usr/bin/env python3
"""
Strategy 4: Bug Composition - Combine multiple simple bugs into complex ones.

This module implements bug composition strategies:
- Medium: 2-3 procedural bugs on related code paths
- Hard: Cross-file bugs + timing/concurrency aspects
- Expert: Multi-layer architectural bugs

Usage:
    python -m swesmith.bug_gen.composite_generator \
        --repo vllm-project/vllm \
        --strategy medium \
        --n_bugs 5
"""

import argparse
import json
import random
from pathlib import Path
from typing import Literal

from swesmith.bug_gen.llm.modify import gen_bug_from_code_lm
from swesmith.bug_gen.procedural.python.base import PythonProceduralModifier
from swesmith.constants import CodeEntity
from swesmith.profiles import registry


class CompositeBugGenerator:
    """Generate complex bugs by composing multiple simple bugs."""

    def __init__(self, repo: str, complexity: Literal["medium", "hard", "expert"]):
        self.repo = repo
        self.complexity = complexity
        self.rp = registry.get(repo)
        self.rp.clone()

    def generate_medium_composite(
        self, candidates: list[CodeEntity]
    ) -> dict:
        """
        Generate MEDIUM composite bug by stacking 2-3 related bugs.

        Strategy:
        - Find related functions (same module, caller/callee)
        - Apply complementary bugs that interact
        - Example: Wrong validation + incorrect error handling
        """
        if len(candidates) < 2:
            return None

        # Select 2-3 related candidates
        num_bugs = random.choice([2, 3])
        selected = random.sample(candidates, min(num_bugs, len(candidates)))

        bugs = []
        explanations = []

        for candidate in selected:
            # Generate different types of bugs for each
            bug_types = ["logic", "state", "validation"]
            bug_type = random.choice(bug_types)

            # Create simple bug using procedural or LLM
            simple_bug = self._generate_simple_bug(candidate, bug_type)
            if simple_bug:
                bugs.append(simple_bug)
                explanations.append(f"{candidate.name}: {simple_bug['explanation']}")

        if len(bugs) < 2:
            return None

        return {
            "complexity": "medium",
            "components": [b["entity"] for b in bugs],
            "patches": [b["patch"] for b in bugs],
            "explanation": "Composite bug involving:\n" + "\n".join(f"  - {e}" for e in explanations),
            "strategy": "medium_composite",
        }

    def generate_hard_composite(
        self, candidates: list[CodeEntity]
    ) -> dict:
        """
        Generate HARD composite bug with cross-file + concurrency aspects.

        Strategy:
        - Find bugs across dependent files
        - Add race condition or timing aspect
        - Make bugs interact non-deterministically
        """
        if len(candidates) < 3:
            return None

        # Group by file to ensure cross-file
        by_file = {}
        for c in candidates:
            by_file.setdefault(c.file_path, []).append(c)

        if len(by_file) < 2:
            return None

        # Select from different files
        selected_files = random.sample(list(by_file.keys()), min(3, len(by_file)))
        selected = [random.choice(by_file[f]) for f in selected_files]

        bugs = []
        for i, candidate in enumerate(selected):
            if i == 0:
                # First bug: state corruption
                bug = self._generate_simple_bug(candidate, "state_corruption")
            elif i == 1:
                # Second bug: race condition
                bug = self._generate_simple_bug(candidate, "race")
            else:
                # Third bug: error handling
                bug = self._generate_simple_bug(candidate, "error_handling")

            if bug:
                bugs.append(bug)

        if len(bugs) < 2:
            return None

        return {
            "complexity": "hard",
            "components": [b["entity"] for b in bugs],
            "patches": [b["patch"] for b in bugs],
            "files_involved": list(set(b["file"] for b in bugs)),
            "explanation": (
                "Cross-file bug with timing aspects:\n"
                "  - State corruption in one component\n"
                "  - Race condition in dependent component\n"
                "  - Cascading error handling failure"
            ),
            "strategy": "hard_composite",
        }

    def generate_expert_composite(
        self, candidates: list[CodeEntity]
    ) -> dict:
        """
        Generate EXPERT composite bug - architectural level.

        Strategy:
        - Break API contracts across multiple interfaces
        - Invalidate architectural assumptions
        - Create cascading failure modes
        """
        if len(candidates) < 4:
            return None

        # Select candidates from different abstraction layers
        # (API layer, business logic, data access)
        selected = random.sample(candidates, min(4, len(candidates)))

        bugs = []
        layers = ["API_contract", "business_logic", "data_consistency", "resource_mgmt"]

        for i, candidate in enumerate(selected[: len(layers)]):
            bug = self._generate_simple_bug(candidate, layers[i])
            if bug:
                bugs.append(bug)

        if len(bugs) < 3:
            return None

        return {
            "complexity": "expert",
            "components": [b["entity"] for b in bugs],
            "patches": [b["patch"] for b in bugs],
            "files_involved": list(set(b["file"] for b in bugs)),
            "layers_affected": layers[: len(bugs)],
            "explanation": (
                "Architectural bug spanning multiple layers:\n"
                "  - Broken API contract\n"
                "  - Inconsistent business logic\n"
                "  - Data consistency violation\n"
                "  - Resource management flaw"
            ),
            "strategy": "expert_composite",
        }

    def _generate_simple_bug(self, entity: CodeEntity, bug_type: str) -> dict:
        """Generate a simple bug of specified type."""
        # This would integrate with procedural generators or LLM
        # For now, return placeholder structure
        return {
            "entity": entity.name,
            "file": entity.file_path,
            "type": bug_type,
            "patch": f"<patch_for_{entity.name}>",
            "explanation": f"{bug_type} bug in {entity.name}",
        }

    def generate(self, n_bugs: int = 5) -> list[dict]:
        """Generate n composite bugs of specified complexity."""
        candidates = self.rp.extract_entities()
        if not candidates:
            return []

        generator_map = {
            "medium": self.generate_medium_composite,
            "hard": self.generate_hard_composite,
            "expert": self.generate_expert_composite,
        }

        generator = generator_map.get(self.complexity)
        if not generator:
            return []

        results = []
        for _ in range(n_bugs):
            composite = generator(candidates)
            if composite:
                results.append(composite)

        return results


def main():
    parser = argparse.ArgumentParser(description="Generate composite bugs")
    parser.add_argument("--repo", required=True, help="Repository name")
    parser.add_argument(
        "--strategy",
        choices=["medium", "hard", "expert"],
        required=True,
        help="Complexity level",
    )
    parser.add_argument("--n_bugs", type=int, default=5, help="Number of bugs to generate")
    parser.add_argument("--output", type=str, default="composite_bugs.json", help="Output file")

    args = parser.parse_args()

    print(f"Generating {args.n_bugs} {args.strategy} composite bugs for {args.repo}")

    gen = CompositeBugGenerator(args.repo, args.strategy)
    bugs = gen.generate(args.n_bugs)

    # Save results
    output_path = Path(args.output)
    with open(output_path, "w") as f:
        json.dump(bugs, f, indent=2)

    print(f"Generated {len(bugs)} composite bugs")
    print(f"Saved to: {output_path}")

    # Print summary
    for i, bug in enumerate(bugs, 1):
        print(f"\n{i}. {bug['strategy']}")
        print(f"   Files: {len(bug.get('files_involved', []))}")
        print(f"   Components: {len(bug['components'])}")


if __name__ == "__main__":
    main()
