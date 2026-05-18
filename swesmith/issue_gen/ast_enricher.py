"""Enrich parsed patches with semantic context for LLM prompts.

Builds a structured "semantic diff" report from patch hunks that includes:
- Function/class identification around changes
- Change classification (signature vs body vs import)
- Before/after reconstructed snippets
- Import-level changes

Works from the patch string alone — no git repository required.
"""

import re
from dataclasses import dataclass, field
from typing import Any

from swesmith.issue_gen.patch_parser import FileDiff, Hunk, parse_patch


# Language-agnostic regex patterns for identifying code constructs
FUNCTION_PATTERNS = {
    "python": re.compile(
        r"^(\s*)(?:async\s+)?def\s+(\w+)\s*\([^)]*\)\s*(?:->\s*[^:]+)?:",
        re.MULTILINE,
    ),
    "rust": re.compile(
        r"^(\s*)(?:pub\s+)?(?:async\s+)?(?:unsafe\s+)?fn\s+(\w+)",
        re.MULTILINE,
    ),
    "general": re.compile(
        r"^(\s*)(?:def|fn|func|function)\s+(\w+)",
        re.MULTILINE,
    ),
}

CLASS_STRUCT_PATTERNS = {
    "python": re.compile(r"^(\s*)class\s+(\w+)(?:\([^)]*\))?:", re.MULTILINE),
    "rust": re.compile(r"^(\s*)(?:pub\s+)?(?:struct|enum|trait|impl)\s+(?:<[^>]*>\s*)?(\w+)", re.MULTILINE),
    "general": re.compile(r"^(\s*)(?:class|struct|enum|trait|impl|interface)\s+(\w+)", re.MULTILINE),
}

IMPORT_PATTERNS = {
    "python": re.compile(r"^(?:from\s+\S+\s+import|import\s+\S+)", re.MULTILINE),
    "rust": re.compile(r"^(?:use|extern|mod)\s+", re.MULTILINE),
    "general": re.compile(r"^(?:import|from|use|require|include|extern|mod)\s+", re.MULTILINE),
}

SIGNATURE_CHANGE_INDICATORS = [
    re.compile(r"\bdef\s+\w+\s*\("),       # Python function def
    re.compile(r"\bfn\s+\w+\s*\("),          # Rust fn
    re.compile(r"\bclass\s+\w+"),           # class definition
    re.compile(r"\bstruct\s+\w+"),          # struct definition
    re.compile(r"\bimpl\s+(?:<[^>]+>\s*)?\w+"),  # impl block
]


def detect_language(file_path: str) -> str:
    """Detect programming language from file extension."""
    ext = file_path.rsplit(".", 1)[-1].lower() if "." in file_path else ""
    lang_map = {
        "py": "python",
        "rs": "rust",
        "java": "java",
        "cpp": "cpp",
        "c": "c",
        "js": "javascript",
        "ts": "typescript",
        "go": "go",
        "rb": "ruby",
        "php": "php",
    }
    return lang_map.get(ext, "general")


def extract_changed_items(snippet: str, language: str) -> list[dict[str, str]]:
    """Extract function/class names from a code snippet."""
    items = []

    func_pattern = FUNCTION_PATTERNS.get(language, FUNCTION_PATTERNS["general"])
    class_pattern = CLASS_STRUCT_PATTERNS.get(language, CLASS_STRUCT_PATTERNS["general"])

    for match in func_pattern.finditer(snippet):
        items.append({
            "kind": "function",
            "name": match.group(2),
            "indent": len(match.group(1)),
        })

    for match in class_pattern.finditer(snippet):
        kind = "class" if language == "python" else "type"
        items.append({
            "kind": kind,
            "name": match.group(2),
            "indent": len(match.group(1)),
        })

    return items


def classify_change_type(before: str, after: str, language: str) -> str:
    """Classify what kind of change occurred."""
    before_items = extract_changed_items(before, language)
    after_items = extract_changed_items(after, language)

    before_names = {i["name"] for i in before_items}
    after_names = {i["name"] for i in after_items}

    # Check for added/removed items
    added = after_names - before_names
    removed = before_names - after_names

    if added and not removed and not before.strip():
        return "addition"
    if removed and not added and not after.strip():
        return "deletion"
    if added or removed:
        return "signature_change"

    # Check if the change is just within the body
    if before and after:
        # Strip signatures to compare bodies
        before_body = strip_signature(before, language)
        after_body = strip_signature(after, language)
        if before_body != after_body:
            return "body_change"

    return "modification"


