"""Context retrieval for grounded bug generation."""

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Set


@dataclass
class ContextPack:
    """Structured context pack for LLM prompting."""
    target_file: str
    target_function: str
    target_line: int
    function_body: str
    related_files: List[str]
    file_contents: Dict[str, str]
    imports: List[str]
    call_relationships: Dict[str, List[str]]
    test_files: List[str]
    allowed_files: List[str]
    line_reference: str = ""


class ContextRetriever:
    """Retrieve grounded context for bug generation."""

    def __init__(self, parser, graph_builder, repo_path: str):
        self.parser = parser
        self.graph = graph_builder
        self.repo_path = Path(repo_path)

        # Configuration
        self.max_related_files = 8
        self.max_context_lines = 300  # ~150-300 lines per file chunk
        self.chunk_overlap = 40
        self.token_budget = 12000

    def build_context_pack(self, func_key: str) -> Optional[ContextPack]:
        """Build a comprehensive context pack for a target function."""
        if func_key not in self.parser.ast_extractor.functions:
            return None

        func = self.parser.ast_extractor.functions[func_key]

        # Get related files (strictly limited)
        related_files = self._get_related_files(func_key)

        # Load file contents
        file_contents = self._load_file_contents(related_files + [func.file_path])

        # Get imports for context
        imports = self._get_relevant_imports(func.file_path)

        # Get call relationships
        call_relationships = self._get_call_relationships(func_key)

        # Find test files
        test_files = self._find_test_files(func.file_path)

        # Define allowed files for LLM
        allowed_files = list(file_contents.keys())

        # Build line number reference for target file
        line_reference = self._build_line_reference(func.file_path, func.line_start)

        return ContextPack(
            target_file=func.file_path,
            target_function=func.name,
            target_line=func.line_start,
            function_body=func.body,
            related_files=related_files,
            file_contents=file_contents,
            imports=imports,
            call_relationships=call_relationships,
            test_files=test_files,
            allowed_files=allowed_files,
            line_reference=line_reference,
        )

    def _get_related_files(self, func_key: str) -> List[str]:
        """Get strictly limited related files."""
        func = self.parser.ast_extractor.functions[func_key]

        related = set()

        # 1. Direct callers (up to 2)
        for caller_key in func.callers[:2]:
            if caller_key in self.parser.ast_extractor.functions:
                caller = self.parser.ast_extractor.functions[caller_key]
                related.add(caller.file_path)

        # 2. Direct callees (up to 2)
        callee_count = 0
        for callee_name in func.calls:
            for other_key, other_func in self.parser.ast_extractor.functions.items():
                if other_func.name == callee_name and callee_count < 2:
                    related.add(other_func.file_path)
                    callee_count += 1
                    break

        # 3. Same-module files (up to 2)
        target_dir = Path(func.file_path).parent
        same_dir_files = [
            f.file_path for f in self.parser.ast_extractor.functions.values()
            if Path(f.file_path).parent == target_dir and f.file_path != func.file_path
        ]
        related.update(same_dir_files[:2])

        # Limit total
        result = list(related)[:self.max_related_files]
        return result

    def _load_file_contents(self, file_paths: List[str]) -> Dict[str, str]:
        """Load content for specified files."""
        contents = {}

        for fp in file_paths:
            full_path = self.repo_path / fp
            try:
                with open(full_path) as f:
                    content = f.read()

                # Truncate if too large
                lines = content.splitlines()
                if len(lines) > self.max_context_lines:
                    content = "\n".join(lines[: self.max_context_lines])
                    content += "\n... [truncated]\n"

                contents[fp] = content
            except Exception as e:
                print(f"Warning: Could not read {fp}: {e}")

        return contents

    def _get_relevant_imports(self, file_path: str) -> List[str]:
        """Get imports for a file."""
        imports = []
        for imp in self.parser.ast_extractor.imports:
            if imp.file_path == file_path:
                imports.append(imp.path)
        return imports

    def _get_call_relationships(self, func_key: str) -> Dict[str, List[str]]:
        """Get call relationships for a function."""
        if func_key not in self.parser.ast_extractor.functions:
            return {"calls": [], "called_by": []}

        func = self.parser.ast_extractor.functions[func_key]

        return {
            "calls": func.calls[:10],  # Limit
            "called_by": [self.parser.ast_extractor.functions[k].name
                         for k in func.callers[:10]
                         if k in self.parser.ast_extractor.functions],
        }

    def _find_test_files(self, source_file: str) -> List[str]:
        """Find test files related to source file."""
        test_files = []

        # Look for tests in the same directory
        source_dir = Path(source_file).parent
        test_patterns = [
            f"{source_dir}/tests.rs",
            f"{source_dir}/test_*.rs",
            f"{source_dir}/*_test.rs",
        ]

        for pattern in test_patterns:
            matches = list(self.repo_path.glob(pattern))
            test_files.extend([str(m.relative_to(self.repo_path)) for m in matches])

        # Look for tests directory
        tests_dir = source_dir / "tests"
        if tests_dir.exists():
            for test_file in tests_dir.glob("*.rs"):
                test_files.append(str(test_file.relative_to(self.repo_path)))

        return test_files[:3]  # Limit

    def _build_line_reference(self, file_path: str, target_line: int) -> str:
        """Build a line number reference for key lines in the file."""
        content = self.repo_path.joinpath(file_path).read_text()
        lines = content.split('\n')

        ref_lines = []
        ref_lines.append(f"FUNCTION_START: {target_line}")

        # Find interesting lines around the target
        start = max(0, target_line - 5)
        end = min(len(lines), target_line + 20)

        for i in range(start, end):
            line = lines[i]
            stripped = line.strip()

            # Record lines with structural significance
            if stripped.startswith('pub fn') or stripped.startswith('pub async fn'):
                ref_lines.append(f"  Line {i+1}: {stripped[:50]}...")
            elif stripped.startswith('if ') or stripped.startswith('match '):
                ref_lines.append(f"  Line {i+1}: {stripped[:50]}...")
            elif 'permission' in stripped.lower() or 'auth' in stripped.lower():
                ref_lines.append(f"  Line {i+1}: {stripped[:50]}...")

        return '\n'.join(ref_lines[:10])  # Limit reference lines

    def format_context_for_prompt(self, pack: ContextPack) -> str:
        """Format context pack as LLM prompt."""
        lines = []

        # Header
        lines.append("=" * 60)
        lines.append("REPOSITORY CONTEXT FOR BUG GENERATION")
        lines.append("=" * 60)
        lines.append("")

        # Allowed files section (CRITICAL)
        lines.append("-" * 40)
        lines.append("ALLOWED FILES TO MODIFY")
        lines.append("-" * 40)
        lines.append("You may ONLY modify these specific files:")
        for fp in pack.allowed_files:
            lines.append(f"  - {fp}")
        lines.append("")
        lines.append("WARNING: Do NOT create, reference, or modify any other files!")
        lines.append("Do NOT invent paths, modules, or imports!")
        lines.append("")

        # Target function
        lines.append("-" * 40)
        lines.append("TARGET FUNCTION")
        lines.append("-" * 40)
        lines.append(f"File: {pack.target_file}")
        lines.append(f"Function: {pack.target_function}")
        lines.append(f"CRITICAL - Function starts at line: {pack.target_line}")
        lines.append("")

        # Line reference
        if pack.line_reference:
            lines.append("-" * 40)
            lines.append("LINE NUMBER REFERENCE")
            lines.append("-" * 40)
            lines.append(pack.line_reference)
            lines.append("")

        # Call relationships
        lines.append("-" * 40)
        lines.append("CALL RELATIONSHIPS")
        lines.append("-" * 40)
        lines.append(f"Calls: {', '.join(pack.call_relationships['calls']) or 'None'}")
        lines.append(f"Called by: {', '.join(pack.call_relationships['called_by']) or 'None'}")
        lines.append("")

        # File contents
        lines.append("-" * 40)
        lines.append("FILE CONTENTS")
        lines.append("-" * 40)
        lines.append("")

        # Target file first
        if pack.target_file in pack.file_contents:
            lines.append(f"### {pack.target_file}")
            lines.append("```rust")
            lines.append(pack.file_contents[pack.target_file])
            lines.append("```")
            lines.append("")

        # Related files
        for fp, content in pack.file_contents.items():
            if fp != pack.target_file:
                lines.append(f"### {fp}")
                lines.append("```rust")
                lines.append(content)
                lines.append("```")
                lines.append("")

        return "\n".join(lines)
