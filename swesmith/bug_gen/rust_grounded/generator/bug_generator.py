"""Grounded bug generation with strict constraints."""

import os
import re
from dataclasses import dataclass
from typing import Optional, Tuple

from litellm import completion


@dataclass
class GeneratedBug:
    """Generated bug with metadata."""
    patch: str
    explanation: str
    affected_files: list
    validation_status: str = "pending"


class BugGenerator:
    """Generate grounded, compilable bugs."""

    SYSTEM_PROMPT = """You are an expert Rust developer introducing subtle bugs.

CRITICAL CONSTRAINTS:
1. ONLY modify files listed in ALLOWED FILES
2. Do NOT create new files
3. Do NOT modify imports or add use statements
4. Do NOT change function signatures
5. All changes must compile without errors
6. Generate COMPLETE, VALID unified diff patches

BUG REQUIREMENTS:
- Subtle logic errors (off-by-one, flipped conditionals, missing checks)
- State management bugs
- Error handling bugs
- Control flow issues

PATCH FORMAT - MUST BE VALID GIT DIFF:
```diff
diff --git a/<file> b/<file>
--- a/<file>
+++ b/<file>
@@ -<line>,<count> +<line>,<count> @@
 <context lines>
-<removed line>
+<added line>
 <context lines>
```

IMPORTANT RULES:
- Include at least 3 lines of context before and after changes
- Ensure the hunk has correct line counts
- The patch must end with a newline
- Only change what is necessary for the bug
- Use EXACT line numbers from the context provided"""

    INSTANCE_TEMPLATE = """{context}

GENERATE A BUG:

Introduce ONE subtle bug in the target function:
- Change a conditional (== to !=, > to >=)
- Remove an important check
- Flip a boolean condition
- Use wrong variable in comparison
- Remove error propagation (?)

The bug should:
1. Be hard to spot in code review
2. Compile without errors
3. Cause incorrect behavior in edge cases

Output EXACTLY:
```diff
<patch content>
```

Explanation: <1-2 sentence description of the bug>"""

    def __init__(self, model: str = "private-large"):
        self.model = model
        self.api_key = os.getenv("LITE_LLM_API_KEY", "")
        self.api_base = os.getenv("LITE_LLM_URL", "")

    def generate_bug(
        self,
        context_str: str,
        max_retries: int = 3
    ) -> Optional[GeneratedBug]:
        """Generate a bug with retry logic."""
        prompt = self.INSTANCE_TEMPLATE.format(context=context_str)

        for attempt in range(max_retries):
            try:
                response = completion(
                    model=f"openai/{self.model}",
                    messages=[
                        {"role": "system", "content": self.SYSTEM_PROMPT},
                        {"role": "user", "content": prompt}
                    ],
                    temperature=0.7 + (attempt * 0.1),  # Increase temp on retry
                    max_tokens=4000,
                    api_key=self.api_key,
                    base_url=self.api_base,
                )

                content = response.choices[0].message.content

                # Extract patch
                patch = self._extract_patch(content)
                if not patch:
                    continue

                # Extract explanation
                explanation = self._extract_explanation(content)

                # Get affected files
                affected_files = self._extract_affected_files(patch)

                return GeneratedBug(
                    patch=patch,
                    explanation=explanation,
                    affected_files=affected_files,
                )

            except Exception as e:
                print(f"Generation attempt {attempt + 1} failed: {e}")
                continue

        return None

    def _extract_patch(self, content: str) -> Optional[str]:
        """Extract unified diff patch from response."""
        patch = None

        # Pattern 1: Markdown code block with diff
        match = re.search(r'```diff\s*\n(.*?)```', content, re.DOTALL)
        if match:
            patch = match.group(1).strip()

        # Pattern 2: Raw diff
        if not patch:
            match = re.search(r'(diff --git.*)', content, re.DOTALL)
            if match:
                patch = match.group(1).strip()
                # Cut at explanation
                expl_idx = patch.find('\n\nExplanation:')
                if expl_idx != -1:
                    patch = patch[:expl_idx].strip()

        if not patch:
            return None

        # Ensure patch ends with newline
        if not patch.endswith('\n'):
            patch += '\n'

        # Remove any trailing backticks or markdown
        patch = patch.rstrip('`').strip()

        # Ensure patch starts with diff --git
        if not patch.startswith('diff --git'):
            return None

        return patch

    def _extract_explanation(self, content: str) -> str:
        """Extract bug explanation."""
        match = re.search(r'Explanation:\s*(.+?)(?:\n\n|$)', content, re.DOTALL)
        if match:
            return match.group(1).strip()
        return "Bug introduced in the code"

    def _extract_affected_files(self, patch: str) -> list:
        """Extract list of affected files from patch."""
        files = re.findall(r'diff --git a/(\S+) b/', patch)
        return files

    def generate_test_patch(
        self,
        bug_patch: str,
        context_str: str,
    ) -> Optional[str]:
        """Generate a test that catches the bug."""
        test_prompt = f"""Given this bug patch:

```diff
{bug_patch}
```

And this context:
{context_str}

Generate a REGRESSION TEST that:
1. Tests the buggy behavior (would fail before fix)
2. Uses the existing test patterns in the codebase
3. Is minimal and focused

Output ONLY the test code in ```rust blocks."""

        try:
            response = completion(
                model=f"openai/{self.model}",
                messages=[
                    {"role": "user", "content": test_prompt}
                ],
                temperature=0.3,
                max_tokens=1500,
                api_key=self.api_key,
                base_url=self.api_base,
            )

            content = response.choices[0].message.content

            # Extract test code
            match = re.search(r'```rust\s*\n(.*?)```', content, re.DOTALL)
            if match:
                return match.group(1).strip()

            return None

        except Exception as e:
            print(f"Test generation failed: {e}")
            return None
