"""AST-based code transformation using tree-sitter."""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Callable, Tuple

HAS_TREE_SITTER = False
try:
    from tree_sitter import Language, Parser, Node, Tree
    import tree_sitter_rust as ts_rust
    HAS_TREE_SITTER = True
except ImportError:
    pass

from .mutation_planner import MutationPlan, MutationInstruction


@dataclass
class TransformResult:
    """Result of a transformation."""
    success: bool
    modified_code: Optional[str]
    error_message: str = ""
    line_changes: List[Tuple[int, int]] = None  # (old_line, new_line) mappings


class RustAstTransformer:
    """Apply mutations programmatically using tree-sitter AST."""

    def __init__(self):
        self.parser = None
        self.has_tree_sitter = HAS_TREE_SITTER
        if self.has_tree_sitter:
            try:
                # tree-sitter 0.21+ API: LANGUAGE is a constant
                lang = getattr(ts_rust, 'LANGUAGE', None) or ts_rust.language()
                self.parser = Parser(lang)
            except Exception as e:
                print(f"Warning: Failed to initialize tree-sitter parser: {e}")
                self.has_tree_sitter = False

        # Register transformation handlers
        self.transformers: Dict[str, Callable] = {
            # Basic mutations
            "flip_comparison": self._transform_flip_comparison,
            "remove_guard": self._transform_remove_guard,
            "reorder_statements": self._transform_reorder_statements,
            "change_operator": self._transform_change_operator,
            "invert_boolean": self._transform_invert_boolean,
            "remove_error_handling": self._transform_remove_error_handling,
            "swap_arguments": self._transform_swap_arguments,
            "off_by_one": self._transform_off_by_one,
            "missing_return": self._transform_missing_return,
            "logic_swap": self._transform_logic_swap,
            # Complex mutations
            "state_mismatch": self._transform_state_mismatch,
            "async_reordering": self._transform_async_reordering,
            "trait_impl_bug": self._transform_trait_impl_bug,
            "error_propagation": self._transform_error_propagation,
            "type_confusion": self._transform_type_confusion,
            "cache_invalidation": self._transform_cache_invalidation,
            "permission_check": self._transform_permission_check,
            "transaction_boundary": self._transform_transaction_boundary,
            "lock_ordering": self._transform_lock_ordering,
            "lifetime_manipulation": self._transform_lifetime_manipulation,
        }

    def apply_mutations(
        self,
        file_contents: Dict[str, str],
        plan: MutationPlan,
    ) -> Dict[str, TransformResult]:
        """Apply all mutations from a plan to the code."""
        results = {}
        modified_files = dict(file_contents)

        # Group mutations by file
        mutations_by_file: Dict[str, List[MutationInstruction]] = {}
        for mutation in plan.mutations:
            file_path = mutation.target_file
            if file_path not in mutations_by_file:
                mutations_by_file[file_path] = []
            mutations_by_file[file_path].append(mutation)

        # Apply mutations file by file
        for file_path, mutations in mutations_by_file.items():
            if file_path not in modified_files:
                results[file_path] = TransformResult(
                    success=False,
                    modified_code=None,
                    error_message=f"File not in context: {file_path}"
                )
                continue

            code = modified_files[file_path]
            result = self._apply_mutations_to_file(code, mutations)
            results[file_path] = result

            if result.success:
                modified_files[file_path] = result.modified_code

        return results

    def _apply_mutations_to_file(
        self,
        code: str,
        mutations: List[MutationInstruction],
    ) -> TransformResult:
        """Apply multiple mutations to a single file."""
        modified_code = code
        all_line_changes = []

        # Sort mutations by location (line number) in reverse order
        sorted_mutations = sorted(
            mutations,
            key=lambda m: self._extract_line_number(m.location_hint),
            reverse=True
        )

        for mutation in sorted_mutations:
            transformer = self.transformers.get(mutation.type)
            if not transformer:
                return TransformResult(
                    success=False,
                    modified_code=None,
                    error_message=f"Unknown mutation type: {mutation.type}"
                )

            result = transformer(modified_code, mutation)
            if not result.success:
                # Try fallback text-based transformation
                result = self._fallback_text_transform(modified_code, mutation)
                if not result.success:
                    return result

            modified_code = result.modified_code

            # Validate and fix syntax after each mutation
            syntax_check = self._validate_syntax(modified_code)
            if not syntax_check.success:
                # Try to fix syntax
                fixed_code = self._fix_syntax_errors(modified_code)
                if fixed_code:
                    modified_code = fixed_code
                else:
                    return TransformResult(
                        success=False,
                        modified_code=None,
                        error_message=f"Syntax error after mutation: {syntax_check.error_message}"
                    )

            if result.line_changes:
                all_line_changes.extend(result.line_changes)

        # Final validation
        final_check = self._validate_syntax(modified_code)
        if not final_check.success:
            fixed_code = self._fix_syntax_errors(modified_code)
            if fixed_code:
                modified_code = fixed_code
            else:
                return TransformResult(
                    success=False,
                    modified_code=None,
                    error_message=f"Final syntax check failed: {final_check.error_message}"
                )

        return TransformResult(
            success=True,
            modified_code=modified_code,
            line_changes=all_line_changes
        )

    def _extract_line_number(self, location_hint: str) -> int:
        """Extract line number from location hint."""
        match = re.search(r'[Ll]ine\s+(\d+)', location_hint)
        if match:
            return int(match.group(1))
        return 0

    def _parse_ast(self, code: str):
        """Parse code into AST."""
        if not self.parser or not self.has_tree_sitter:
            return None
        try:
            return self.parser.parse(bytes(code, 'utf8'))
        except Exception as e:
            print(f"AST parsing failed: {e}")
            return None

    def _find_node_at_line(self, root: Node, target_line: int) -> Optional[Node]:
        """Find AST node at specific line number."""
        def traverse(node: Node) -> Optional[Node]:
            start_line = node.start_point[0] + 1  # 0-indexed to 1-indexed
            end_line = node.end_point[0] + 1

            if start_line <= target_line <= end_line:
                # Check children first (more specific)
                for child in node.children:
                    found = traverse(child)
                    if found:
                        return found
                return node
            return None

        return traverse(root)

    def _get_node_text(self, code: str, node: Node) -> str:
        """Get text for an AST node."""
        return code[node.start_byte:node.end_byte]

    def _replace_node(self, code: str, node: Node, replacement: str) -> str:
        """Replace an AST node with new text."""
        return code[:node.start_byte] + replacement + code[node.end_byte:]

    # ============ Transformation Handlers ============

    def _transform_flip_comparison(
        self,
        code: str,
        mutation: MutationInstruction,
    ) -> TransformResult:
        """Flip comparison operator (== to !=, > to <, etc.)."""
        details = mutation.mutation_details
        old_op = details.get("operator", "==")
        new_op = details.get("new_operator", "!=")

        ast = self._parse_ast(code)
        if ast:
            line = self._extract_line_number(mutation.location_hint)
            node = self._find_node_at_line(ast.root_node, line)

            if node and node.type == "binary_expression":
                node_text = self._get_node_text(code, node)
                if old_op in node_text:
                    new_text = node_text.replace(old_op, new_op, 1)
                    modified = self._replace_node(code, node, new_text)
                    return TransformResult(success=True, modified_code=modified)

        # Fallback: text-based replacement
        return self._fallback_text_transform(code, mutation, old_op, new_op)

    def _transform_remove_guard(
        self,
        code: str,
        mutation: MutationInstruction,
    ) -> TransformResult:
        """Remove a guard condition (if statement or check)."""
        fragment = mutation.original_fragment.strip()

        ast = self._parse_ast(code)
        if ast:
            line = self._extract_line_number(mutation.location_hint)
            node = self._find_node_at_line(ast.root_node, line)

            # Look for if_expression
            if node:
                current = node
                while current and current.type != "if_expression":
                    current = current.parent

                if current and current.type == "if_expression":
                    if_text = self._get_node_text(code, current)
                    # Check if it matches our fragment
                    if self._fragment_matches(if_text, fragment):
                        # For simple if without else, replace with body only
                        # For if-else, more complex
                        return self._remove_if_statement(code, current)

        # Text-based fallback
        lines = code.split('\n')
        for i, line in enumerate(lines):
            if fragment.split('\n')[0].strip() in line:
                # Find the full if block and remove it
                return self._text_remove_guard(code, fragment, i)

        return TransformResult(
            success=False,
            modified_code=None,
            error_message=f"Could not find guard to remove: {fragment[:50]}..."
        )

    def _transform_reorder_statements(
        self,
        code: str,
        mutation: MutationInstruction,
    ) -> TransformResult:
        """Swap order of two statements."""
        details = mutation.mutation_details
        stmt1 = details.get("statement1", "")
        stmt2 = details.get("statement2", "")

        if not stmt1 or not stmt2:
            return TransformResult(
                success=False,
                modified_code=None,
                error_message="Missing statement details for reordering"
            )

        # Find and swap statements
        lines = code.split('\n')
        idx1 = idx2 = -1

        for i, line in enumerate(lines):
            if stmt1.split('\n')[0].strip() in line and idx1 == -1:
                idx1 = i
            elif stmt2.split('\n')[0].strip() in line and idx2 == -1:
                idx2 = i

        if idx1 == -1 or idx2 == -1:
            return TransformResult(
                success=False,
                modified_code=None,
                error_message="Could not find statements to reorder"
            )

        lines[idx1], lines[idx2] = lines[idx2], lines[idx1]
        return TransformResult(success=True, modified_code='\n'.join(lines))

    def _transform_change_operator(
        self,
        code: str,
        mutation: MutationInstruction,
    ) -> TransformResult:
        """Change arithmetic operator."""
        details = mutation.mutation_details
        old_op = details.get("operator", "+")
        new_op = details.get("new_operator", "-")

        # Similar to flip_comparison
        return self._fallback_text_transform(code, mutation, old_op, new_op)

    def _transform_invert_boolean(
        self,
        code: str,
        mutation: MutationInstruction,
    ) -> TransformResult:
        """Invert boolean condition or return value."""
        fragment = mutation.original_fragment.strip()

        # Look for return statements or boolean expressions
        if 'return' in fragment:
            # Invert return value
            if 'true' in fragment:
                new_fragment = fragment.replace('true', 'false')
            elif 'false' in fragment:
                new_fragment = fragment.replace('false', 'true')
            else:
                # Wrap with !
                new_fragment = fragment.replace('return ', 'return !')

            modified = code.replace(fragment, new_fragment, 1)
            return TransformResult(success=True, modified_code=modified)

        return self._fallback_text_transform(code, mutation, fragment, f"!({fragment})")

    def _transform_remove_error_handling(
        self,
        code: str,
        mutation: MutationInstruction,
    ) -> TransformResult:
        """Remove ? operator from expressions."""
        fragment = mutation.original_fragment.strip()

        # Remove ? from the fragment
        new_fragment = fragment.replace('?', '')
        modified = code.replace(fragment, new_fragment, 1)

        if modified != code:
            return TransformResult(success=True, modified_code=modified)

        return TransformResult(
            success=False,
            modified_code=None,
            error_message="Could not find error handling to remove"
        )

    def _transform_swap_arguments(
        self,
        code: str,
        mutation: MutationInstruction,
    ) -> TransformResult:
        """Swap order of function arguments."""
        details = mutation.mutation_details
        arg1 = details.get("argument1", "")
        arg2 = details.get("argument2", "")

        fragment = mutation.original_fragment.strip()

        # Find function call and swap arguments
        if arg1 and arg2:
            new_fragment = fragment.replace(arg1, "__TEMP__").replace(arg2, arg1).replace("__TEMP__", arg2)
            modified = code.replace(fragment, new_fragment, 1)
            return TransformResult(success=True, modified_code=modified)

        return TransformResult(
            success=False,
            modified_code=None,
            error_message="Missing argument details for swapping"
        )

    def _transform_off_by_one(
        self,
        code: str,
        mutation: MutationInstruction,
    ) -> TransformResult:
        """Adjust numeric value by +/- 1."""
        details = mutation.mutation_details
        direction = details.get("direction", "+1")  # +1 or -1
        pattern = details.get("pattern", "")  # e.g., "< len" or "<= len"

        fragment = mutation.original_fragment.strip()

        if direction == "+1":
            # Change < to <=, or len to len + 1
            if '<' in fragment and '<=' not in fragment:
                new_fragment = fragment.replace('<', '<=', 1)
            elif '- 1' in fragment:
                new_fragment = fragment.replace('- 1', '', 1)
            else:
                return TransformResult(
                    success=False,
                    modified_code=None,
                    error_message="Could not apply +1 offset"
                )
        else:  # -1
            if '<=' in fragment:
                new_fragment = fragment.replace('<=', '<', 1)
            else:
                return TransformResult(
                    success=False,
                    modified_code=None,
                    error_message="Could not apply -1 offset"
                )

        modified = code.replace(fragment, new_fragment, 1)
        return TransformResult(success=True, modified_code=modified)

    def _transform_missing_return(
        self,
        code: str,
        mutation: MutationInstruction,
    ) -> TransformResult:
        """Remove early return or break."""
        fragment = mutation.original_fragment.strip()

        # Comment out or remove the return statement
        lines = fragment.split('\n')
        modified_lines = []

        for line in lines:
            stripped = line.strip()
            if stripped.startswith('return '):
                # Comment out the return
                modified_lines.append('//' + line)
            else:
                modified_lines.append(line)

        new_fragment = '\n'.join(modified_lines)
        modified = code.replace(fragment, new_fragment, 1)

        return TransformResult(success=True, modified_code=modified)

    def _transform_logic_swap(
        self,
        code: str,
        mutation: MutationInstruction,
    ) -> TransformResult:
        """Swap && with || or vice versa."""
        details = mutation.mutation_details
        old_op = details.get("operator", "&&")
        new_op = details.get("new_operator", "||")

        fragment = mutation.original_fragment.strip()
        new_fragment = fragment.replace(old_op, new_op)

        modified = code.replace(fragment, new_fragment, 1)
        return TransformResult(success=True, modified_code=modified)

    # ============ Complex Mutation Handlers ============

    def _transform_state_mismatch(
        self,
        code: str,
        mutation: MutationInstruction,
    ) -> TransformResult:
        """Change state transition or state check logic."""
        # Map to flip_comparison or remove_guard based on context
        details = mutation.mutation_details
        change_type = details.get("change", "")

        if "transition" in change_type.lower():
            # Allow invalid state transition by flipping condition
            return self._transform_flip_comparison(code, mutation)
        else:
            # Remove state validation check
            return self._transform_remove_guard(code, mutation)

    def _transform_async_reordering(
        self,
        code: str,
        mutation: MutationInstruction,
    ) -> TransformResult:
        """Reorder async operations or await points."""
        # Map to reorder_statements for async blocks
        details = mutation.mutation_details

        # Look for .await patterns and reorder
        fragment = mutation.original_fragment.strip()
        lines = fragment.split('\n')

        # Find lines with .await
        await_lines = [(i, line) for i, line in enumerate(lines) if '.await' in line]

        if len(await_lines) >= 2:
            # Swap first two await lines
            idx1, line1 = await_lines[0]
            idx2, line2 = await_lines[1]
            lines[idx1], lines[idx2] = line2, line1
            new_fragment = '\n'.join(lines)
            modified = code.replace(fragment, new_fragment, 1)
            return TransformResult(success=True, modified_code=modified)

        return self._fallback_text_transform(code, mutation)

    def _transform_trait_impl_bug(
        self,
        code: str,
        mutation: MutationInstruction,
    ) -> TransformResult:
        """Modify trait implementation behavior."""
        # Map to flip_comparison or invert_boolean
        details = mutation.mutation_details

        if "bool" in details.get("return_type", "").lower():
            return self._transform_invert_boolean(code, mutation)
        else:
            return self._transform_flip_comparison(code, mutation)

    def _transform_error_propagation(
        self,
        code: str,
        mutation: MutationInstruction,
    ) -> TransformResult:
        """Alter error conversion or propagation paths."""
        # Map to remove_error_handling or swap error types
        details = mutation.mutation_details

        if "?" in mutation.original_fragment:
            return self._transform_remove_error_handling(code, mutation)
        else:
            # Swap error types in conversion
            return self._fallback_text_transform(code, mutation)

    def _transform_type_confusion(
        self,
        code: str,
        mutation: MutationInstruction,
    ) -> TransformResult:
        """Swap similar types (String vs &str, Vec vs slice)."""
        details = mutation.mutation_details
        old_type = details.get("old_type", "")
        new_type = details.get("new_type", "")

        fragment = mutation.original_fragment.strip()

        if old_type and new_type and old_type in fragment:
            new_fragment = fragment.replace(old_type, new_type, 1)
            modified = code.replace(fragment, new_fragment, 1)
            return TransformResult(success=True, modified_code=modified)

        return self._fallback_text_transform(code, mutation)

    def _transform_cache_invalidation(
        self,
        code: str,
        mutation: MutationInstruction,
    ) -> TransformResult:
        """Break cache invalidation logic."""
        # Remove cache invalidation calls or flip conditions
        details = mutation.mutation_details

        if "invalidate" in mutation.original_fragment.lower():
            # Comment out cache invalidation
            fragment = mutation.original_fragment.strip()
            new_fragment = "// " + fragment.replace('\n', '\n// ')
            modified = code.replace(fragment, new_fragment, 1)
            return TransformResult(success=True, modified_code=modified)

        return self._transform_remove_guard(code, mutation)

    def _transform_permission_check(
        self,
        code: str,
        mutation: MutationInstruction,
    ) -> TransformResult:
        """Modify authorization/permission validation."""
        # Map to remove_guard or flip_comparison
        details = mutation.mutation_details

        if "check" in details.get("change", "").lower():
            return self._transform_remove_guard(code, mutation)
        else:
            return self._transform_flip_comparison(code, mutation)

    def _transform_transaction_boundary(
        self,
        code: str,
        mutation: MutationInstruction,
    ) -> TransformResult:
        """Move transaction begin/commit points."""
        # Reorder statements involving transaction calls
        return self._transform_reorder_statements(code, mutation)

    def _transform_lock_ordering(
        self,
        code: str,
        mutation: MutationInstruction,
    ) -> TransformResult:
        """Change lock acquisition order."""
        # Reorder lock acquisition statements
        return self._transform_reorder_statements(code, mutation)

    def _transform_lifetime_manipulation(
        self,
        code: str,
        mutation: MutationInstruction,
    ) -> TransformResult:
        """Change lifetime bounds or elision."""
        details = mutation.mutation_details
        old_lt = details.get("old_lifetime", "")
        new_lt = details.get("new_lifetime", "")

        fragment = mutation.original_fragment.strip()

        if old_lt and new_lt and old_lt in fragment:
            new_fragment = fragment.replace(old_lt, new_lt, 1)
            modified = code.replace(fragment, new_fragment, 1)
            return TransformResult(success=True, modified_code=modified)

        return self._fallback_text_transform(code, mutation)

    # ============ Syntax Validation & Fixing ============

    @dataclass
    class SyntaxCheckResult:
        success: bool
        error_message: str = ""

    def _validate_syntax(self, code: str) -> SyntaxCheckResult:
        """Basic syntax validation for Rust code."""
        # Check brace balance
        open_braces = code.count('{')
        close_braces = code.count('}')
        if open_braces != close_braces:
            return self.SyntaxCheckResult(
                success=False,
                error_message=f"Unbalanced braces: {open_braces} open, {close_braces} close"
            )

        # Check parenthesis balance
        open_parens = code.count('(')
        close_parens = code.count(')')
        if open_parens != close_parens:
            return self.SyntaxCheckResult(
                success=False,
                error_message=f"Unbalanced parentheses: {open_parens} open, {close_parens} close"
            )

        # Check bracket balance
        open_brackets = code.count('[')
        close_brackets = code.count(']')
        if open_brackets != close_brackets:
            return self.SyntaxCheckResult(
                success=False,
                error_message=f"Unbalanced brackets: {open_brackets} open, {close_brackets} close"
            )

        # Check for common Rust syntax issues
        lines = code.split('\n')
        for i, line in enumerate(lines, 1):
            # Check for unmatched quotes (rough check)
            single_quotes = line.count("'") - line.count("\\'")
            double_quotes = line.count('"') - line.count('\\"')

            # Skip comment lines
            stripped = line.strip()
            if stripped.startswith('//'):
                continue

            # Rough check for odd quote counts
            if single_quotes % 2 != 0 and '"' not in line:
                # Might be a lifetime, check context
                if not any(c.isalnum() for c in line.split("'")[0] if c):
                    return self.SyntaxCheckResult(
                        success=False,
                        error_message=f"Potential unmatched quote at line {i}"
                    )

        return self.SyntaxCheckResult(success=True)

    def _fix_syntax_errors(self, code: str) -> Optional[str]:
        """Attempt to fix common syntax errors."""
        original_code = code

        # Fix unbalanced braces
        open_braces = code.count('{')
        close_braces = code.count('}')

        if open_braces > close_braces:
            # Add missing closing braces at the end of blocks
            lines = code.split('\n')
            fixed_lines = []
            brace_diff = open_braces - close_braces

            for i, line in enumerate(lines):
                fixed_lines.append(line)

                # Check if we're at the end of a file-level item
                if brace_diff > 0 and i < len(lines) - 1:
                    next_line = lines[i + 1].strip()
                    current_indent = len(line) - len(line.lstrip())
                    next_indent = len(lines[i + 1]) - len(lines[i + 1].lstrip())

                    # If next line is at file level or start of new item, close blocks
                    if next_indent == 0 and next_line and not next_line.startswith('}'):
                        # Add closing braces
                        for _ in range(min(brace_diff, 2)):  # Close at most 2 at a time
                            fixed_lines.append('}')
                            brace_diff -= 1

            # Add any remaining braces at the end
            while brace_diff > 0:
                fixed_lines.append('}')
                brace_diff -= 1

            code = '\n'.join(fixed_lines)

        elif close_braces > open_braces:
            # Remove extra closing braces
            diff = close_braces - open_braces
            # Remove from the end
            for _ in range(diff):
                last_brace = code.rfind('}')
                if last_brace != -1:
                    # Check if it's a standalone brace
                    before = code[last_brace-1:last_brace] if last_brace > 0 else ''
                    after = code[last_brace+1:last_brace+2] if last_brace < len(code)-1 else ''
                    if before == '\n' or after == '\n' or after == '':
                        code = code[:last_brace] + code[last_brace+1:]

        # Validate the fix worked
        final_check = self._validate_syntax(code)
        if final_check.success:
            return code

        # If we couldn't fix it, return None
        return None

    # ============ Helper Methods ============

    def _fragment_matches(self, code_text: str, fragment: str) -> bool:
        """Check if fragment matches code (with some tolerance)."""
        # Normalize whitespace
        norm_code = ' '.join(code_text.split())
        norm_fragment = ' '.join(fragment.split())

        # Check for substantial overlap
        if len(norm_fragment) > 30:
            return norm_fragment[:30] in norm_code
        return norm_fragment in norm_code

    def _remove_if_statement(self, code: str, if_node: Node) -> TransformResult:
        """Remove an if statement, keeping only the body or alternative."""
        # Find the consequence (then block)
        consequence = None
        for child in if_node.children:
            if child.type == "block":
                consequence = child
                break

        if consequence:
            # Extract body content (without braces)
            body_text = self._get_node_text(code, consequence)
            inner_body = body_text[1:-1].strip()  # Remove { and }

            # Replace entire if with body
            modified = self._replace_node(code, if_node, inner_body)
            return TransformResult(success=True, modified_code=modified)

        return TransformResult(
            success=False,
            modified_code=None,
            error_message="Could not extract if body"
        )

    def _text_remove_guard(self, code: str, fragment: str, start_line: int) -> TransformResult:
        """Text-based removal of guard condition."""
        lines = code.split('\n')

        # Find the complete if block
        i = start_line
        if_indent = len(lines[i]) - len(lines[i].lstrip())

        # Collect lines of the if statement
        block_lines = []
        while i < len(lines):
            line = lines[i]
            indent = len(line) - len(line.lstrip())

            if i > start_line and line.strip() and indent <= if_indent:
                break

            block_lines.append((i, line))
            i += 1

        # For now, comment out the entire block
        for idx, _ in block_lines:
            lines[idx] = '//' + lines[idx]

        return TransformResult(success=True, modified_code='\n'.join(lines))

    def _fallback_text_transform(
        self,
        code: str,
        mutation: MutationInstruction,
        old_text: str = None,
        new_text: str = None,
    ) -> TransformResult:
        """Fallback text-based transformation with syntax preservation."""
        fragment = mutation.original_fragment.strip()

        if old_text is None:
            old_text = fragment
        if new_text is None:
            details = mutation.mutation_details
            new_text = details.get("new_fragment")
            if not new_text:
                # Try to construct transformation based on type
                new_text = self._construct_transformation(mutation)
                if not new_text:
                    return TransformResult(
                        success=False,
                        modified_code=None,
                        error_message="No transformation text provided"
                    )

        # Validate the transformation preserves brace balance
        old_open = old_text.count('{')
        old_close = old_text.count('}')
        new_open = new_text.count('{')
        new_close = new_text.count('}')

        if old_open != new_open or old_close != new_close:
            # Brace imbalance - try to fix by adding/removing braces
            if new_open < old_open:
                new_text += '}' * (old_open - new_open)
            if new_close < old_close:
                new_text = '{' * (old_close - new_close) + new_text

        # Try exact replacement first
        if old_text in code:
            modified = code.replace(old_text, new_text, 1)
            return TransformResult(success=True, modified_code=modified)

        # Try fuzzy match
        lines = code.split('\n')
        fragment_lines = old_text.split('\n')

        for i in range(len(lines) - len(fragment_lines) + 1):
            match = True
            for j, frag_line in enumerate(fragment_lines):
                if frag_line.strip() not in lines[i + j]:
                    match = False
                    break

            if match:
                # Replace
                new_lines = new_text.split('\n')
                modified_lines = lines[:i] + new_lines + lines[i + len(fragment_lines):]
                return TransformResult(
                    success=True,
                    modified_code='\n'.join(modified_lines)
                )

        return TransformResult(
            success=False,
            modified_code=None,
            error_message=f"Could not find code fragment: {old_text[:50]}..."
        )

    def _construct_transformation(self, mutation: MutationInstruction) -> Optional[str]:
        """Try to construct transformation from mutation details."""
        fragment = mutation.original_fragment.strip()
        details = mutation.mutation_details
        mutation_type = mutation.type

        if mutation_type == "flip_comparison":
            old_op = details.get("operator", "")
            new_op = details.get("new_operator", "")
            if old_op and new_op and old_op in fragment:
                return fragment.replace(old_op, new_op, 1)

        elif mutation_type == "change_operator":
            old_op = details.get("operator", "")
            new_op = details.get("new_operator", "")
            if old_op and new_op and old_op in fragment:
                return fragment.replace(old_op, new_op, 1)

        elif mutation_type == "invert_boolean":
            if 'return true' in fragment:
                return fragment.replace('return true', 'return false')
            elif 'return false' in fragment:
                return fragment.replace('return false', 'return true')
            elif 'return ' in fragment:
                # Wrap with !
                return fragment.replace('return ', 'return !')

        elif mutation_type == "remove_error_handling":
            return fragment.replace('?', '')

        elif mutation_type == "logic_swap":
            old_op = details.get("operator", "&&")
            new_op = details.get("new_operator", "||")
            if old_op in fragment:
                return fragment.replace(old_op, new_op, 1)

        elif mutation_type == "off_by_one":
            direction = details.get("direction", "+1")
            if direction == "+1":
                if '<' in fragment and '<=' not in fragment:
                    return fragment.replace('<', '<=', 1)
                if '- 1' in fragment:
                    return fragment.replace('- 1', '', 1)
            else:  # -1
                if '<=' in fragment:
                    return fragment.replace('<=', '<', 1)

        elif mutation_type == "remove_guard":
            # Comment out the guard
            lines = fragment.split('\n')
            commented = ['//' + line for line in lines]
            return '\n'.join(commented)

        elif mutation_type == "missing_return":
            # Comment out the return
            if 'return ' in fragment:
                return fragment.replace('return ', '//return ')

        return None

        # Try exact replacement first
        if old_text in code:
            modified = code.replace(old_text, new_text, 1)
            return TransformResult(success=True, modified_code=modified)

        # Try fuzzy match
        lines = code.split('\n')
        fragment_lines = old_text.split('\n')

        for i in range(len(lines) - len(fragment_lines) + 1):
            match = True
            for j, frag_line in enumerate(fragment_lines):
                if frag_line.strip() not in lines[i + j]:
                    match = False
                    break

            if match:
                # Replace
                new_lines = new_text.split('\n')
                modified_lines = lines[:i] + new_lines + lines[i + len(fragment_lines):]
                return TransformResult(
                    success=True,
                    modified_code='\n'.join(modified_lines)
                )

        return TransformResult(
            success=False,
            modified_code=None,
            error_message=f"Could not find code fragment: {old_text[:50]}..."
        )
