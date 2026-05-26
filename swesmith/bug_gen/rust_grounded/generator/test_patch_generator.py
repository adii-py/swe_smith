"""Test patch generator using AST analysis and LLM.

Generates test patches that detect introduced bugs. The key insight is that
the pipeline generates patches that INTRODUCE bugs (not fix them), so the
prompt must be explicit about direction so assertions are correct:
- Test should FAIL when the bug patch is applied
- Test should PASS on the original (unmodified) code
"""

import os
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from litellm import completion
from tree_sitter import Language, Parser
import tree_sitter_rust
from unidiff import PatchSet


@dataclass
class FunctionInfo:
    """Information about a function extracted from AST."""
    name: str
    params: List[str]
    return_type: Optional[str]
    is_async: bool
    is_pub: bool
    start_line: int
    end_line: int
    signature: str


@dataclass
class BugPattern:
    """Detected bug pattern from a patch."""
    bug_type: str
    affected_function: str
    changed_lines: List[int]
    description: str
    original_lines: List[str]
    buggy_lines: List[str]


class TestPatchGenerator:
    """Generate test patches that detect bugs introduced by bug patches."""

    def __init__(self, model: str = "private-large"):
        self.model = model
        self.api_key = os.getenv("LITE_LLM_API_KEY", "")
        self.api_base = os.getenv("LITE_LLM_URL", "")
        self._parser = None

    @property
    def parser(self) -> Parser:
        """Lazy-initialize tree-sitter parser."""
        if self._parser is None:
            language = Language(tree_sitter_rust.language())
            self._parser = Parser(language)
        return self._parser

    def generate_test_patch(
        self,
        bug_patch: str,
        file_path: str,
        file_content: str,
        repo_path: str = "",
    ) -> Tuple[Optional[str], Optional[List[str]]]:
        """Generate a test patch for a given bug patch.

        Args:
            bug_patch: The unified diff that introduces a bug.
            file_path: Path to the file being modified (relative to repo root).
            file_content: Current content of the file.
            repo_path: Path to the repository root (unused, kept for interface compat).

        Returns:
            (test_patch, test_names) or (None, None) on failure.
        """
        # Parse file with tree-sitter
        functions = self._extract_functions(file_content)
        if not functions:
            return None, None

        # Analyze bug patch to detect pattern and extract original/buggy lines
        bug_pattern = self._analyze_bug_patch(bug_patch, functions)
        if not bug_pattern:
            return None, None

        # Find relevant functions near the change
        patch_line = bug_pattern.changed_lines[0] if bug_pattern.changed_lines else 1
        relevant_funcs = self._find_relevant_functions(functions, patch_line)

        # Get focused context around bug location
        file_chunk = self._chunk_file_by_context(file_content, patch_line)

        # Classify bug type for better prompt guidance
        bug_type = self._classify_bug_type(
            bug_pattern.original_lines, bug_pattern.buggy_lines
        )

        # Build prompt with explicit bug direction
        prompt = self._build_prompt(
            bug_pattern, relevant_funcs, file_chunk, file_path, bug_patch, bug_type
        )

        # Call LLM
        test_code = self._call_llm_for_test(prompt)
        if not test_code:
            return None, None

        test_names = self._extract_test_names(test_code)
        if not test_names:
            return None, None

        # Create unified diff for test patch
        test_patch = self._create_test_patch(file_path, file_content, test_code)
        if not test_patch:
            return None, None

        return test_patch, test_names

    # ------------------------------------------------------------------
    # AST parsing
    # ------------------------------------------------------------------

    def _extract_functions(self, file_content: str) -> List[FunctionInfo]:
        """Parse file with tree-sitter and extract function information."""
        try:
            tree = self.parser.parse(file_content.encode("utf-8"))
            source = file_content.encode("utf-8")
            functions: List[FunctionInfo] = []
            self._traverse_for_functions(tree.root_node, source, functions)
            return functions
        except Exception as e:
            print(f"  AST parsing failed: {e}")
            return []

    def _traverse_for_functions(self, node, source: bytes, functions: List[FunctionInfo]):
        """Recursively traverse AST to find function definitions."""
        if node.type == "function_item":
            info = self._extract_function_info(node, source)
            if info:
                functions.append(info)
        for child in node.children:
            self._traverse_for_functions(child, source, functions)

    def _extract_function_info(self, node, source: bytes) -> Optional[FunctionInfo]:
        """Extract detailed information from a function AST node."""
        name = None
        params: List[str] = []
        return_type = None
        is_async = False
        is_pub = False

        for child in node.children:
            if child.type == "visibility_modifier":
                vis_text = self._node_text(child, source)
                if "pub" in vis_text:
                    is_pub = True
            elif child.type == "function_modifiers":
                mods = self._node_text(child, source)
                if "async" in mods:
                    is_async = True
            elif child.type == "identifier":
                name = self._node_text(child, source)
            elif child.type == "parameters":
                params = self._extract_parameters(child, source)
            elif child.type == "return_type":
                return_type = self._node_text(child, source).replace("->", "").strip()

        if not name:
            return None

        signature = self._node_text(node, source).split("{")[0].strip()

        return FunctionInfo(
            name=name,
            params=params,
            return_type=return_type,
            is_async=is_async,
            is_pub=is_pub,
            start_line=node.start_point[0],
            end_line=node.end_point[0],
            signature=signature,
        )

    def _extract_parameters(self, params_node, source: bytes) -> List[str]:
        """Extract parameter strings from a parameters node."""
        params: List[str] = []
        for child in params_node.children:
            if child.type in ("parameter", "self_parameter"):
                params.append(self._node_text(child, source).strip())
        return params

    @staticmethod
    def _node_text(node, source: bytes) -> str:
        """Extract text from an AST node."""
        return source[node.start_byte : node.end_byte].decode("utf-8", errors="ignore")

    # ------------------------------------------------------------------
    # Patch analysis
    # ------------------------------------------------------------------

    def _analyze_bug_patch(
        self, patch_text: str, functions: List[FunctionInfo]
    ) -> Optional[BugPattern]:
        """Analyze a bug patch to detect the bug pattern and extract original/buggy lines."""
        try:
            patch = PatchSet(patch_text)
        except Exception as e:
            print(f"  Patch parse failed: {e}")
            return None

        for file in patch:
            for hunk in file:
                original_lines: List[str] = []
                buggy_lines: List[str] = []
                added_error_handling = False
                added_validation = False
                removed_guard = False
                changed_function = None

                for line in hunk:
                    line_text = line.value
                    if line.is_removed:
                        original_lines.append(line_text)
                    elif line.is_added:
                        buggy_lines.append(line_text)

                        # Detect common bug-introduction patterns
                        if any(
                            x in line_text
                            for x in [".map_err(", ".ok_or(", "?;", "change_context("]
                        ):
                            added_error_handling = True
                        if any(
                            x in line_text
                            for x in ["if let", ".is_none()", ".is_some()", ".is_empty()"]
                        ):
                            added_validation = True
                    elif line.is_removed:
                        # Check if a guard/condition was removed
                        if any(
                            x in line_text
                            for x in ["if ", "guard", "check", "validate", "verify"]
                        ):
                            removed_guard = True

                # Find which function was changed
                for func in functions:
                    if func.start_line <= hunk.source_start <= func.end_line:
                        changed_function = func.name
                        break

                if not changed_function and functions:
                    changed_function = functions[0].name

                # Determine bug type label
                if removed_guard:
                    bug_type = "removed_guard"
                elif added_error_handling:
                    bug_type = "missing_error_handling"
                elif added_validation:
                    bug_type = "missing_validation"
                else:
                    bug_type = "unknown"

                if changed_function:
                    return BugPattern(
                        bug_type=bug_type,
                        affected_function=changed_function,
                        changed_lines=list(
                            range(
                                hunk.source_start,
                                hunk.source_start + hunk.source_length,
                            )
                        ),
                        description=f"Bug type '{bug_type}' in {changed_function}",
                        original_lines=original_lines,
                        buggy_lines=buggy_lines,
                    )

        # Fallback: return a generic pattern if we couldn't determine specifics
        if functions:
            return BugPattern(
                bug_type="unknown",
                affected_function=functions[0].name,
                changed_lines=[1],
                description="Unknown bug pattern",
                original_lines=[],
                buggy_lines=[],
            )

        return None

    def _classify_bug_type(
        self, original_lines: List[str], buggy_lines: List[str]
    ) -> str:
        """Classify the type of bug based on original vs buggy line comparison.

        Returns one of: operator_change, comparison_flip, logic_swap,
        off_by_one, missing_error_handling, missing_validation,
        removed_guard, unknown.
        """
        # Join for easier scanning
        orig = " ".join(original_lines)
        buggy = " ".join(buggy_lines)

        # Comparison flips: == <-> !=, > <-> <, >= <-> <=
        comparison_pairs = [("==", "!="), (">", "<"), (">=", "<=")]
        for a, b in comparison_pairs:
            if (a in orig and b in buggy) or (b in orig and a in buggy):
                return "comparison_flip"

        # Logic swaps: && <-> ||
        if ("&&" in orig and "||" in buggy) or ("||" in orig and "&&" in buggy):
            return "logic_swap"

        # Off-by-one: < <-> <= in loop/bound contexts
        if ("<" in orig and "<=" in buggy) or ("<=" in orig and "<" in buggy):
            return "off_by_one"

        # Operator changes: + <-> -, * <-> /
        op_pairs = [("+", "-"), ("*", "/"), ("&", "|")]
        for a, b in op_pairs:
            if (a in orig and b in buggy) or (b in orig and a in buggy):
                return "operator_change"

        # Missing error handling: removed ?, .map_err, .ok_or, etc.
        if any(x in orig for x in ["?", ".map_err(", ".ok_or(", "return Err"]):
            if not any(x in buggy for x in ["?", ".map_err(", ".ok_or(", "return Err"]):
                return "missing_error_handling"

        # Missing validation: removed if/check
        if any(
            x in orig
            for x in ["if ", ".is_none()", ".is_some()", ".is_empty()", "assert"]
        ):
            if not any(
                x in buggy
                for x in ["if ", ".is_none()", ".is_some()", ".is_empty()", "assert"]
            ):
                return "missing_validation"

        # Removed guard
        if any(x in orig for x in ["if ", "guard", "check", "validate", "verify"]):
            if not any(x in buggy for x in ["if ", "guard", "check", "validate"]):
                return "removed_guard"

        return "unknown"

    # ------------------------------------------------------------------
    # Prompt building (KEY FIX: bug direction)
    # ------------------------------------------------------------------

    def _build_prompt(
        self,
        bug_pattern: BugPattern,
        functions: List[FunctionInfo],
        file_chunk: str,
        file_path: str,
        patch_text: str,
        bug_type: str,
    ) -> str:
        """Build the LLM prompt. Critically, tells the model the patch INTRODUCES a bug."""

        # Format function signatures
        func_signatures = []
        for func in functions:
            params_str = ", ".join(func.params[:3])
            ret = f" -> {func.return_type}" if func.return_type else ""
            async_str = "async " if func.is_async else ""
            pub_str = "pub " if func.is_pub else ""
            func_signatures.append(f"{pub_str}{async_str}fn {func.name}({params_str}){ret}")

        # Build original vs buggy comparison
        comparison_section = ""
        if bug_pattern.original_lines or bug_pattern.buggy_lines:
            comparison_lines = ["## ORIGINAL vs BUGGY CODE COMPARISON"]
            if bug_pattern.original_lines:
                comparison_lines.append("### Original (correct) code:")
                comparison_lines.append("```rust")
                for line in bug_pattern.original_lines:
                    comparison_lines.append(line.rstrip())
                comparison_lines.append("```")
            if bug_pattern.buggy_lines:
                comparison_lines.append("### Buggy (patched) code:")
                comparison_lines.append("```rust")
                for line in bug_pattern.buggy_lines:
                    comparison_lines.append(line.rstrip())
                comparison_lines.append("```")
            comparison_section = "\n".join(comparison_lines)

        prompt = f"""You are a Rust testing expert. Generate a test that detects a specific bug.

**CRITICAL**: This patch INTRODUCES a bug (NOT a fix). The patched code is BUGGY. The original code is CORRECT.
Your test must FAIL when run against the buggy (patched) code and PASS against the original (correct) code.

## BUG ANALYSIS
Bug Type: {bug_type}
Affected Function: {bug_pattern.affected_function}
Description: {bug_pattern.description}

## RELEVANT FUNCTION SIGNATURES
```rust
{chr(10).join(func_signatures)}
```

## CODE CONTEXT (around bug location)
```rust
{file_chunk}
```

{comparison_section}

## BUG PATCH (introduces the bug, NOT a fix)
```diff
{patch_text[:1500]}
```

## YOUR TASK
Generate AT LEAST TWO COMPLETE, COMPILING Rust test functions in one module:

1. **Imports correctly**: Use `use super::*;` and import actual types from the module
2. **Calls REAL functions**: Use the function signatures above - only call functions that exist
3. **Triggers the bug**: Pass inputs that exercise the buggy code path
4. **Detects the bug**: Include assertions that FAIL with the buggy code but PASS with the original correct code
5. **Two tests minimum**: one primary F2P regression + one edge-case F2P test
6. **Do NOT expose exact buggy line numbers** — test expected behavior/symptoms only

## CRITICAL RULES
- The bug is INTRODUCED by the patch, so assertions should check the OPPOSITE of what the buggy code does
- ONLY call functions listed above - do not invent function names
- Use actual parameter types from the signatures
- Handle async functions with `.await` if needed
- Make sure the test COMPILES - use real types and imports
- The test should FAIL when the bug patch is applied and PASS on the original code

## OUTPUT FORMAT
Provide TWO or more `#[test]` functions in a single ```rust code block (no mod wrapper).

Example:
```rust
#[test]
fn test_regression_primary() {{ /* F2P */ }}

#[test]
fn test_regression_edge_case() {{ /* F2P edge case */ }}
```
"""
        return prompt

    # ------------------------------------------------------------------
    # LLM interaction
    # ------------------------------------------------------------------

    def _call_llm_for_test(self, prompt: str, max_retries: int = 3) -> Optional[str]:
        """Call LLM and extract test code from response."""
        for attempt in range(max_retries):
            try:
                response = completion(
                    model=f"openai/{self.model}",
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.1 + (attempt * 0.05),
                    max_tokens=4000,
                    api_key=self.api_key,
                    base_url=self.api_base,
                )

                content = response.choices[0].message.content
                test_code = self._extract_test_from_response(content)
                names = self._extract_test_names(test_code) if test_code else []
                if test_code and len(names) >= 2:
                    return test_code
                if test_code and "#[test]" in test_code and attempt == max_retries - 1:
                    return test_code

                print(f"  Test extraction attempt {attempt + 1}: no valid test found")

            except Exception as e:
                print(f"  LLM call attempt {attempt + 1} failed: {e}")

        return None

    def _extract_test_from_response(self, response: str) -> Optional[str]:
        """Extract Rust test code from LLM markdown response."""
        # Try ```rust block first
        if "```rust" in response:
            start = response.find("```rust") + 7
            end = response.find("```", start)
            if end > start:
                return response[start:end].strip()

        # Try any ``` block
        if "```" in response:
            start = response.find("```") + 3
            # Skip language label on same line
            newline = response.find("\n", start)
            if newline > start and newline - start < 20:
                start = newline + 1
            end = response.find("```", start)
            if end > start:
                return response[start:end].strip()

        # Fallback: look for #[test] pattern
        match = re.search(
            r"(\#\[test\].*?\n.*?fn\s+\w+.*?\{.*?\n\})", response, re.DOTALL
        )
        if match:
            return match.group(1).strip()

        return None

    # ------------------------------------------------------------------
    # Test patch creation
    # ------------------------------------------------------------------

    def _create_test_patch(
        self, file_path: str, file_content: str, test_code: str
    ) -> Optional[str]:
        """Create unified diff that appends a #[cfg(test)] module to the file."""
        lines = file_content.split("\n")

        # Format test module
        indented_test = "\n".join(
            ("    " + line if line.strip() else line) for line in test_code.split("\n")
        )
        test_module = f"\n#[cfg(test)]\nmod regression_tests {{\n    use super::*;\n\n{indented_test}\n}}\n"

        test_lines = test_module.split("\n")

        # Find insertion point: end of file (after last non-empty line)
        insert_line = len(lines)
        for i in range(len(lines) - 1, -1, -1):
            if lines[i].strip():
                insert_line = i + 1
                break

        # Build diff context around insertion point
        context_start = max(0, insert_line - 3)
        before_context = lines[context_start:insert_line]
        after_context = []  # Appending at end

        # Build unified diff
        old_start = context_start + 1
        old_count = len(before_context)
        new_start = context_start + 1
        new_count = len(before_context) + len(test_lines)

        diff_lines = [
            f"diff --git a/{file_path} b/{file_path}",
            f"--- a/{file_path}",
            f"+++ b/{file_path}",
            f"@@ -{old_start},{old_count} +{new_start},{new_count} @@",
        ]

        for line in before_context:
            diff_lines.append(" " + line)
        for line in test_lines:
            diff_lines.append("+" + line)
        # No after_context when appending at end

        patch = "\n".join(diff_lines) + "\n"
        return self._fix_hunk_headers(patch)

    def _fix_hunk_headers(self, patch: str) -> str:
        """Fix hunk header line counts that may be incorrect.

        LLMs and manual construction often produce wrong counts. This
        recalculates based on actual hunk content.
        """
        lines = patch.split("\n")
        result = []
        i = 0

        while i < len(lines):
            line = lines[i]
            hunk_match = re.match(r"^@@ -(\d+),(\d+) \+(\d+),(\d+) @@", line)
            if hunk_match:
                old_start = int(hunk_match.group(1))
                new_start = int(hunk_match.group(3))

                # Preserve any text after the closing @@
                header_end = line.find("@@", line.find("@@") + 2) + 2
                rest = line[header_end:]

                # Collect hunk lines and count
                i += 1
                hunk_lines = []
                old_actual = 0
                new_actual = 0

                while i < len(lines):
                    hunk_line = lines[i]
                    if re.match(r"^@@ -", hunk_line) or hunk_line.startswith("diff --git"):
                        break
                    if hunk_line.startswith("--- ") or hunk_line.startswith("+++ "):
                        break

                    hunk_lines.append(hunk_line)

                    if hunk_line.startswith("-") and not hunk_line.startswith("---"):
                        old_actual += 1
                    elif hunk_line.startswith("+") and not hunk_line.startswith("+++"):
                        new_actual += 1
                    elif hunk_line.startswith("\\"):
                        pass  # No newline marker
                    else:
                        old_actual += 1
                        new_actual += 1

                    i += 1

                # Write corrected header
                result.append(
                    f"@@ -{old_start},{old_actual} +{new_start},{new_actual} @@{rest}"
                )
                result.extend(hunk_lines)
                continue  # Don't increment i; already advanced
            else:
                result.append(line)

            i += 1

        return "\n".join(result)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _find_relevant_functions(
        self, functions: List[FunctionInfo], changed_line: int
    ) -> List[FunctionInfo]:
        """Find top 3 functions near the changed line."""
        relevant: List[FunctionInfo] = []
        for func in functions:
            if func.start_line <= changed_line <= func.end_line:
                relevant.insert(0, func)  # Most relevant first
            elif abs(func.start_line - changed_line) < 100:
                relevant.append(func)
        return relevant[:3]

    def _chunk_file_by_context(
        self, file_content: str, patch_line: int, context_size: int = 150
    ) -> str:
        """Extract ~300 lines around the patch location with overlap."""
        lines = file_content.split("\n")
        start = max(0, patch_line - context_size)
        end = min(len(lines), patch_line + context_size)
        return "\n".join(lines[start:end])

    def _extract_test_names(self, test_code: str) -> List[str]:
        """Extract only properly annotated #[test] function names from generated test code."""
        # Only match fn names that are preceded by #[test] within 3 lines
        names: List[str] = []
        lines = test_code.splitlines()
        for i, line in enumerate(lines):
            if "#[test]" in line or "#[tokio::test]" in line or "#[actix_rt::test]" in line:
                # Look for the fn definition in the next few lines
                for j in range(i + 1, min(i + 4, len(lines))):
                    m = re.match(r"\s*(?:async\s+)?fn\s+(\w+)\s*\(", lines[j])
                    if m:
                        names.append(m.group(1))
                        break
        return names
