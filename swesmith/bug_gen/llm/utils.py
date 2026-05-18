import ast
import re


PROMPT_KEYS = ["system", "demonstration", "instance"]


def extract_code_block(text: str) -> str:
    pattern = r"```(?:\w+)?\n(.*?)```"
    match = re.search(pattern, text, re.DOTALL)
    return match.group(1).strip() if match else ""


def validate_python_syntax(code: str) -> tuple[bool, str]:
    """Validate that code is syntactically valid Python.

    Returns:
        tuple of (is_valid, error_message)
    """
    if not code or not code.strip():
        return False, "Empty code"

    try:
        ast.parse(code)
        return True, ""
    except SyntaxError as e:
        return False, f"SyntaxError: {e.msg} at line {e.lineno}"
    except Exception as e:
        return False, f"Parse error: {str(e)}"


def check_indentation_consistency(code: str, expected_indent: int = 4) -> tuple[bool, str]:
    """Check that code indentation is consistent.

    Args:
        code: The code to check
        expected_indent: Expected indentation size (usually 4 spaces)

    Returns:
        tuple of (is_valid, error_message)
    """
    lines = code.split('\n')
    prev_indent = 0

    for i, line in enumerate(lines, 1):
        if not line.strip():
            continue

        # Calculate indentation
        stripped = line.lstrip(' ')
        indent = len(line) - len(stripped)

        # Check if indentation is multiple of expected
        if indent > 0 and indent % expected_indent != 0:
            return False, f"Inconsistent indentation at line {i}: {indent} spaces"

        # Check decorator indentation
        if stripped.startswith('@') and not stripped.startswith('@@'):
            if indent != prev_indent:
                return False, f"Decorator at line {i} has wrong indentation"

        prev_indent = indent

    return True, ""
