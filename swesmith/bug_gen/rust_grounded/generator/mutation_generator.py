"""Bug generation using AST-based mutations (NOT patches)."""

import os
from dataclasses import dataclass
from typing import Optional, Dict

from ..transformer.mutation_planner import MutationPlanner, MutationPlan
from ..transformer.ast_transformer import RustAstTransformer, TransformResult


@dataclass
class GeneratedBug:
    """Generated bug with mutation plan and transformed code."""
    mutation_plan: MutationPlan
    original_files: Dict[str, str]
    modified_files: Dict[str, str]
    explanation: str
    affected_files: list
    validation_status: str = "pending"


class MutationBugGenerator:
    """Generate bugs using AST-based mutations."""

    def __init__(self, model: str = "private-large"):
        self.planner = MutationPlanner(model)
        self.transformer = RustAstTransformer()
        self.model = model
        self.api_key = os.getenv("LITE_LLM_API_KEY", "")
        self.api_base = os.getenv("LITE_LLM_URL", "")

    def generate_bug(
        self,
        context_str: str,
        file_contents: Dict[str, str],
        max_retries: int = 3
    ) -> Optional[GeneratedBug]:
        """Generate a bug using mutation planning and AST transformation."""

        # Step 1: Generate mutation plan (JSON)
        plan = self.planner.generate_plan(context_str, max_retries)
        if not plan:
            print("  Failed to generate mutation plan")
            return None

        print(f"  Generated plan: {plan.strategy}")

        # Step 2: Apply mutations programmatically
        results = self.transformer.apply_mutations(file_contents, plan)

        # Check if all mutations succeeded
        modified_files = {}
        for file_path, result in results.items():
            if not result.success:
                print(f"  Mutation failed for {file_path}: {result.error_message}")

                # Try to refine the plan
                refined_plan = self.planner.refine_plan(
                    plan, result.error_message, context_str
                )
                if refined_plan:
                    print("  Retrying with refined plan...")
                    results = self.transformer.apply_mutations(file_contents, refined_plan)

                    # Check refined results
                    for fp, r in results.items():
                        if not r.success:
                            print(f"  Refined mutation also failed: {r.error_message}")
                            return None
                        modified_files[fp] = r.modified_code
                    plan = refined_plan
                else:
                    return None
            else:
                modified_files[file_path] = result.modified_code

        print(f"  Applied {len(plan.mutations)} mutations successfully")

        # Step 3: Create bug object
        return GeneratedBug(
            mutation_plan=plan,
            original_files=dict(file_contents),
            modified_files=modified_files,
            explanation=plan.affected_behavior,
            affected_files=list(modified_files.keys()),
        )

    def generate_test_for_bug(
        self,
        bug: GeneratedBug,
        context_str: str,
    ) -> Optional[str]:
        """Generate a regression test for the bug."""
        from litellm import completion

        # Create diff-style view for test generation
        diffs = []
        for file_path in bug.affected_files:
            if file_path in bug.original_files and file_path in bug.modified_files:
                original = bug.original_files[file_path]
                modified = bug.modified_files[file_path]

                # Find changed lines
                orig_lines = original.split('\n')
                mod_lines = modified.split('\n')

                for i, (o, m) in enumerate(zip(orig_lines, mod_lines)):
                    if o != m:
                        diffs.append(f"- {o}")
                        diffs.append(f"+ {m}")

        diff_view = '\n'.join(diffs) if diffs else "See mutation plan"

        test_prompt = f"""Given this bug mutation plan:

Strategy: {bug.mutation_plan.strategy}
Affected Behavior: {bug.mutation_plan.affected_behavior}
Mutations:
"""
        for i, m in enumerate(bug.mutation_plan.mutations, 1):
            test_prompt += f"{i}. {m.type}: {m.reasoning}\n"

        test_prompt += f"""
Code Changes:
```diff
{diff_view}
```

And this context:
{context_str}

Generate a REGRESSION TEST that:
1. Tests the buggy behavior (would fail before fix)
2. Uses the existing test patterns in the codebase
3. Is minimal and focused
4. Can detect the introduced bug

Output ONLY the test code in ```rust blocks."""

        try:
            response = completion(
                model=f"openai/{self.model}",
                messages=[
                    {"role": "user", "content": test_prompt}
                ],
                temperature=0.3,
                max_tokens=1500,
                api_key=self.api_key,
                base_url=self.api_base,
            )

            content = response.choices[0].message.content

            # Extract test code
            import re
            match = re.search(r'```rust\s*\n(.*?)```', content, re.DOTALL)
            if match:
                return match.group(1).strip()

            return None

        except Exception as e:
            print(f"Test generation failed: {e}")
            return None

    def create_git_patch(
        self,
        bug: GeneratedBug,
        repo_path: str,
    ) -> str:
        """Convert mutations to git patch format for compatibility."""
        import subprocess
        import tempfile
        from pathlib import Path

        repo = Path(repo_path)

        with tempfile.TemporaryDirectory() as tmpdir:
            # Clone repo
            subprocess.run(
                ["git", "clone", "--quiet", str(repo), tmpdir],
                capture_output=True,
                check=True,
            )

            # Apply modified files
            for file_path, content in bug.modified_files.items():
                target_file = Path(tmpdir) / file_path
                if target_file.exists():
                    target_file.write_text(content)

            # Generate diff
            result = subprocess.run(
                ["git", "diff", "HEAD"],
                cwd=tmpdir,
                capture_output=True,
                text=True,
            )

            return result.stdout if result.returncode == 0 else ""
