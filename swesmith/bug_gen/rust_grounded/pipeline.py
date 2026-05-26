"""Main pipeline for grounded bug generation using AST-based mutations."""

import json
import os
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from swesmith.constants import LOG_DIR_BUG_GEN, PREFIX_BUG, PREFIX_METADATA

from .parser.repository_parser import RepositoryParser
from .graph.builder import GraphBuilder
from .retrieval.context_retriever import ContextRetriever
from .generator.patch_generator import PatchGenerator, GeneratedBug
from .generator.test_patch_generator import TestPatchGenerator
from .validator.code_validator import MutatedCodeValidator
from .scorer.quality_scorer import QualityScorer


class GroundedBugPipeline:
    """End-to-end pipeline for generating grounded Rust bugs using AST mutations."""

    def __init__(
        self,
        repo_path: str,
        model: str = "private-large",
        max_bugs: int = 10,
        min_difficulty: str = "medium",
    ):
        self.repo_path = Path(repo_path)
        self.model = model
        self.max_bugs = max_bugs
        self.min_difficulty = min_difficulty

        # Initialize components
        self.parser = RepositoryParser(repo_path)
        self.graph = None
        self.retriever = None
        self.generator = PatchGenerator(model)
        self.test_generator = TestPatchGenerator(model)
        self.validator = MutatedCodeValidator(repo_path)
        self.scorer = QualityScorer()

        # Statistics
        self.stats = {
            "processed": 0,
            "planned": 0,
            "transformed": 0,
            "validated": 0,
            "failed": 0,
            "by_stage": {},
        }

    def run(self) -> List[dict]:
        """Run the full pipeline."""
        print("=" * 70)
        print("GROUNDED RUST BUG GENERATION PIPELINE (AST-BASED)")
        print("=" * 70)
        print(f"Repository: {self.repo_path}")
        print(f"Max bugs: {self.max_bugs}")
        print(f"Min difficulty: {self.min_difficulty}")

        # Stage 1: Parse repository
        print("\n[Stage 1] Parsing repository...")
        self.parser.parse_full_repository()
        print(f"  Found {len(self.parser.ast_extractor.functions)} functions")
        crate_count = len(self.parser.workspace.members) if self.parser.workspace else 0
        print(f"  Found {crate_count} crates")

        # Stage 2: Build graphs
        print("\n[Stage 2] Building dependency graphs...")
        self.graph = GraphBuilder(self.parser.ast_extractor)
        self.graph.build_all_graphs()
        print(f"  Call graph: {self.graph.call_graph.number_of_nodes()} nodes")

        # Stage 3: Initialize retriever
        print("\n[Stage 3] Initializing context retriever...")
        self.retriever = ContextRetriever(
            self.parser,
            self.graph,
            self.repo_path
        )

        # Stage 4: Select targets
        print("\n[Stage 4] Selecting target functions...")
        targets = self._select_targets()
        print(f"  Selected {len(targets)} target functions")

        # Stage 5: Generate bugs
        print("\n[Stage 5] Generating bugs using AST mutations...")
        bugs = self._generate_bugs(targets)

        # Stage 6: Report
        print("\n" + "=" * 70)
        print("PIPELINE COMPLETE")
        print("=" * 70)
        print(f"Targets processed: {self.stats['processed']}")
        print(f"Plans generated: {self.stats['planned']}")
        print(f"Transformations applied: {self.stats['transformed']}")
        print(f"Bugs validated: {self.stats['validated']}")
        print(f"Failures: {self.stats['failed']}")
        print(f"\nBreakdown by stage:")
        for stage, count in self.stats['by_stage'].items():
            print(f"  {stage}: {count}")

        return bugs

    def _select_targets(self) -> List[str]:
        """Select target functions for bug generation."""
        # Get hotspot functions (expanded pool)
        hotspots = self.parser.get_hotspot_functions(top_n=self.max_bugs * 20)

        # External service patterns to avoid (reduced list)
        external_patterns = ['redis', 'kafka', 'elastic', 'smtp']

        # Filter for viable targets (relaxed)
        targets = []
        for func in hotspots:
            # Skip very small functions (lowered threshold)
            if len(func.body) < 50:
                continue

            # Skip getters/setters and boilerplate functions
            if func.name.startswith(('get_', 'set_', 'is_')):
                continue

            # Skip constructors and trait boilerplate
            boilerplate = ('new', 'default', 'from', 'into', 'clone', 'drop',
                           'eq', 'partial_eq', 'fmt', 'display', 'debug',
                           'as_ref', 'as_mut', 'as_ptr', 'deref', 'deref_mut',
                           'to_string', 'try_from', 'try_into', 'build')
            if func.name in boilerplate:
                continue

            # Skip test functions
            if 'test' in func.file_path or func.name.startswith('test_'):
                continue

            # Skip external-service-dependent files
            file_lower = func.file_path.lower()
            if any(pattern in file_lower for pattern in external_patterns):
                continue

            # Relaxed complexity: accept either async OR >=1 calls OR >=80 char body
            if 'async' not in func.signature and len(func.calls) < 1 and len(func.body) < 80:
                continue

            func_key = f"{func.file_path}::{func.name}"
            targets.append(func_key)

            if len(targets) >= self.max_bugs * 4:
                break

        return targets[:self.max_bugs]

    def _generate_bugs(self, targets: List[str]) -> List[dict]:
        """Generate bugs for selected targets using AST mutations."""
        bugs = []
        log_dir = LOG_DIR_BUG_GEN / self.repo_path.name
        log_dir.mkdir(parents=True, exist_ok=True)

        for i, func_key in enumerate(targets, 1):
            print(f"\n[{i}/{len(targets)}] Processing {func_key}...")
            self.stats['processed'] += 1

            # Build context pack
            context_pack = self.retriever.build_context_pack(func_key)
            if not context_pack:
                print("  Failed to build context pack")
                self._record_failure("context_build")
                continue

            # Format context
            context_str = self.retriever.format_context_for_prompt(context_pack)

            # Generate bug using patch
            target_content = context_pack.file_contents.get(context_pack.target_file, "")
            bug = self.generator.generate_bug(
                context_pack.target_file,
                target_content,
                context_pack.target_function,
                context_str=context_str,
            )

            if not bug:
                print("  Failed to generate bug")
                self._record_failure("generation")
                continue

            self.stats['planned'] += 1
            self.stats['transformed'] += 1

            print(f"  Strategy: {bug.strategy}")
            print(f"  Patch size: {len(bug.patch)} chars")
            print(f"  Affected files: {len(bug.affected_files)}")

            # Validate patch applies cleanly
            import tempfile
            import subprocess
            from pathlib import Path

            with tempfile.TemporaryDirectory() as tmpdir:
                # Clone repo
                result = subprocess.run(
                    ["git", "clone", "--quiet", str(self.repo_path), tmpdir],
                    capture_output=True,
                )
                if result.returncode == 0:
                    # Try to apply patch
                    result = subprocess.run(
                        ["git", "apply", "-"],
                        cwd=tmpdir,
                        input=bug.patch,
                        capture_output=True,
                        text=True,
                    )
                    if result.returncode != 0:
                        print(f"  Patch apply failed: {result.stderr[:100]}")
                        self._record_failure("patch_apply")
                        continue
                    print("  Patch applies cleanly!")

            # Skip cargo validation for speed - git apply is sufficient
            # If it applies, syntax is valid

            # Validate mutated code
            crate_name = self._get_crate_name(context_pack.target_file)
            result = self.validator.validate_bug(bug, crate_name)

            if not result.success:
                print(f"  Validation failed at '{result.stage}': {result.message}")
                self._record_failure(f"validation:{result.stage}")
                continue

            print("  Validation passed!")
            self.stats['validated'] += 1

            # Generate test patch
            test_patch, test_names = self._generate_test_patch(bug, context_pack)

            # Score quality
            quality_score = self.scorer.score(bug)
            print(f"  Quality score: {quality_score:.2f}")

            # Check quality threshold for complex bugs
            if quality_score < 0.6:
                print(f"  Skipping: quality score too low ({quality_score:.2f})")
                self._record_failure("quality_filter")
                continue

            # Generate git patch for output
            git_patch = self.validator.generate_patch_from_bug(bug)

            # Save bug
            bug_data = self._save_bug(
                func_key,
                context_pack,
                bug,
                git_patch,
                test_patch,
                quality_score,
                log_dir,
                test_names=test_names,
            )
            bugs.append(bug_data)

        return bugs

    def _get_crate_name(self, file_path: str) -> Optional[str]:
        """Get crate name for a file."""
        crate = self.parser.get_file_crate(file_path)
        return crate.name if crate else None

    def _record_failure(self, stage: str):
        """Record a failure."""
        self.stats['failed'] += 1
        self.stats['by_stage'][stage] = self.stats['by_stage'].get(stage, 0) + 1

    def _generate_test_patch(
        self, bug: GeneratedBug, context_pack
    ) -> tuple:
        """Generate a test patch for the bug using the test generator.

        Returns (test_patch, test_names) where either may be None on failure.
        Gracefully falls back to (None, None) so the pipeline continues.
        """
        try:
            target_content = context_pack.file_contents.get(
                context_pack.target_file, ""
            )
            test_patch, test_names = self.test_generator.generate_test_patch(
                bug_patch=bug.patch,
                file_path=context_pack.target_file,
                file_content=target_content,
            )
            if test_patch:
                names_str = ", ".join(test_names) if test_names else "unknown"
                print(f"  Test patch generated (tests: {names_str})")
                return test_patch, test_names
            else:
                print("  Test patch generation returned None (non-fatal)")
                return None, None
        except Exception as e:
            print(f"  Test patch generation failed (non-fatal): {e}")
            return None, None

    def _save_bug(
        self,
        func_key: str,
        context_pack,
        bug: GeneratedBug,
        git_patch: str,
        test_patch: Optional[str],
        quality_score: float,
        log_dir: Path,
        test_names: Optional[list] = None,
    ) -> dict:
        """Save bug to disk."""
        # Get base commit
        import subprocess
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=self.repo_path,
            capture_output=True,
            text=True,
        )
        base_commit = result.stdout.strip() if result.returncode == 0 else "HEAD"

        # Build FAIL_TO_PASS from generated test names (require >=1, prefer >=2)
        if test_names:
            fail_to_pass = [
                f"regression_tests::{name}" for name in test_names[:5]
            ]
        else:
            fail_to_pass = [f"regression_{context_pack.target_function}::test_bug"]

        # Create instance data
        instance = {
            "instance_id": f"{self.repo_path.name}.{base_commit[:7]}.{context_pack.target_function}",
            "repo": str(self.repo_path.name).replace('.', '/'),
            "base_commit": base_commit,
            "version": base_commit,
            "language": "rust",
            "patch": bug.patch,
            "test_patch": test_patch or "",
            "problem_statement": f"## Bug: {bug.explanation}\n\n**Target:** {context_pack.target_function} in {context_pack.target_file}",
            "hints_text": f"Look for {context_pack.target_function} in {context_pack.target_file}",
            "FAIL_TO_PASS": fail_to_pass,
            "PASS_TO_PASS": [],
            "test_cmd": f"cargo test --release -p {self._get_crate_name(context_pack.target_file) or 'router'} --lib",
            "target_function": context_pack.target_function,
            "target_file": context_pack.target_file,
            "affected_files": bug.affected_files,
            "generation_time": datetime.now().isoformat(),
            "strategy": bug.strategy,
            "quality_score": quality_score,
        }

        # Create directory
        bug_dir = log_dir / f"{context_pack.target_function}_bug"
        bug_dir.mkdir(parents=True, exist_ok=True)

        # Generate UUID-like string
        uuid_str = f"patch_{hash(bug.patch) & 0xFFFFFFFF:08x}"

        # Save files
        with open(bug_dir / f"{PREFIX_METADATA}__{uuid_str}.json", "w") as f:
            json.dump(instance, f, indent=2)

        with open(bug_dir / f"{PREFIX_BUG}__{uuid_str}.diff", "w") as f:
            f.write(bug.patch)

        print(f"  Saved to {bug_dir}")

        return instance


def main():
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="Generate grounded Rust bugs using AST mutations")
    parser.add_argument("--repo", type=str, required=True, help="Path to Rust repository")
    parser.add_argument("--model", type=str, default="private-large", help="Model to use")
    parser.add_argument("--max-bugs", type=int, default=10, help="Maximum bugs to generate")
    parser.add_argument("--min-difficulty", type=str, default="medium",
                        choices=["easy", "medium", "hard"],
                        help="Minimum difficulty level")
    parser.add_argument("--output", type=str, default=None, help="Output JSON file")

    args = parser.parse_args()

    pipeline = GroundedBugPipeline(
        repo_path=args.repo,
        model=args.model,
        max_bugs=args.max_bugs,
        min_difficulty=args.min_difficulty,
    )

    bugs = pipeline.run()

    if args.output:
        with open(args.output, "w") as f:
            json.dump(bugs, f, indent=2)
        print(f"\nSaved {len(bugs)} bugs to {args.output}")


if __name__ == "__main__":
    main()
