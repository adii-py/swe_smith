"""Parse unified diff patches into structured hunks for semantic analysis.

This module extracts file-level changes, hunks, and reconstructs before/after
snippets from a raw patch string WITHOUT requiring a local git repository.
"""

import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class HunkLine:
    """A single line within a diff hunk."""
    content: str
    old_lineno: int | None = None
    new_lineno: int | None = None
    kind: str = "context"  # "context", "addition", "removal"


@dataclass
class Hunk:
    """A hunk within a diff patch."""
    old_start: int
    old_lines: int
    new_start: int
    new_lines: int
    section_header: str = ""
    lines: list[HunkLine] = field(default_factory=list)

    @property
    def before_snippet(self) -> str:
        """Reconstruct the buggy (before) version of this hunk."""
        lines = []
        for line in self.lines:
            if line.kind == "context":
                lines.append(line.content)
            elif line.kind == "removal":
                lines.append(line.content)
            # Skip additions (they don't exist in buggy version)
        return "\n".join(lines)

    @property
    def after_snippet(self) -> str:
        """Reconstruct the fixed (after) version of this hunk."""
        lines = []
        for line in self.lines:
            if line.kind == "context":
                lines.append(line.content)
            elif line.kind == "addition":
                lines.append(line.content)
            # Skip removals (they don't exist in fixed version)
        return "\n".join(lines)

    def has_additions(self) -> bool:
        return any(l.kind == "addition" for l in self.lines)

    def has_removals(self) -> bool:
        return any(l.kind == "removal" for l in self.lines)


@dataclass
class FileDiff:
    """Changes within a single file."""
    old_path: str
    new_path: str
    hunks: list[Hunk] = field(default_factory=list)
    is_new_file: bool = False
    is_deleted: bool = False

    @property
    def path(self) -> str:
        return self.new_path if self.new_path != "/dev/null" else self.old_path

    @property
    def before_snippet(self) -> str:
        """Full buggy snippet for this file."""
        return "\n".join(h.before_snippet for h in self.hunks if h.before_snippet)

    @property
    def after_snippet(self) -> str:
        """Full fixed snippet for this file."""
        return "\n".join(h.after_snippet for h in self.hunks if h.after_snippet)


class PatchParser:
    """Parse unified diff format into structured FileDiff objects."""

    FILE_HEADER_RE = re.compile(
        r"^diff --git a/(.*?) b/(.*?)$",
        re.MULTILINE,
    )
    HUNK_HEADER_RE = re.compile(
        r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$",
        re.MULTILINE,
    )

    def parse(self, patch: str) -> list[FileDiff]:
        """Parse a unified diff patch string into structured FileDiffs."""
        if not patch or not patch.strip():
            return []

        # Split into per-file sections
        file_sections = self._split_into_file_sections(patch)
        file_diffs = []

        for section in file_sections:
            file_diff = self._parse_file_section(section)
            if file_diff:
                file_diffs.append(file_diff)

        return file_diffs

    def _split_into_file_sections(self, patch: str) -> list[str]:
        """Split patch into sections, one per changed file."""
        # Find all file header positions
        matches = list(self.FILE_HEADER_RE.finditer(patch))
        if not matches:
            return [patch]

        sections = []
        for i, match in enumerate(matches):
            start = match.start()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(patch)
            sections.append(patch[start:end])

        return sections

    def _parse_file_section(self, section: str) -> FileDiff | None:
        """Parse a single file section into a FileDiff."""
        header_match = self.FILE_HEADER_RE.search(section)
        if not header_match:
            return None

        old_path = header_match.group(1)
        new_path = header_match.group(2)

        # Detect new/deleted files
        is_new_file = (
            "--- /dev/null" in section or old_path == "/dev/null"
        )
        is_deleted = (
            "+++ /dev/null" in section or new_path == "/dev/null"
        )

        # Extract hunks (skip header lines before first hunk)
        hunks = self._parse_hunks(section)

        return FileDiff(
            old_path=old_path,
            new_path=new_path,
            hunks=hunks,
            is_new_file=is_new_file,
            is_deleted=is_deleted,
        )

    def _parse_hunks(self, section: str) -> list[Hunk]:
        """Parse all hunks from a file section."""
        hunks = []

        # Find all hunk headers
        hunk_matches = list(self.HUNK_HEADER_RE.finditer(section))
        if not hunk_matches:
            return hunks

        for i, match in enumerate(hunk_matches):
            old_start = int(match.group(1))
            old_lines = int(match.group(2)) if match.group(2) else 1
            new_start = int(match.group(3))
            new_lines = int(match.group(4)) if match.group(4) else 1
            section_header = match.group(5).strip()

            # Extract lines for this hunk
            hunk_start = match.end()
            hunk_end = hunk_matches[i + 1].start() if i + 1 < len(hunk_matches) else len(section)
            hunk_text = section[hunk_start:hunk_end]

            lines = self._parse_hunk_lines(hunk_text, old_start, new_start)

            hunks.append(Hunk(
                old_start=old_start,
                old_lines=old_lines,
                new_start=new_start,
                new_lines=new_lines,
                section_header=section_header,
                lines=lines,
            ))

        return hunks

    def _parse_hunk_lines(
        self, hunk_text: str, old_lineno: int, new_lineno: int
    ) -> list[HunkLine]:
        """Parse individual lines within a hunk."""
        lines = []
        current_old = old_lineno
        current_new = new_lineno

        for raw_line in hunk_text.splitlines():
            if not raw_line:
                continue

            # Unified diff lines start with space, +, or -
            # Handle "No newline at end of file" marker
            if "\\ No newline at end of file" in raw_line:
                continue

            if raw_line.startswith("+"):
                lines.append(HunkLine(
                    content=raw_line[1:],
                    old_lineno=None,
                    new_lineno=current_new,
                    kind="addition",
                ))
                current_new += 1
            elif raw_line.startswith("-"):
                lines.append(HunkLine(
                    content=raw_line[1:],
                    old_lineno=current_old,
                    new_lineno=None,
                    kind="removal",
                ))
                current_old += 1
            elif raw_line.startswith(" "):
                lines.append(HunkLine(
                    content=raw_line[1:],
                    old_lineno=current_old,
                    new_lineno=current_new,
                    kind="context",
                ))
                current_old += 1
                current_new += 1
            else:
                # Might be a context line without prefix in some formats
                lines.append(HunkLine(
                    content=raw_line,
                    old_lineno=current_old,
                    new_lineno=current_new,
                    kind="context",
                ))
                current_old += 1
                current_new += 1

        return lines


def parse_patch(patch: str) -> list[FileDiff]:
    """Convenience function: parse a patch string into structured diffs."""
    return PatchParser().parse(patch)
