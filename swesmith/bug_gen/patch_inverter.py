"""Utilities for inverting, validating, and diagnosing unified diffs.

PR-mirror instances store the merged PR *fix* diff. Benchmark bug patches must
*introduce* the pre-fix behavior, which is the fix diff with +/- lines swapped.
"""

from __future__ import annotations

import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from unidiff import PatchSet


@dataclass
class PatchApplyResult:
    success: bool
    stage: str
    message: str


def invert_unified_diff(patch_text: str) -> str:
    """Invert a unified diff (swap additions and removals).

    Handles standard file edits and /dev/null new/deleted file diffs.
    """
    if not patch_text or not patch_text.strip():
        return ""

    # PatchSet expects newline-terminated input
    normalized = patch_text if patch_text.endswith("\n") else patch_text + "\n"
    try:
        patch_set = PatchSet(normalized)
    except Exception:
        return _invert_unified_diff_line_based(normalized)

    out_lines: list[str] = []
    for patched_file in patch_set:
        path = patched_file.path
        if patched_file.is_removed_file:
            out_lines.append(f"diff --git a/{path} b/{path}")
            out_lines.append("deleted file mode 100644")
            out_lines.append(f"--- a/{path}")
            out_lines.append("+++ /dev/null")
            for hunk in patched_file:
                out_lines.append(str(hunk.source))
                for line in hunk:
                    if line.is_added:
                        out_lines.append("-" + line.value.rstrip("\n"))
            continue

        if patched_file.is_added_file:
            out_lines.append(f"diff --git a/{path} b/{path}")
            out_lines.append("new file mode 100644")
            out_lines.append("--- /dev/null")
            out_lines.append(f"+++ b/{path}")
            for hunk in patched_file:
                out_lines.append(str(hunk.target))
                for line in hunk:
                    if line.is_added:
                        out_lines.append("+" + line.value.rstrip("\n"))
            continue

        out_lines.append(f"diff --git a/{path} b/{path}")
        out_lines.append(f"--- a/{path}")
        out_lines.append(f"+++ b/{path}")

        for hunk in patched_file:
            out_lines.append(_swap_hunk_header(str(hunk).strip()))
            for line in hunk:
                if line.is_context:
                    out_lines.append(" " + line.value.rstrip("\n"))
                elif line.is_removed:
                    out_lines.append("+" + line.value.rstrip("\n"))
                elif line.is_added:
                    out_lines.append("-" + line.value.rstrip("\n"))

    result = "\n".join(out_lines)
    return result + ("\n" if result and not result.endswith("\n") else "")


def _swap_hunk_header(header: str) -> str:
    m = re.match(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", header.strip())
    if not m:
        return header.strip()
    o_s, o_c, n_s, n_c = m.group(1), m.group(2) or "1", m.group(3), m.group(4) or "1"
    return f"@@ -{n_s},{n_c} +{o_s},{o_c} @@"


def _invert_unified_diff_line_based(patch_text: str) -> str:
    """Fallback line-based inverter when unidiff parsing fails."""
    out: list[str] = []
    for line in patch_text.splitlines(keepends=True):
        if line.startswith("+++ ") and "/dev/null" in line:
            out.append(line.replace("+++ ", "--- ").replace("/dev/null", "a/dev/null"))
        elif line.startswith("--- ") and "/dev/null" in line:
            out.append(line.replace("--- ", "+++ ").replace("/dev/null", "b/dev/null"))
        elif line.startswith("+") and not line.startswith("+++"):
            out.append("-" + line[1:])
        elif line.startswith("-") and not line.startswith("---"):
            out.append("+" + line[1:])
        elif line.startswith("@@"):
            out.append(_swap_hunk_header(line.rstrip("\n")) + "\n")
        else:
            out.append(line)
    return "".join(out)


def validate_patch_applies(
    repo_path: str | Path,
    patch: str,
    *,
    reverse: bool = False,
) -> PatchApplyResult:
    """Validate patch with ``git apply --check`` against a clean checkout."""
    repo_path = Path(repo_path)
    if not patch or not patch.strip():
        return PatchApplyResult(False, "empty", "Patch is empty")

    cmd = ["git", "apply", "--check"]
    if reverse:
        cmd.append("--reverse")
    cmd.append("-")

    try:
        result = subprocess.run(
            cmd,
            cwd=repo_path,
            input=patch,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        return PatchApplyResult(False, "timeout", "git apply --check timed out")

    if result.returncode == 0:
        return PatchApplyResult(True, "apply_check", "Patch applies cleanly")

    err = (result.stderr or result.stdout or "unknown error").strip()
    return PatchApplyResult(False, "apply_check", err[:2000])


def validate_patch_in_temp_clone(
    source_repo: str | Path,
    patch: str,
) -> PatchApplyResult:
    """Clone repo to a temp dir and run ``git apply --check``."""
    source_repo = Path(source_repo)
    with tempfile.TemporaryDirectory() as tmpdir:
        clone_dir = Path(tmpdir) / "repo"
        clone = subprocess.run(
            ["git", "clone", "--quiet", str(source_repo), str(clone_dir)],
            capture_output=True,
            text=True,
        )
        if clone.returncode != 0:
            return PatchApplyResult(
                False, "clone", clone.stderr[:500] or "git clone failed"
            )
        return validate_patch_applies(clone_dir, patch)


def apply_reverse_and_capture(repo_path: str | Path, fix_patch: str) -> Optional[str]:
    """Apply fix patch in reverse and return the captured bug-introducing diff."""
    repo_path = Path(repo_path)
    try:
        result = subprocess.run(
            ["git", "apply", "--reverse", "-"],
            cwd=repo_path,
            input=fix_patch,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            return None
        capture = subprocess.run(
            ["git", "diff", "HEAD"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if capture.returncode != 0 or not capture.stdout.strip():
            return None
        return capture.stdout
    finally:
        subprocess.run(
            ["git", "reset", "--hard"],
            cwd=repo_path,
            capture_output=True,
        )
        subprocess.run(
            ["git", "clean", "-fdx"],
            cwd=repo_path,
            capture_output=True,
        )


def try_invert_and_validate(
    source_repo: str | Path,
    fix_patch: str,
) -> tuple[Optional[str], PatchApplyResult]:
    """Invert a PR fix patch and verify the bug patch applies on base commit."""
    inverted = invert_unified_diff(fix_patch)
    result = validate_patch_in_temp_clone(source_repo, inverted)
    if result.success:
        return inverted, result
    return None, result