def strip_signature(code: str, language: str) -> str:
    """Try to strip the function signature, keeping only the body."""
    lines = code.splitlines()
    if not lines:
        return ""

    first_line = lines[0]

    # For Python: find the colon ending the signature
    if language == "python":
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.endswith(":") and (
                stripped.startswith("def ") or stripped.startswith("async def ")
            ):
                return "\n".join(lines[i + 1 :])

    # For Rust: find the opening brace
    if language == "rust":
        for i, line in enumerate(lines):
            if "{" in line and ("fn " in line or "impl " in line):
                # Return from after the opening brace
                brace_idx = line.index("{")
                remainder = line[brace_idx + 1 :]
                body_lines = [remainder] if remainder.strip() else []
                body_lines.extend(lines[i + 1 :])
                return "\n".join(body_lines)

    return code


def is_import_change(hunk: Hunk, language: str) -> bool:
    """Check if a hunk only modifies import/use statements."""
    import_pattern = IMPORT_PATTERNS.get(language, IMPORT_PATTERNS["general"])

    changed_lines = [l for l in hunk.lines if l.kind in ("addition", "removal")]
    if not changed_lines:
        return False

    return all(
        import_pattern.match(line.content)
        for line in changed_lines
    )


def has_signature_change(hunk: Hunk, language: str) -> bool:
    """Detect if a hunk changes a function/type signature."""
    changed = [l.content for l in hunk.lines if l.kind in ("addition", "removal")]

    # Heuristic: look for signature-defining patterns in changed lines
    for line in changed:
        for pattern in SIGNATURE_CHANGE_INDICATORS:
            if pattern.search(line):
                return True

        # Check for parameter changes (comma-separated in parens for fn defs)
        stripped = line.strip()
        if stripped.startswith("def ") or stripped.startswith("fn "):
            return True
        if stripped.startswith("pub fn ") or stripped.startswith("async fn "):
            return True
        if re.search(r"\)\s*->", stripped):  # Return type change
            return True
        if stripped.startswith("class ") or stripped.startswith("struct "):
            return True
        if stripped.startswith("impl "):
            return True

    return False


@dataclass
class SemanticChange:
    """A single semantic change within a file."""
    file_path: str
    kind: str  # "function", "class", "import", "body", "mixed"
    change_type: str  # "modified", "added", "removed"
    item_name: str | None = None
    before_snippet: str = ""
    after_snippet: str = ""
    change_classification: str = ""  # "signature_change", "body_change", "addition", "deletion"
    description: str = ""


@dataclass
class SemanticDiffReport:
    """Complete semantic diff report for a patch."""
    files_changed: list[str] = field(default_factory=list)
    changes: list[SemanticChange] = field(default_factory=list)
    imports_added: list[str] = field(default_factory=list)
    imports_removed: list[str] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)

    def to_markdown(self) -> str:
        """Convert report to markdown for LLM consumption."""
        lines = []
        lines.append("## Semantic Change Analysis")
        lines.append("")
        lines.append(f"**Files changed:** {len(self.files_changed)}")
        lines.append(f"**Total semantic changes:** {len(self.changes)}")
        lines.append("")

        # Import changes
        if self.imports_added or self.imports_removed:
            lines.append("### Import Changes")
            for imp in self.imports_added:
                lines.append(f"- ADDED: `{imp}`")
            for imp in self.imports_removed:
                lines.append(f"- REMOVED: `{imp}`")
            lines.append("")

        # Changes by file
        files_grouped: dict[str, list[SemanticChange]] = {}
        for change in self.changes:
            files_grouped.setdefault(change.file_path, []).append(change)

        for file_path, file_changes in files_grouped.items():
            lines.append(f"### File: {file_path}")
            lines.append("")

            for change in file_changes:
                item_label = change.item_name or "unnamed"
                lines.append(f"**{change.change_type.upper()} {change.kind}: `{item_label}`**")
                if change.change_classification:
                    lines.append(f"*Classification: {change.change_classification}*")
                if change.description:
                    lines.append(f"*Description: {change.description}*")

                if change.before_snippet and change.after_snippet:
                    lines.append("")
                    lines.append("```")
                    lines.append("--- BEFORE (buggy) ---")
                    lines.append(change.before_snippet)
                    lines.append("```")
                    lines.append("")
                    lines.append("```")
                    lines.append("--- AFTER (fixed) ---")
                    lines.append(change.after_snippet)
                    lines.append("```")
                elif change.before_snippet:
                    lines.append("")
                    lines.append("```")
                    lines.append("--- REMOVED (was in buggy) ---")
                    lines.append(change.before_snippet)
                    lines.append("```")
                elif change.after_snippet:
                    lines.append("")
                    lines.append("```")
                    lines.append("--- ADDED (in fixed) ---")
                    lines.append(change.after_snippet)
                    lines.append("```")

                lines.append("")

        return "\n".join(lines)


