"""Bug generation using structured LLM output + programmatic diff building."""

import os
import re
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, List, Tuple

from litellm import completion


@dataclass
class GeneratedBug:
    """Generated bug with patch."""
    patch: str
    explanation: str
    affected_files: List[str]
    strategy: str


class PatchGenerator:
    """Generate bugs using structured LLM output + programmatic diff construction."""

    def __init__(self, model: str = "private-large"):
        self.model = model
        self.api_key = os.getenv("LITE_LLM_API_KEY", "")
        self.api_base = os.getenv("LITE_LLM_URL", "")

    def generate_bug(
        self,
        file_path: str,
        original_code: str,
        target_function: str,
        max_retries: int = 3,
        context_str: str = "",
    ) -> Optional[GeneratedBug]:
        """Generate a bug patch using structured LLM output."""

        lines = original_code.split('\n')
        numbered_lines = []
        func_start_line = None
        func_end_line = None

        for i, line in enumerate(lines, 1):
            numbered_lines.append(f"{i:4d}: {line}")
            if func_start_line is None and re.search(
                rf'\bfn\s+{re.escape(target_function)}\b', line
            ):
                func_start_line = i
            if func_start_line and func_end_line is None:
                if line.strip() == '}' and i > func_start_line + 5:
                    func_end_line = i
                    break

        if func_start_line is None:
            print(f"  Function {target_function} not found")
            return None

        # Function context: 150-300 lines with overlap around target
        context_start = max(0, func_start_line - 75)
        context_end = min(
            len(lines),
            (func_end_line + 75) if func_end_line else (func_start_line + 200)
        )
        context_lines = numbered_lines[context_start:context_end]

        # Find a specific line to modify
        target_lines = []
        for i, line in enumerate(
            lines[context_start:context_end], context_start + 1
        ):
            if any(
                op in line
                for op in ['==', '!=', '<', '>', '<=', '>=', '&&', '||', '.await', '?']
            ):
                target_lines.append((i, line))

        target_hint = ""
        if target_lines:
            line_num, line_content = target_lines[len(target_lines) // 2]
            target_hint = (
                f"SUGGESTED TARGET: Line {line_num}: {line_content[:60]}"
            )

        context_section = ""
        if context_str:
            context_section = f"""
REPOSITORY CONTEXT (callers, callees, related logic):
{context_str[:8000]}
"""

        prompt = f"""You are modifying Rust code to introduce ONE realistic behavioral regression.

FILE: {file_path}
FUNCTION: {target_function}

CODE (with line numbers):
```
{chr(10).join(context_lines)}
```

{target_hint}
{context_section}

INSTRUCTIONS:
1. Pick ONE non-trivial change inside the target function (not a constructor/getter).
2. Prefer realistic bugs: wrong validation order, missing guard, stale fallback,
   incorrect retry/async handling, partial error propagation, wrong threshold.
3. Avoid trivial-only operator flips unless embedded in real business logic.
4. Do NOT change function signatures, imports, or types — code must compile.
5. Output ONLY the structured format below. No prose. No markdown.

OUTPUT FORMAT (exactly):
LINE: <line_number>
OLD: <exact original line text>
NEW: <modified line text>
BUG: <one sentence describing the behavioral regression>"""

        for attempt in range(max_retries):
            try:
                response = completion(
                    model=f"openai/{self.model}",
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.1 + (attempt * 0.05),
                    max_tokens=1000,
                    api_key=self.api_key,
                    base_url=self.api_base,
                )

                content = response.choices[0].message.content

                # Parse structured output
                parsed = self._parse_structured_output(content)
                if not parsed:
                    self._save_debug_response(
                        file_path, target_function, attempt, content
                    )
                    print(f"  Attempt {attempt + 1}: No structured output found")
                    continue

                line_num, old_line, new_line, bug_desc = parsed

                # Build diff programmatically
                patch = self._build_diff(
                    file_path, lines, line_num, old_line, new_line
                )
                if not patch:
                    print(f"  Attempt {attempt + 1}: Could not build diff")
                    continue

                # Validate with git apply
                if not self._validate_patch(patch, original_code, file_path):
                    print(f"  Attempt {attempt + 1}: Patch validation failed")
                    continue

                return GeneratedBug(
                    patch=patch,
                    explanation=bug_desc,
                    affected_files=[file_path],
                    strategy=bug_desc,
                )

            except Exception as e:
                print(f"  Attempt {attempt + 1} failed: {e}")
                continue

        return None

    def _parse_structured_output(self, content: str) -> Optional[Tuple[int, str, str, str]]:
        """Parse LINE/OLD/NEW/BUG format from LLM response."""
        if not content:
            return None

        line_match = re.search(r'LINE:\s*(\d+)', content, re.IGNORECASE)
        if not line_match:
            return None

        line_num = int(line_match.group(1))

        # Extract OLD text (up to NEW or BUG or end)
        old_match = re.search(
            r'OLD:\s*(.*?)(?=NEW:|BUG:|$)',
            content, re.DOTALL | re.IGNORECASE
        )
        old_line = old_match.group(1).strip() if old_match else ""

        # Extract NEW text (up to BUG or end)
        new_match = re.search(
            r'NEW:\s*(.*?)(?=BUG:|$)',
            content, re.DOTALL | re.IGNORECASE
        )
        new_line = new_match.group(1).strip() if new_match else ""

        # Extract BUG description
        bug_match = re.search(
            r'BUG:\s*(.+?)(?:\n|$)',
            content, re.IGNORECASE
        )
        bug_desc = bug_match.group(1).strip()[:200] if bug_match else "Bug introduced"

        if not old_line or not new_line:
            return None

        return (line_num, old_line, new_line, bug_desc)

    def _build_diff(
        self,
        file_path: str,
        lines: List[str],
        line_num: int,
        old_line: str,
        new_line: str
    ) -> Optional[str]:
        """Build a unified diff from structured output."""
        idx = line_num - 1  # 0-indexed

        if idx < 0 or idx >= len(lines):
            print(f"    Line {line_num} out of range (file has {len(lines)} lines)")
            return None

        source_line = lines[idx]

        # Verify old line roughly matches source (stripped comparison)
        if old_line.strip() != source_line.strip():
            # Try to find matching line within +/- 3 positions
            found_idx = None
            for offset in range(-3, 4):
                check_idx = idx + offset
                if 0 <= check_idx < len(lines):
                    if lines[check_idx].strip() == old_line.strip():
                        found_idx = check_idx
                        break
            if found_idx is not None:
                idx = found_idx
                source_line = lines[idx]
                print(f"    Fuzzy-matched old line to actual line {idx + 1}")
            else:
                # Use source line at reported position anyway
                print(
                    f"    Warning: old line doesn't match source at {line_num}, "
                    f"using source line"
                )

        # Context lines (5 before, 5 after)
        ctx_before = 5
        ctx_after = 5

        start = max(0, idx - ctx_before)
        end = min(len(lines), idx + ctx_after + 1)

        context_before = lines[start:idx]
        context_after = lines[idx + 1:end]

        old_start = start + 1  # 1-indexed for diff header
        old_count = len(context_before) + 1 + len(context_after)
        new_count = len(context_before) + 1 + len(context_after)

        diff_lines = [
            f"diff --git a/{file_path} b/{file_path}",
            f"--- a/{file_path}",
            f"+++ b/{file_path}",
            f"@@ -{old_start},{old_count} +{old_start},{new_count} @@",
        ]

        for line in context_before:
            diff_lines.append(' ' + line)
        diff_lines.append('-' + source_line)
        diff_lines.append('+' + new_line)
        for line in context_after:
            diff_lines.append(' ' + line)

        return '\n'.join(diff_lines) + '\n'

    def _save_debug_response(
        self, file_path: str, func: str, attempt: int, content: str
    ):
        """Save LLM response to /tmp for inspection."""
        safe_fp = file_path.replace('/', '_')
        debug_file = f"/tmp/llm_debug_{safe_fp}_{func}_{attempt}_{int(time.time())}.txt"
        try:
            with open(debug_file, 'w') as f:
                f.write(content)
        except Exception:
            pass

    def _validate_patch(self, patch: str, original_code: str, file_path: str) -> bool:
        """Validate patch applies cleanly with git apply."""
        with tempfile.TemporaryDirectory() as tmpdir:
            subprocess.run(['git', 'init'], cwd=tmpdir, capture_output=True)
            subprocess.run(
                ['git', 'config', 'user.email', 'test@test.com'],
                cwd=tmpdir, capture_output=True
            )
            subprocess.run(
                ['git', 'config', 'user.name', 'Test'],
                cwd=tmpdir, capture_output=True
            )

            full_path = Path(tmpdir) / file_path
            full_path.parent.mkdir(parents=True, exist_ok=True)
            full_path.write_text(original_code)

            subprocess.run(['git', 'add', '.'], cwd=tmpdir, capture_output=True)
            subprocess.run(['git', 'commit', '-m', 'original'], cwd=tmpdir, capture_output=True)

            result = subprocess.run(
                ['git', 'apply', '--check', '-'],
                cwd=tmpdir,
                input=patch,
                capture_output=True,
                text=True,
            )

            if result.returncode != 0:
                error = result.stderr[:150] if result.stderr else "Unknown error"
                print(f"    git apply --check failed: {error}")
                return False

            result = subprocess.run(
                ['git', 'apply', '-'],
                cwd=tmpdir,
                input=patch,
                capture_output=True,
                text=True,
            )

            if result.returncode != 0:
                error = result.stderr[:150] if result.stderr else "Unknown error"
                print(f"    git apply failed: {error}")
                return False

            return True

    def _extract_files(self, patch: str) -> List[str]:
        """Extract affected files from patch."""
        files = []
        for match in re.finditer(r'\+\+\+ b/(\S+)', patch):
            files.append(match.group(1))
        return files

    def apply_patch(self, patch: str, repo_path: str) -> bool:
        """Apply patch to repository."""
        result = subprocess.run(
            ['git', 'apply', '-'],
            cwd=repo_path,
            input=patch,
            capture_output=True,
            text=True,
        )
        return result.returncode == 0
