"""Repository-wide parsing using cargo metadata and AST extraction."""

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Set

from .rust_ast import RustAstExtractor, FunctionInfo, StructInfo


@dataclass
class CrateInfo:
    """Information about a Rust crate."""
    name: str
    version: str
    path: str
    dependencies: List[str] = field(default_factory=list)
    targets: List[str] = field(default_factory=list)
    is_workspace_member: bool = True


@dataclass
class WorkspaceInfo:
    """Rust workspace information."""
    root_path: str
    members: List[CrateInfo] = field(default_factory=list)
    target_directory: str = ""


class RepositoryParser:
    """Parse Rust repository structure."""

    def __init__(self, repo_path: str):
        self.repo_path = Path(repo_path)
        self.workspace: Optional[WorkspaceInfo] = None
        self.ast_extractor = RustAstExtractor()
        self.all_rust_files: List[str] = []

    def parse_full_repository(self) -> None:
        """Parse the entire repository."""
        print("Parsing repository structure...")

        # Parse cargo metadata
        self._parse_cargo_metadata()

        # Find all Rust files
        self._collect_rust_files()

        # Parse AST for all files
        self._parse_all_files()

        # Build call relationships
        self._build_call_graph()

        print(f"Parsed {len(self.ast_extractor.functions)} functions from {len(self.all_rust_files)} files")

    def _parse_cargo_metadata(self) -> None:
        """Parse cargo metadata to get workspace structure."""
        try:
            result = subprocess.run(
                ["cargo", "metadata", "--format-version", "1"],
                cwd=self.repo_path,
                capture_output=True,
                text=True,
                timeout=60
            )

            if result.returncode != 0:
                print(f"Warning: cargo metadata failed: {result.stderr}")
                return

            metadata = json.loads(result.stdout)

            self.workspace = WorkspaceInfo(
                root_path=metadata.get("workspace_root", str(self.repo_path)),
                target_directory=metadata.get("target_directory", ""),
            )

            # Parse packages/crates
            for pkg in metadata.get("packages", []):
                if pkg.get("source") is None:  # Local crate
                    crate = CrateInfo(
                        name=pkg["name"],
                        version=pkg["version"],
                        path=pkg["manifest_path"],
                        dependencies=[dep["name"] for dep in pkg.get("dependencies", [])],
                        targets=[t["name"] for t in pkg.get("targets", [])],
                    )
                    self.workspace.members.append(crate)

        except Exception as e:
            print(f"Warning: Failed to parse cargo metadata: {e}")

    def _collect_rust_files(self) -> None:
        """Collect all Rust source files."""
        # Skip target directory and tests
        exclude_patterns = ["target/", ".git/", "tests/", "benches/"]

        for rust_file in self.repo_path.rglob("*.rs"):
            file_str = str(rust_file.relative_to(self.repo_path))

            # Skip excluded patterns
            if any(pat in file_str for pat in exclude_patterns):
                continue

            self.all_rust_files.append(file_str)

    def _parse_all_files(self) -> None:
        """Parse AST for all collected files."""
        for file_path in self.all_rust_files:
            full_path = self.repo_path / file_path
            self.ast_extractor.parse_file(full_path, file_path)

    def _build_call_graph(self) -> None:
        """Build caller-callee relationships."""
        # For each function, find who calls it
        for func_key, func in self.ast_extractor.functions.items():
            for call_name in func.calls:
                # Find the called function
                for other_key, other_func in self.ast_extractor.functions.items():
                    if other_func.name == call_name:
                        if func_key not in other_func.callers:
                            other_func.callers.append(func_key)

    def get_file_crate(self, file_path: str) -> Optional[CrateInfo]:
        """Get the crate that owns a file."""
        if not self.workspace:
            return None

        for crate in self.workspace.members:
            crate_root = Path(crate.path).parent
            file_abs = self.repo_path / file_path
            try:
                file_abs.relative_to(crate_root)
                return crate
            except ValueError:
                continue

        return None

    def get_test_files(self) -> List[str]:
        """Find all test files."""
        test_files = []
        for rust_file in self.all_rust_files:
            if "test" in rust_file or rust_file.endswith("_tests.rs"):
                test_files.append(rust_file)
        return test_files

    def get_hotspot_functions(self, top_n: int = 20) -> List[FunctionInfo]:
        """Get highly-connected functions (hotspots)."""
        scored = []

        for func in self.ast_extractor.functions.values():
            # Score by connectivity
            score = len(func.calls) + len(func.callers)

            # Bonus for public functions
            if "pub" in func.visibility:
                score += 5

            # Penalty for getters/setters
            if func.name.startswith("get_") or func.name.startswith("set_"):
                score -= 3

            scored.append((score, func))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [f for _, f in scored[:top_n]]

    def get_function_context(self, func_key: str) -> dict:
        """Get rich context for a function."""
        if func_key not in self.ast_extractor.functions:
            return {}

        func = self.ast_extractor.functions[func_key]

        # Get related functions
        callees = []
        callers = []

        for call in func.calls:
            for key, f in self.ast_extractor.functions.items():
                if f.name == call:
                    callees.append(key)

        for caller_key in func.callers:
            callers.append(caller_key)

        return {
            "function": func,
            "callees": callees,
            "callers": callers,
            "crate": self.get_file_crate(func.file_path),
            "imports": [imp for imp in self.ast_extractor.imports if imp.file_path == func.file_path],
        }