class ASTEnricher:
    """Enrich patch hunks with semantic context."""

    def enrich(self, patch: str) -> SemanticDiffReport:
        """Build a semantic diff report from a raw patch string."""
        file_diffs = parse_patch(patch)
        report = SemanticDiffReport()

        for file_diff in file_diffs:
            report.files_changed.append(file_diff.path)
            language = detect_language(file_diff.path)

            # Process each hunk
            for hunk in file_diff.hunks:
                change = self._analyze_hunk(file_diff, hunk, language)
                if change:
                    report.changes.append(change)

                # Track imports separately
                if is_import_change(hunk, language):
                    for line in hunk.lines:
                        if line.kind == "addition":
                            report.imports_added.append(line.content.strip())
                        elif line.kind == "removal":
                            report.imports_removed.append(line.content.strip())

        # Build summary
        report.summary = {
            "files_changed": len(report.files_changed),
            "total_changes": len(report.changes),
            "items_added": sum(1 for c in report.changes if c.change_type == "added"),
            "items_removed": sum(1 for c in report.changes if c.change_type == "removed"),
            "items_modified": sum(1 for c in report.changes if c.change_type == "modified"),
            "signature_changes": sum(
                1 for c in report.changes if c.change_classification == "signature_change"
            ),
            "body_changes": sum(
                1 for c in report.changes if c.change_classification == "body_change"
            ),
        }

        return report

    def _analyze_hunk(self, file_diff: FileDiff, hunk: Hunk, language: str) -> SemanticChange | None:
        """Analyze a single hunk and produce a SemanticChange."""
        before = hunk.before_snippet
        after = hunk.after_snippet

        # Skip empty hunks
        if not before.strip() and not after.strip():
            return None

        # Skip no-op hunks (only whitespace/newline changes)
        if before.strip() == after.strip():
            return None

        # Identify items in before and after
        before_items = extract_changed_items(before, language)
        after_items = extract_changed_items(after, language)
        before_names = {i["name"] for i in before_items}
        after_names = {i["name"] for i in after_items}

        # Pick the primary item (least indent in whichever side has items)
        all_items = before_items + after_items
        item_name = None
        kind = "body"

        if all_items:
            items_sorted = sorted(all_items, key=lambda x: x["indent"])
            item_name = items_sorted[0]["name"]
            kind = items_sorted[0]["kind"]
        else:
            # Fallback: try to extract from hunk section header
            if hunk.section_header:
                header_items = extract_changed_items(hunk.section_header, language)
                if header_items:
                    item_name = header_items[0]["name"]
                    kind = header_items[0]["kind"]

        # Determine change type based on item presence in before/after
        if item_name and item_name in before_names and item_name not in after_names:
            change_type = "removed"
        elif item_name and item_name not in before_names and item_name in after_names:
            change_type = "added"
        elif file_diff.is_new_file or (not before.strip() and after.strip()):
            change_type = "added"
        elif file_diff.is_deleted or (before.strip() and not after.strip()):
            change_type = "removed"
        else:
            change_type = "modified"

        # Classify the nature of the change
        if change_type == "removed":
            classification = "deletion"
        elif change_type == "added":
            classification = "addition"
        elif has_signature_change(hunk, language):
            classification = "signature_change"
        else:
            classification = classify_change_type(before, after, language)

        # Build description
        description = self._build_description(
            item_name, kind, change_type, classification, language
        )

        return SemanticChange(
            file_path=file_diff.path,
            kind=kind,
            change_type=change_type,
            item_name=item_name,
            before_snippet=before.strip(),
            after_snippet=after.strip(),
            change_classification=classification,
            description=description,
        )

    def _build_description(
        self,
        item_name: str | None,
        kind: str,
        change_type: str,
        classification: str,
        language: str,
    ) -> str:
        """Build a human-readable description of the change."""
        name = item_name or "unnamed"
        kind_label = kind

        if change_type == "added":
            return f"New {kind_label} `{name}` was added."
        if change_type == "removed":
            return f"Existing {kind_label} `{name}` was removed."

        if classification == "signature_change":
            return f"The signature of {kind_label} `{name}` was modified (parameters, return type, or visibility)."
        if classification == "body_change":
            return f"The implementation body of {kind_label} `{name}` was modified."

        return f"{kind_label.capitalize()} `{name}` was modified."


def enrich_patch(patch: str) -> SemanticDiffReport:
    """Convenience function: build semantic diff report from patch string."""
    return ASTEnricher().enrich(patch)
