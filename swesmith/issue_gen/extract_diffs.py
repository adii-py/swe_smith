"""Extract semantic diffs for each commit."""

import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Any
from difflib import SequenceMatcher

from dotenv import load_dotenv
from github import Github
from tree_sitter import Language, Parser
import tree_sitter_rust
from rich.console import Console
from rich.progress import track
from rich.logging import RichHandler

load_dotenv()

# Setup logging with Rich handler
logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    handlers=[RichHandler(rich_tracebacks=True, show_time=False)]
)
logger = logging.getLogger(__name__)

console = Console()


class DiffExtractor:
    """Extract semantic diffs for commits using tree-sitter AST analysis."""

    def __init__(self, repo_path: Path | None, output_dir: Path, repo_full_name: str | None = None):
        """Initialize diff extractor.

        Args:
            repo_path: Path to local git repository (optional, will use as primary source)
            output_dir: Output directory for diff data
            repo_full_name: Full repo name (e.g., 'juspay/hyperswitch') for GitHub API fallback
        """
        self.repo_path = repo_path
        self.output_dir = Path(output_dir)
        self.repo_full_name = repo_full_name
        self.has_local_repo = repo_path is not None and (repo_path / ".git").exists()
        self.has_github_api = repo_full_name is not None

        logger.info(f"Initialized DiffExtractor")
        logger.info(f"Output directory: {output_dir}")

        # Setup local git repo if available
        if self.has_local_repo:
            logger.info(f"Local git repo available: {repo_path}")

            # Check if repo is shallow clone
            shallow_file = repo_path / ".git" / "shallow"
            if shallow_file.exists():
                logger.warning("Repository is a shallow clone - some commits may be missing!")
                logger.warning("Will fall back to GitHub API for missing commits")
                console.print(f"[yellow]Warning: Shallow clone detected - will use API fallback[/yellow]")
        else:
            logger.info("No local git repo available")

        # Setup GitHub API if configured (for fallback or primary use)
        if self.has_github_api:
            github_token = os.getenv("GITHUB_TOKEN")
            if not github_token:
                if not self.has_local_repo:
                    raise ValueError("GITHUB_TOKEN required when no local repo available")
                logger.warning("No GITHUB_TOKEN - API fallback disabled")
                self.github = None
                self.github_repo = None
            else:
                self.github = Github(github_token)
                self.github_repo = self.github.get_repo(repo_full_name)
                if self.has_local_repo:
                    logger.info(f"GitHub API available for fallback: {repo_full_name}")
                else:
                    logger.info(f"Using GitHub API: {repo_full_name}")
        else:
            self.github = None
            self.github_repo = None
            if not self.has_local_repo:
                raise ValueError("Either repo_path or repo_full_name must be provided")

        # Determine mode
        if self.has_local_repo and self.has_github_api:
            console.print(f"[green]Hybrid mode: Local git with API fallback[/green]")
        elif self.has_local_repo:
            console.print(f"[cyan]Local git only (no API fallback)[/cyan]")
        else:
            console.print(f"[cyan]GitHub API only[/cyan]")

        # Setup tree-sitter parser for Rust
        self.rust_language = Language(tree_sitter_rust.language())
        self.parser = Parser(self.rust_language)
        logger.debug("Initialized tree-sitter parser for Rust")

    def get_file_content_at_commit(self, file_path: str, commit_sha: str) -> bytes | None:
        """Get file content at a specific commit.

        Args:
            file_path: Path to the file
            commit_sha: The commit SHA hash

        Returns:
            File content as bytes, or None if not found
        """
        # Try local git first if available
        if self.has_local_repo:
            try:
                result = subprocess.run(
                    ["git", "-C", str(self.repo_path), "show", f"{commit_sha}:{file_path}"],
                    capture_output=True,
                    check=True,
                )
                logger.debug(f"✓ Local git: {file_path} at {commit_sha[:7]}")
                return result.stdout
            except subprocess.CalledProcessError as e:
                logger.debug(f"✗ Local git failed for {file_path} at {commit_sha[:7]}: {e.stderr.decode() if e.stderr else 'unknown error'}")
                # Fall through to API fallback

        # Try GitHub API if available (primary or fallback)
        if self.has_github_api and self.github_repo:
            try:
                content = self.github_repo.get_contents(file_path, ref=commit_sha)
                if isinstance(content, list):
                    # It's a directory, skip
                    return None
                logger.debug(f"✓ GitHub API: {file_path} at {commit_sha[:7]}")
                return content.decoded_content
            except Exception as e:
                logger.debug(f"✗ GitHub API failed for {file_path} at {commit_sha[:7]}: {e}")
                return None

        # No source available
        return None

    def get_node_text(self, node: Any, source_code: bytes) -> str:
        """Extract text from a tree-sitter node."""
        return source_code[node.start_byte:node.end_byte].decode("utf8", errors='ignore')

    def find_top_level_imports(self, root_node: Any, source_code: bytes) -> list[str]:
        """Find all top-level imports in the source file."""
        imports = []

        def find_imports(node):
            if node.type == 'use_declaration':
                imports.append(self.get_node_text(node, source_code))
            for child in node.children:
                find_imports(child)

        find_imports(root_node)
        return imports

    def get_used_imports(self, code_block: str, all_imports: list[str]) -> list[str]:
        """Determine which imports are used in a code block."""
        used = []
        for imp in all_imports:
            parts = imp.replace(';', '').split('::')
            last_part = parts[-1].strip()
            if '{' in last_part:
                sub_imports = last_part.replace('{', '').replace('}', '').split(',')
                for sub_import in sub_imports:
                    if sub_import.strip() in code_block:
                        used.append(imp)
                        break
            elif last_part in code_block:
                used.append(imp)
        return list(set(used))

    def extract_rust_items(self, source_code: bytes, file_path: str) -> list[dict[str, Any]]:
        """Extract Rust items (functions, structs, etc.) from source code.

        Args:
            source_code: Rust source code as bytes
            file_path: Relative file path

        Returns:
            List of items with metadata
        """
        tree = self.parser.parse(source_code)
        root_node = tree.root_node

        # Find all imports
        imports = self.find_top_level_imports(root_node, source_code)

        items = []

        def traverse(node, parent=None):
            """Traverse AST and extract items."""
            item_name = None

            if node.type in ["function_item", "struct_item", "enum_item", "impl_item",
                            "trait_item", "mod_item", "const_item", "static_item",
                            "type_item", "macro_definition", "union_item"]:

                # For impl blocks, extract the type being implemented
                if node.type == "impl_item":
                    type_node = node.child_by_field_name("type")
                    item_name = self.get_node_text(type_node, source_code) if type_node else None
                else:
                    name_node = node.child_by_field_name("name")
                    item_name = self.get_node_text(name_node, source_code) if name_node else None

                code_block = self.get_node_text(node, source_code)
                used_imports = self.get_used_imports(code_block, imports)

                # Extract kind name for id
                kind_name = node.type.replace("_item", "").replace("_definition", "")

                items.append({
                    "id": f"{file_path}::{parent+'::' if parent else ''}{kind_name}::{item_name or 'unknown'}",
                    "kind": node.type,
                    "parent": parent,
                    "code": code_block,
                    "file": file_path,
                    "imports": used_imports
                })

                # Extract methods from impl blocks
                if node.type == "impl_item":
                    for child in node.children:
                        if child.type == "function_item":
                            method_name = self.get_node_text(child.child_by_field_name("name"), source_code)
                            method_code = self.get_node_text(child, source_code)
                            used_imports_method = self.get_used_imports(method_code, imports)
                            items.append({
                                "id": f"{file_path}::{item_name}::method::{method_name}",
                                "kind": "method",
                                "parent": item_name,
                                "code": method_code,
                                "file": file_path,
                                "imports": used_imports_method
                            })

                # Extract methods from trait blocks
                elif node.type == "trait_item":
                    for child in node.children:
                        if child.type == "function_item":
                            method_name = self.get_node_text(child.child_by_field_name("name"), source_code)
                            method_code = self.get_node_text(child, source_code)
                            used_imports_method = self.get_used_imports(method_code, imports)
                            items.append({
                                "id": f"{file_path}::{item_name}::trait_method::{method_name}",
                                "kind": "trait_method",
                                "parent": item_name,
                                "code": method_code,
                                "file": file_path,
                                "imports": used_imports_method
                            })

            for child in node.children:
                traverse(child, parent=item_name if node.type in ["struct_item", "impl_item", "trait_item", "mod_item"] else parent)

        traverse(root_node)
        return items

    def get_modified_rust_files(self, commit_sha: str) -> list[str]:
        """Get list of Rust files modified in a commit."""
        # Try local git first if available
        if self.has_local_repo:
            try:
                result = subprocess.run(
                    ['git', '-C', str(self.repo_path), 'diff-tree', '--no-commit-id', '--name-only', '-r', commit_sha],
                    capture_output=True,
                    text=True,
                    check=True
                )
                files = result.stdout.strip().split('\n')
                rust_files = [f for f in files if f.endswith('.rs')]
                logger.debug(f"✓ Local git: Found {len(rust_files)} Rust files in {commit_sha[:7]}")
                return rust_files
            except subprocess.CalledProcessError as e:
                logger.debug(f"✗ Local git failed for commit {commit_sha[:7]}: {e.stderr if e.stderr else 'unknown error'}")
                # Fall through to API fallback

        # Try GitHub API if available (primary or fallback)
        if self.has_github_api and self.github_repo:
            try:
                commit = self.github_repo.get_commit(commit_sha)
                files = []
                for file in commit.files:
                    if file.filename.endswith('.rs'):
                        files.append(file.filename)
                logger.debug(f"✓ GitHub API: Found {len(files)} Rust files in {commit_sha[:7]}")
                return files
            except Exception as e:
                logger.debug(f"✗ GitHub API failed for commit {commit_sha[:7]}: {e}")
                return []

        # No source available
        return []

    def compute_diff_indices(self, before_code: str, after_code: str) -> tuple[list[int], list[int]]:
        """Compute the indices of changed lines between two code snippets."""
        before_lines = before_code.splitlines()
        after_lines = after_code.splitlines()
        matcher = SequenceMatcher(None, before_lines, after_lines, autojunk=False)

        before_indices = []
        after_indices = []

        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag != 'equal':
                before_indices.extend(range(i1, i2))
                after_indices.extend(range(j1, j2))

        # Fallback: If strings are different but no diffs were found
        if not before_indices and not after_indices and before_code != after_code:
            return list(range(len(before_lines))), list(range(len(after_lines)))

        return before_indices, after_indices

    def extract_context_lines(self, code: str, diff_indices: list[int], context: int = 2) -> str:
        """Extract context lines around the given diff indices."""
        if not diff_indices:
            return ""

        lines = code.splitlines()
        min_index = max(0, min(diff_indices) - context)
        max_index = min(len(lines), max(diff_indices) + context + 1)

        return "\n".join(lines[min_index:max_index])

    def build_semantic_diff(self, items_before: list[dict], items_after: list[dict], commit_sha: str) -> list[dict]:
        """Build semantic diff between two sets of Rust items."""
        # Create maps by id
        before_map = {item['id']: item for item in items_before}
        after_map = {item['id']: item for item in items_after}

        ids_before = set(before_map.keys())
        ids_after = set(after_map.keys())

        common_ids = ids_before.intersection(ids_after)
        added_ids = ids_after - ids_before
        removed_ids = ids_before - ids_after

        diffs = []

        # Modified items
        for item_id in common_ids:
            node_before = before_map[item_id]
            node_after = after_map[item_id]

            code_changed = node_before.get('code') != node_after.get('code')
            imports_changed = node_before.get('imports') != node_after.get('imports')

            if code_changed or imports_changed:
                before_code = node_before.get('code', '')
                after_code = node_after.get('code', '')

                before_indices, after_indices = self.compute_diff_indices(before_code, after_code)
                before_context = self.extract_context_lines(before_code, before_indices)
                after_context = self.extract_context_lines(after_code, after_indices)

                diff_entry = {
                    "id": item_id,
                    "file": node_after.get('file'),
                    "kind": node_after.get('kind'),
                    "status": "modified",
                    "code_changed": code_changed,
                    "imports_changed": imports_changed,
                    "before_code": before_code,
                    "after_code": after_code,
                    "diff_span": {
                        "before": before_context,
                        "after": after_context
                    },
                    "commit_sha": commit_sha
                }

                if imports_changed:
                    diff_entry["before_imports"] = node_before.get('imports', [])
                    diff_entry["after_imports"] = node_after.get('imports', [])

                diffs.append(diff_entry)

        # Added items
        for item_id in added_ids:
            node = after_map[item_id]
            diffs.append({
                "id": item_id,
                "file": node.get('file'),
                "kind": node.get('kind'),
                "status": "added",
                "before_code": None,
                "after_code": node.get('code'),
                "diff_span": None,
                "commit_sha": commit_sha
            })

        # Removed items
        for item_id in removed_ids:
            node = before_map[item_id]
            diffs.append({
                "id": item_id,
                "file": node.get('file'),
                "kind": node.get('kind'),
                "status": "removed",
                "before_code": node.get('code'),
                "after_code": None,
                "diff_span": None,
                "commit_sha": commit_sha
            })

        return diffs

    def extract_commit_diff(self, commit_sha: str, pr_number: int) -> dict[str, Any]:
        """Extract semantic diff for a single commit.

        Args:
            commit_sha: The commit SHA hash
            pr_number: The PR number this commit belongs to

        Returns:
            Dict with commit info and diffs
        """
        # Get list of modified Rust files
        rust_files = self.get_modified_rust_files(commit_sha)

        if not rust_files:
            # No Rust files modified
            return {
                "commit_sha": commit_sha,
                "pr_number": pr_number,
                "rust_files": [],
                "diffs": []
            }

        # Extract items before and after commit
        all_items_before = []
        all_items_after = []

        for file_path in rust_files:
            # Get content before commit (parent)
            parent_sha = f"{commit_sha}^"
            before_content = self.get_file_content_at_commit(file_path, parent_sha)
            if before_content:
                try:
                    items_before = self.extract_rust_items(before_content, file_path)
                    all_items_before.extend(items_before)
                except Exception:
                    pass  # Parsing errors are expected for some commits

            # Get content after commit
            after_content = self.get_file_content_at_commit(file_path, commit_sha)
            if after_content:
                try:
                    items_after = self.extract_rust_items(after_content, file_path)
                    all_items_after.extend(items_after)
                except Exception:
                    pass

        # Build semantic diff
        diffs = self.build_semantic_diff(all_items_before, all_items_after, commit_sha)

        return {
            "commit_sha": commit_sha,
            "pr_number": pr_number,
            "rust_files": rust_files,
            "diffs": diffs
        }

    def extract_pr_diffs(self, pr_file: Path) -> None:
        """Extract diffs for all commits in a PR.

        Args:
            pr_file: Path to PR JSON file
        """
        # Load PR data
        with open(pr_file, 'r') as f:
            pr_data = json.load(f)

        pr_number = pr_data["number"]
        commits = pr_data["commits"]

        if not commits:
            return

        # Create output directory for this PR
        pr_output_dir = self.output_dir / f"pr_{pr_number}"
        pr_output_dir.mkdir(parents=True, exist_ok=True)

        console.print(f"\n[cyan]Processing PR #{pr_number}: {pr_data['title']}[/cyan]")

        # Process each commit
        for commit in track(commits, description=f"  Extracting diffs"):
            commit_sha = commit["sha"]
            commit_sha_short = commit_sha[:7]

            # Create commit directory
            commit_dir = pr_output_dir / commit_sha_short
            commit_dir.mkdir(parents=True, exist_ok=True)

            # Check if already processed
            diff_file = commit_dir / "diff.json"
            if diff_file.exists():
                continue

            # Extract diff
            diff_data = self.extract_commit_diff(commit_sha, pr_number)

            # Save diff
            with open(diff_file, 'w') as f:
                json.dump(diff_data, f, indent=2)

    def extract_all(self, pr_dir: Path, resume: bool = True) -> dict[str, Any]:
        """Extract diffs for all PRs.

        Args:
            pr_dir: Directory containing PR files
            resume: Skip commits that already have diffs

        Returns:
            Summary statistics
        """
        pr_files = sorted(pr_dir.glob("pr_*.json"))

        if not pr_files:
            console.print(f"[yellow]No PR files found in {pr_dir}[/yellow]")
            return {"total_prs": 0, "total_commits": 0}

        console.print(f"\n[bold cyan]Extracting diffs[/bold cyan]")
        if self.has_local_repo and self.has_github_api:
            console.print(f"[dim]Mode: Hybrid (Local git → GitHub API fallback)[/dim]")
            console.print(f"[dim]Local: {self.repo_path}[/dim]")
            console.print(f"[dim]API: {self.repo_full_name}[/dim]")
        elif self.has_local_repo:
            console.print(f"[dim]Mode: Local git only[/dim]")
            console.print(f"[dim]Source: {self.repo_path}[/dim]")
        else:
            console.print(f"[dim]Mode: GitHub API only[/dim]")
            console.print(f"[dim]Source: {self.repo_full_name}[/dim]")
        console.print(f"[dim]Output: {self.output_dir}[/dim]")

        total_commits = 0
        total_prs = len(pr_files)

        for pr_file in pr_files:
            try:
                self.extract_pr_diffs(pr_file)

                # Count commits
                with open(pr_file, 'r') as f:
                    pr_data = json.load(f)
                    total_commits += len(pr_data.get("commits", []))

            except Exception as e:
                console.print(f"[red]Error processing {pr_file.name}: {e}[/red]")
                logger.error(f"Error processing {pr_file.name}: {e}")

        console.print(f"\n[bold green]✓ Diff extraction complete![/bold green]")
        console.print(f"\n[bold]Summary:[/bold]")
        console.print(f"  PRs processed: {total_prs}")
        console.print(f"  Total commits: {total_commits}")
        console.print(f"  Output: {self.output_dir}")

        return {
            "total_prs": total_prs,
            "total_commits": total_commits,
            "output_dir": str(self.output_dir)
        }
