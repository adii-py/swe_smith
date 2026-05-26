"""Comprehensive patch validation."""

import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple


@dataclass
class ValidationResult:
    """Validation result."""
    success: bool
    stage: str
    message: str
    details: dict = None


class PatchValidator:
    """Validate generated patches."""

    def __init__(self, repo_path: str):
        self.repo_path = Path(repo_path)

    def validate_full(
        self,
        patch: str,
        allowed_files: List[str],
        crate_name: Optional[str] = None
    ) -> ValidationResult:
        """Run full validation pipeline."""

        # Stage 1: File existence check
        result = self._validate_files_exist(patch, allowed_files)
        if not result.success:
            return result

        # Stage 2: Patch format check
        result = self._validate_patch_format(patch)
        if not result.success:
            return result

        # Stage 3: Apply patch in temp directory
        result = self._validate_apply(patch)
        if not result.success:
            return result

        # Stage 4: Compile check
        result = self._validate_compile(patch, crate_name)
        if not result.success:
            return result

        # Stage 5: Check no new files created
        result = self._validate_no_new_files(patch, allowed_files)
        if not result.success:
            return result

        return ValidationResult(
            success=True,
            stage="complete",
            message="Patch passed all validations",
        )

    def _validate_files_exist(
        self,
        patch: str,
        allowed_files: List[str]
    ) -> ValidationResult:
        """Check that all files in patch exist and are allowed."""
        files = re.findall(r'diff --git a/(\S+) b/', patch)

        for file_path in files:
            full_path = self.repo_path / file_path
            if not full_path.exists():
                return ValidationResult(
                    success=False,
                    stage="file_existence",
                    message=f"File does not exist: {file_path}",
                )

            if file_path not in allowed_files:
                return ValidationResult(
                    success=False,
                    stage="file_allowed",
                    message=f"File not in allowed list: {file_path}",
                )

        return ValidationResult(
            success=True,
            stage="file_existence",
            message="All files exist and are allowed",
        )

    def _validate_patch_format(self, patch: str) -> ValidationResult:
        """Validate unified diff format."""
        # Check for required headers
        if not patch.startswith("diff --git"):
            return ValidationResult(
                success=False,
                stage="format",
                message="Patch must start with 'diff --git'",
            )

        # Check for hunk headers
        hunks = re.findall(r'@@ -(\d+),(\d+) \+(\d+),(\d+) @@', patch)
        if not hunks:
            return ValidationResult(
                success=False,
                stage="format",
                message="No hunk headers found",
            )

        # Validate hunk line counts
        lines = patch.split('\n')
        for i, line in enumerate(lines):
            hunk_match = re.match(r'^@@ -(\d+),(\d+) \+(\d+),(\d+) @@', line)
            if hunk_match:
                old_count = int(hunk_match.group(2))
                new_count = int(hunk_match.group(4))

                # Count actual lines in hunk
                j = i + 1
                old_actual = 0
                new_actual = 0

                while j < len(lines):
                    hunk_line = lines[j]
                    if re.match(r'^@@ ', hunk_line) or hunk_line.startswith('diff --git'):
                        break

                    if hunk_line.startswith('-') and not hunk_line.startswith('---'):
                        old_actual += 1
                    elif hunk_line.startswith('+') and not hunk_line.startswith('+++'):
                        new_actual += 1
                    elif not hunk_line.startswith('\\'):
                        old_actual += 1
                        new_actual += 1

                    j += 1

                if old_count != old_actual or new_count != new_actual:
                    return ValidationResult(
                        success=False,
                        stage="hunk_counts",
                        message=f"Hunk at line {i+1}: expected {old_count}/{new_count}, "
                                f"found {old_actual}/{new_actual}",
                    )

        return ValidationResult(
            success=True,
            stage="format",
            message="Patch format is valid",
        )

    def _validate_apply(self, patch: str) -> ValidationResult:
        """Validate patch applies cleanly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Clone repo
            result = subprocess.run(
                ["git", "clone", "--quiet", str(self.repo_path), tmpdir],
                capture_output=True,
                text=True,
            )

            if result.returncode != 0:
                return ValidationResult(
                    success=False,
                    stage="apply",
                    message=f"Failed to clone repo: {result.stderr}",
                )

            # Try to apply patch with fuzz factor for context mismatch
            result = subprocess.run(
                ["git", "apply", "--ignore-whitespace", "--whitespace=nowarn", "-"],
                cwd=tmpdir,
                input=patch,
                capture_output=True,
                text=True,
            )

            # If that fails, try with fuzz
            if result.returncode != 0:
                result = subprocess.run(
                    ["git", "apply", "-C1", "--reject", "-"],
                    cwd=tmpdir,
                    input=patch,
                    capture_output=True,
                    text=True,
                )

            if result.returncode != 0:
                return ValidationResult(
                    success=False,
                    stage="apply",
                    message=f"Patch apply failed: {result.stderr}",
                )

            return ValidationResult(
                success=True,
                stage="apply",
                message="Patch applies cleanly",
            )

    def _validate_compile(
        self,
        patch: str,
        crate_name: Optional[str]
    ) -> ValidationResult:
        """Validate patched code compiles."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Clone and apply
            subprocess.run(
                ["git", "clone", "--quiet", str(self.repo_path), tmpdir],
                capture_output=True,
            )

            result = subprocess.run(
                ["git", "apply", "-"],
                cwd=tmpdir,
                input=patch,
                capture_output=True,
                text=True,
            )

            if result.returncode != 0:
                return ValidationResult(
                    success=False,
                    stage="compile",
                    message="Patch apply failed before compile check",
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
                timeout=300,
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

    def _validate_no_new_files(
        self,
        patch: str,
        allowed_files: List[str]
    ) -> ValidationResult:
        """Check that patch doesn't create new files."""
        # Check for "new file mode" in patch
        if "new file mode" in patch:
            return ValidationResult(
                success=False,
                stage="new_files",
                message="Patch creates new files (not allowed)",
            )

        # Check all files in patch are in allowed list
        files = re.findall(r'diff --git a/(\S+) b/', patch)
        for file_path in files:
            if file_path not in allowed_files:
                return ValidationResult(
                    success=False,
                    stage="new_files",
                    message=f"Patch modifies non-allowed file: {file_path}",
                )

        return ValidationResult(
            success=True,
            stage="new_files",
            message="No new files created",
        )

    def fix_hunk_line_numbers(self, patch: str, file_path: str, actual_line: int) -> str:
        """Adjust hunk line numbers to match actual function location."""
        lines = patch.split('\n')
        result = []

        for line in lines:
            hunk_match = re.match(r'^@@ -(\d+),(\d+) \+(\d+),(\d+) @@', line)
            if hunk_match:
                old_start = int(hunk_match.group(1))
                old_count = int(hunk_match.group(2))
                new_count = int(hunk_match.group(4))

                rest_start = line.find('@@', line.find('@@') + 2) + 2
                rest = line[rest_start:]

                # Adjust start line to actual location
                # Keep the offset from the start of the hunk to the actual change
                adjusted_start = actual_line

                new_header = f'@@ -{adjusted_start},{old_count} +{adjusted_start},{new_count} @@{rest}'
                result.append(new_header)
            else:
                result.append(line)

        return '\n'.join(result)

    def fix_hunk_headers(self, patch: str) -> str:
        """Fix incorrect hunk line counts."""
        lines = patch.split('\n')
        result = []

        i = 0
        while i < len(lines):
            line = lines[i]

            hunk_match = re.match(r'^@@ -(\d+),(\d+) \+(\d+),(\d+) @@', line)
            if hunk_match:
                old_start = int(hunk_match.group(1))
                new_start = int(hunk_match.group(3))

                # Get context after @@
                rest_start = line.find('@@', line.find('@@') + 2) + 2
                rest = line[rest_start:]

                # Count lines in hunk
                i += 1
                hunk_lines = []
                old_actual = 0
                new_actual = 0

                while i < len(lines):
                    hunk_line = lines[i]
                    if re.match(r'^@@ ', hunk_line) or hunk_line.startswith('diff --git'):
                        break
                    if hunk_line.startswith('--- ') or hunk_line.startswith('+++'):
                        break

                    hunk_lines.append(hunk_line)

                    if hunk_line.startswith('-') and not hunk_line.startswith('---'):
                        old_actual += 1
                    elif hunk_line.startswith('+') and not hunk_line.startswith('+++'):
                        new_actual += 1
                    elif not hunk_line.startswith('\\'):
                        old_actual += 1
                        new_actual += 1

                    i += 1

                # Write corrected header
                new_header = f'@@ -{old_start},{old_actual} +{new_start},{new_actual} @@{rest}'
                result.append(new_header)
                result.extend(hunk_lines)
                continue
            else:
                result.append(line)

            i += 1

        return '\n'.join(result)
