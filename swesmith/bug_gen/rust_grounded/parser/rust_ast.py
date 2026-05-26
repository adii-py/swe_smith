"""Tree-sitter based Rust AST extraction."""

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set


@dataclass
class FunctionInfo:
    """Information about a Rust function."""
    name: str
    file_path: str
    line_start: int
    line_end: int
    signature: str
    body: str
    visibility: str  # "pub", "pub(crate)", "private"
    is_async: bool
    parameters: List[str] = field(default_factory=list)
    return_type: Optional[str] = None
    calls: List[str] = field(default_factory=list)
    callers: List[str] = field(default_factory=list)


@dataclass
class StructInfo:
    """Information about a Rust struct/enum."""
    name: str
    file_path: str
    line_start: int
    kind: str  # "struct", "enum", "union"
    fields: List[str] = field(default_factory=list)
    visibility: str = "private"


@dataclass
class ImportInfo:
    """Import/use statement."""
    path: str
    file_path: str
    line: int
    items: List[str] = field(default_factory=list)


@dataclass
class ModuleInfo:
    """Module information."""
    name: str
    file_path: str
    line: int
    items: List[str] = field(default_factory=list)


class RustAstExtractor:
    """Extract Rust AST information using regex-based parsing."""

    def __init__(self):
        self.functions: Dict[str, FunctionInfo] = {}
        self.structs: Dict[str, StructInfo] = {}
        self.imports: List[ImportInfo] = []
        self.modules: List[ModuleInfo] = []

    def parse_file(self, file_path: Path, relative_path: str) -> None:
        """Parse a single Rust file."""
        try:
            content = file_path.read_text()
            lines = content.split('\n')

            self._extract_imports(content, relative_path)
            self._extract_functions(content, relative_path, lines)
            self._extract_structs(content, relative_path)
            self._extract_modules(content, relative_path)

        except Exception as e:
            print(f"Warning: Failed to parse {file_path}: {e}")

    def _extract_imports(self, content: str, file_path: str) -> None:
        """Extract use statements."""
        # Pattern for use statements
        patterns = [
            r'use\s+([^;]+);',
            r'pub\s+use\s+([^;]+);',
        ]

        for pattern in patterns:
            for match in re.finditer(pattern, content):
                import_path = match.group(1).strip()
                line_num = content[:match.start()].count('\n') + 1

                self.imports.append(ImportInfo(
                    path=import_path,
                    file_path=file_path,
                    line=line_num,
                ))

    def _extract_functions(self, content: str, file_path: str, lines: List[str]) -> None:
        """Extract function definitions."""
        # Pattern for function definitions
        func_pattern = r'(?:(pub(?:\s*\([^)]*\))?|priv)\s+)?(?:(async|const|unsafe)\s+)?fn\s+(\w+)\s*\('

        for match in re.finditer(func_pattern, content):
            visibility = match.group(1) if match.group(1) else "private"
            modifiers = match.group(2) if match.group(2) else ""
            func_name = match.group(3)

            start_pos = match.start()
            line_num = content[:start_pos].count('\n') + 1

            # Find function body
            body_start = content.find('{', match.end())
            if body_start == -1:
                continue

            brace_count = 0
            body_end = body_start
            for i, c in enumerate(content[body_start:]):
                if c == '{':
                    brace_count += 1
                elif c == '}':
                    brace_count -= 1
                    if brace_count == 0:
                        body_end = body_start + i + 1
                        break

            body = content[body_start:body_end]
            signature = content[match.start():body_start]

            # Extract function calls from body
            calls = self._extract_calls(body)

            # Calculate end line
            end_line = content[:body_end].count('\n') + 1

            func_key = f"{file_path}::{func_name}"
            self.functions[func_key] = FunctionInfo(
                name=func_name,
                file_path=file_path,
                line_start=line_num,
                line_end=end_line,
                signature=signature.strip(),
                body=body,
                visibility=visibility.strip() if visibility else "private",
                is_async="async" in modifiers,
                calls=calls,
            )

    def _extract_calls(self, body: str) -> List[str]:
        """Extract function calls from function body."""
        calls = []

        # Pattern for function calls: name(
        call_pattern = r'\b(\w+)\s*\('
        for match in re.finditer(call_pattern, body):
            call_name = match.group(1)
            # Filter out keywords
            if call_name not in ('if', 'while', 'for', 'match', 'return', 'let'):
                calls.append(call_name)

        return list(set(calls))

    def _extract_structs(self, content: str, file_path: str) -> None:
        """Extract struct/enum definitions."""
        patterns = [
            (r'(?:pub\s+)?struct\s+(\w+)', "struct"),
            (r'(?:pub\s+)?enum\s+(\w+)', "enum"),
        ]

        for pattern, kind in patterns:
            for match in re.finditer(pattern, content):
                name = match.group(1)
                line_num = content[:match.start()].count('\n') + 1
                visibility = "pub" if "pub" in content[match.start():match.end()] else "private"

                key = f"{file_path}::{name}"
                self.structs[key] = StructInfo(
                    name=name,
                    file_path=file_path,
                    line_start=line_num,
                    kind=kind,
                    visibility=visibility,
                )

    def _extract_modules(self, content: str, file_path: str) -> None:
        """Extract module declarations."""
        mod_pattern = r'(?:pub\s+)?mod\s+(\w+)\s*;'

        for match in re.finditer(mod_pattern, content):
            name = match.group(1)
            line_num = content[:match.start()].count('\n') + 1

            self.modules.append(ModuleInfo(
                name=name,
                file_path=file_path,
                line=line_num,
            ))

    def get_function_by_location(self, file_path: str, line: int) -> Optional[FunctionInfo]:
        """Find function containing the given line."""
        for func in self.functions.values():
            if func.file_path == file_path:
                if func.line_start <= line <= func.line_end:
                    return func
        return None

    def get_functions_in_file(self, file_path: str) -> List[FunctionInfo]:
        """Get all functions in a file."""
        return [f for f in self.functions.values() if f.file_path == file_path]

    def get_public_api(self) -> List[FunctionInfo]:
        """Get all public functions."""
        return [f for f in self.functions.values() if "pub" in f.visibility]
