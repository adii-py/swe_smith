"""Bug generation using LLM for actual code transformation."""

import os
import re
from dataclasses import dataclass
from typing import Optional, Dict, List

from litellm import completion


@dataclass
class GeneratedBug:
    """Generated bug with transformed code."""
    strategy: str
    original_files: Dict[str, str]
    modified_files: Dict[str, str]
    explanation: str
    affected_files: List[str]
    validation_status: str = "pending"


class LLMTransformGenerator:
    """Generate bugs using LLM for actual code transformation."""

    SYSTEM_PROMPT = """You are an expert Rust developer creating subtle bugs for testing purposes.

YOUR TASK: Transform provided code to introduce ONE subtle bug that:
1. COMPILES without errors
2. Causes incorrect behavior in specific scenarios
3. Looks reasonable during code review

RULES:
- Output ONLY the transformed function(s), nothing else
- Keep all types, signatures, and imports unchanged
- Make minimal changes - only what's needed for the bug
- Ensure the code is syntactically valid Rust

BUG PATTERNS (choose one):
- Flip a comparison (== to !=, > to <)
- Remove a boundary check (if condition)
- Change an operator (+ to -, * to /)
- Swap && with || in conditions
- Remove error handling (? operator)
- Off-by-one in loop bounds (< to <=)

OUTPUT FORMAT:
```rust
// Transformed code here
pub fn function_name(...) -> ReturnType {
    // buggy implementation
}
```

No explanations, no markdown outside the code block, just the transformed code."""

    def __init__(self, model: str = "private-large"):
        self.model = model
        self.api_key = os.getenv("LITE_LLM_API_KEY", "")
        self.api_base = os.getenv("LITE_LLM_URL", "")

    def generate_bug(
        self,
        context_str: str,
        file_contents: Dict[str, str],
        target_function: str,
        max_retries: int = 3
    ) -> Optional[GeneratedBug]:
        """Generate a bug using LLM transformation."""

        prompt = f"""Transform the target function to introduce ONE subtle bug:

{context_str}

INSTRUCTIONS:
1. Modify ONLY the target function "{target_function}"
2. Introduce ONE of these bugs:
   - Flip a comparison operator
   - Remove an important check/guard
   - Change an arithmetic operator
   - Modify error handling
3. Ensure the code COMPILES
4. Output ONLY the transformed function in ```rust block

Explain briefly what bug you introduced and why it's subtle."""

        for attempt in range(max_retries):
            try:
                response = completion(
                    model=f"openai/{self.model}",
                    messages=[
                        {"role": "system", "content": self.SYSTEM_PROMPT},
                        {"role": "user", "content": prompt}
                    ],
                    temperature=0.4 + (attempt * 0.1),
                    max_tokens=4000,
                    api_key=self.api_key,
                    base_url=self.api_base,
                )

                content = response.choices[0].message.content

                # Extract transformed code
                transformed_code = self._extract_code(content)
                explanation = self._extract_explanation(content)

                if not transformed_code:
                    print(f"  Attempt {attempt + 1}: No code extracted")
                    continue

                # Apply transformation to the file
                modified_files = self._apply_transformation(
                    file_contents,
                    target_function,
                    transformed_code
                )

                if not modified_files:
                    print(f"  Attempt {attempt + 1}: Failed to apply transformation")
                    continue

                return GeneratedBug(
                    strategy=explanation or "Subtle logic bug",
                    original_files=dict(file_contents),
                    modified_files=modified_files,
                    explanation=explanation or "Bug introduced in code",
                    affected_files=list(modified_files.keys()),
                )

            except Exception as e:
                print(f"  Generation attempt {attempt + 1} failed: {e}")
                continue

        return None

    def _extract_code(self, content: str) -> Optional[str]:
        """Extract code from LLM response."""
        # Pattern 1: Markdown code block with rust
        match = re.search(r'```rust\s*\n(.*?)```', content, re.DOTALL)
        if match:
            return match.group(1).strip()

        # Pattern 2: Generic code block
        match = re.search(r'```\s*\n(.*?)```', content, re.DOTALL)
        if match:
            return match.group(1).strip()

        # Pattern 3: Look for function definition
        match = re.search(r'(pub\s+(?:async\s+)?fn\s+\w+\s*\(.*?\)\s*(?:->\s*\w+)?\s*\{.*?\})', content, re.DOTALL)
        if match:
            return match.group(1).strip()

        return None

    def _extract_explanation(self, content: str) -> Optional[str]:
        """Extract explanation from LLM response."""
        # Look for explanation after code block
        parts = content.split('```')
        if len(parts) >= 3:
            after_code = parts[-1].strip()
            if after_code:
                # Take first sentence or line
                lines = [l.strip() for l in after_code.split('\n') if l.strip()]
                if lines:
                    return lines[0][:200]

        return None

    def _apply_transformation(
        self,
        file_contents: Dict[str, str],
        target_function: str,
        transformed_code: str
    ) -> Optional[Dict[str, str]]:
        """Apply LLM-transformed code to the appropriate file."""
        modified_files = dict(file_contents)

        # ONLY modify the target file, not related files
        target_file = None
        for file_path, content in file_contents.items():
            if target_function in content:
                target_file = file_path
                break

        if not target_file:
            print(f"  Could not find function {target_function}")
            return None

        original_content = file_contents[target_file]

        # Extract function name from transformed code
        func_match = re.search(r'fn\s+(\w+)\s*\(', transformed_code)
        if not func_match:
            print("  Could not extract function name from transformed code")
            return None

        func_name = func_match.group(1)

        # Find function bounds with brace counting
        func_start = re.search(rf'(pub\s+(?:async\s+)?fn\s+{re.escape(func_name)})', original_content)
        if not func_start:
            print(f"  Could not find function {func_name}")
            return None

        start_pos = func_start.start()

        # Find function body start
        body_start = original_content.find('{', start_pos)
        if body_start == -1:
            print("  Could not find function body start")
            return None

        # Count braces to find end
        brace_count = 0
        end_pos = body_start
        for i, char in enumerate(original_content[body_start:]):
            if char == '{':
                brace_count += 1
            elif char == '}':
                brace_count -= 1
                if brace_count == 0:
                    end_pos = body_start + i + 1
                    break

        # Replace only the function body, keeping signature
        original_func = original_content[start_pos:end_pos]

        # Validate transformed code has balanced braces
        if transformed_code.count('{') != transformed_code.count('}'):
            print("  Transformed code has unbalanced braces, attempting to fix")
            # Simple fix: add/remove braces at end
            open_count = transformed_code.count('{')
            close_count = transformed_code.count('}')
            if open_count > close_count:
                transformed_code += '}' * (open_count - close_count)
            elif close_count > open_count:
                transformed_code = transformed_code.rstrip('}')

        # Replace
        new_content = original_content[:start_pos] + transformed_code + original_content[end_pos:]

        # Validate final brace balance
        if new_content.count('{') != new_content.count('}'):
            print(f"  Brace imbalance: {new_content.count('{')} open, {new_content.count('}')} close")
            return None

        modified_files[target_file] = new_content
        return modified_files
