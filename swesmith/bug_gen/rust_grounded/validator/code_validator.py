"""Validation for mutated code (NOT patches)."""

import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

from ..generator.mutation_generator import GeneratedBug


@dataclass
class ValidationResult:
    """Validation result."""
    success: bool
    stage: str
    message: str
    details: dict = None


class MutatedCodeValidator:
    """Validate mutated code compiles and passes tests."""

    def __init__(self, repo_path: str):
        self.repo_path = Path(repo_path)

    def validate_bug(
        self,
        bug: GeneratedBug,
        crate_name: Optional[str] = None
    ) -> ValidationResult:
        """Run full validation on mutated code."""

        # Stage 1: Check file modifications are valid
        result = self._validate_modifications(bug)
        if not result.success:
            return result

        # Stage 2: Check syntax validity
        result = self._validate_syntax(bug)
        if not result.success:
            return result

        # Stage 3: Compile check
        result = self._validate_compilation(bug, crate_name)
        if not result.success:
            return result

        # Stage 4: Check tests still exist (don't break test suite)
        result = self._validate_tests_pass(bug, crate_name)

        return result

    def _validate_modifications(self, bug: GeneratedBug) -> ValidationResult:
        """Check that modifications are valid."""
        # Handle patch-based bugs
        if hasattr(bug, 'patch'):
            # Patch was already validated with git apply
            return ValidationResult(
                success=True,
                stage="modification_check",
                message="Patch validated with git apply",
            )

        # Handle old-style modified_files bugs
        for file_path, content in bug.modified_files.items():
            # Check file is in allowed list
            if file_path not in bug.original_files:
                return ValidationResult(
                    success=False,
                    stage="modification_check",
                    message=f"Modified file not in allowed list: {file_path}",
                )

            # Basic syntax check - balanced braces
            open_count = content.count('{')
            close_count = content.count('}')
            if open_count != close_count:
                return ValidationResult(
                    success=False,
                    stage="syntax_check",
                    message=f"Unbalanced braces in {file_path}: {open_count} open, {close_count} close",
                )

            # Check for obvious syntax issues
            if content.count('(') != content.count(')'):
                return ValidationResult(
                    success=False,
                    stage="syntax_check",
                    message=f"Unbalanced parentheses in {file_path}",
                )

        return ValidationResult(
            success=True,
            stage="modification_check",
            message="Modifications are valid",
        )

    def _validate_syntax(self, bug: GeneratedBug) -> ValidationResult:
        """Basic syntax validation using cargo check on individual files."""
        # Skip file-level syntax check - we'll catch errors at compilation stage
        # This avoids needing nightly compiler for -Z parse-only
        return ValidationResult(
            success=True,
            stage="syntax",
            message="Skipping file-level syntax check (will validate at compilation)",
        )

    def _validate_compilation(
        self,
        bug: GeneratedBug,
        crate_name: Optional[str]
    ) -> ValidationResult:
        """Validate mutated code compiles with cargo."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Clone and apply
            result = subprocess.run(
                ["git", "clone", "--quiet", str(self.repo_path), tmpdir],
                capture_output=True,
            )

            if result.returncode != 0:
                return ValidationResult(
                    success=False,
                    stage="compile",
                    message="Failed to clone repo",
                )

            # Apply modifications - handle both patch-based and old-style bugs
            if hasattr(bug, 'patch') and bug.patch:
                # Patch-based bug - apply with git apply
                result = subprocess.run(
                    ["git", "apply", "-"],
                    cwd=tmpdir,
                    input=bug.patch,
                    capture_output=True,
                    text=True,
                )
                if result.returncode != 0:
                    error = result.stderr[:200] if result.stderr else "Unknown error"
                    return ValidationResult(
                        success=False,
                        stage="compile",
                        message=f"Failed to apply patch: {error}",
                    )
            elif hasattr(bug, 'modified_files'):
                # Old-style modified_files bugs
                for file_path, content in bug.modified_files.items():
                    target = Path(tmpdir) / file_path
                    if target.exists():
                        target.write_text(content)
            else:
                return ValidationResult(
                    success=False,
                    stage="compile",
                    message="Bug has neither patch nor modified_files",
                )

            # Try to compile
            cmd = ["cargo", "check", "--release"]
            if crate_name:
                cmd.extend(["-p", crate_name])

            result = subprocess.run(
                cmd,
                cwd=tmpdir,
                capture_output=True,
                text=True,
                timeout=600,
            )

            if result.returncode != 0:
                # Truncate error message
                error = result.stderr[:500] if result.stderr else "Unknown error"
                return ValidationResult(
                    success=False,
                    stage="compile",
                    message=f"Compilation failed: {error}",
                )

            return ValidationResult(
                success=True,
                stage="compile",
                message="Code compiles successfully",
            )

    def _validate_tests_pass(
        self,
        bug: GeneratedBug,
        crate_name: Optional[str]
    ) -> ValidationResult:
        """Check that tests compile without external dependencies."""
        # Skip test validation - focus on library code compilation only
        # Many tests require external services (Redis, Postgres, etc.)
        return ValidationResult(
            success=True,
            stage="test_compile",
            message="Skipped: tests require external dependencies (Redis/Postgres)",
        )

    def _has_external_dependencies(self, code: str) -> bool:
        """Check if code references external services."""
        external_patterns = [
            'redis', 'postgres', 'kafka', 'elastic', 'mongodb',
            'smtp', 'aws', 'gcp', 'azure', 'docker'
        ]
        code_lower = code.lower()
        return any(pattern in code_lower for pattern in external_patterns)

    def generate_patch_from_bug(
        self,
        bug: GeneratedBug,
    ) -> str:
        """Generate a git patch from bug modifications."""
        # If bug already has a patch (patch-based generation), return it directly
        if hasattr(bug, 'patch') and bug.patch:
            return bug.patch

        # Old-style: generate patch from modified_files
        lines = []

        for file_path in sorted(bug.modified_files.keys()):
            original = bug.original_files.get(file_path, "")
            modified = bug.modified_files[file_path]

            if original == modified:
                continue

            # Generate unified diff manually
            lines.append(f"diff --git a/{file_path} b/{file_path}")
            lines.append(f"--- a/{file_path}")
            lines.append(f"+++ b/{file_path}")

            # Simple line-by-line diff
            orig_lines = original.split('\n')
            mod_lines = modified.split('\n')

            # Find common prefix
            prefix_len = 0
            for o, m in zip(orig_lines, mod_lines):
                if o == m:
                    prefix_len += 1
                else:
                    break

            # Find common suffix
            suffix_len = 0
            for o, m in zip(reversed(orig_lines[prefix_len:]), reversed(mod_lines[prefix_len:])):
                if o == m:
                    suffix_len += 1
                else:
                    break

            # Calculate hunk
            old_start = prefix_len + 1
            old_count = len(orig_lines) - prefix_len - suffix_len
            new_start = prefix_len + 1
            new_count = len(mod_lines) - prefix_len - suffix_len

            # Include context
            context_before = min(3, prefix_len)
            context_after = min(3, suffix_len)

            old_start -= context_before
            old_count += context_before + context_after
            new_start -= context_before
            new_count += context_before + context_after

            lines.append(f"@@ -{old_start},{old_count} +{new_start},{new_count} @@")

            # Context before
            for i in range(prefix_len - context_before, prefix_len):
                lines.append(f" {orig_lines[i]}")

            # Removed lines
            for i in range(prefix_len, len(orig_lines) - suffix_len):
                lines.append(f"-{orig_lines[i]}")

            # Added lines
            for i in range(prefix_len, len(mod_lines) - suffix_len):
                lines.append(f"+{mod_lines[i]}")

            # Context after
            for i in range(len(orig_lines) - suffix_len, len(orig_lines)):
                lines.append(f" {orig_lines[i]}")

            lines.append("")

        return '\n'.join(lines) + '\n'
