"""
Purpose: Given a bug patch, generate a GitHub-style issue that describes the bug.

Uses AST diff extraction + enhanced LLM prompting patterns similar to
generate_tasks_unified.py and extract_diffs.py

python swesmith/issue_gen/generate.py \
    --dataset logs/experiments/*.json \
    --config configs/issue_gen/*.yaml \
    --model anthropic/claude-3-7-sonnet-20250219 \
    --workers 2 \
    --redo_existing  # Optional: regenerate existing issue texts
    --use_structured_reasoning  # Enable structured reasoning fields
"""

import argparse
import jinja2
import json
import litellm
import logging
import os
import random
import shutil
import re
import yaml

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datasets import load_dataset
from dotenv import load_dotenv
from litellm import completion, completion_cost
from litellm.utils import get_token_count
from pathlib import Path
from tqdm import tqdm
from tqdm.contrib.logging import logging_redirect_tqdm
from swebench.harness.constants import (
    FAIL_TO_PASS,
    KEY_INSTANCE_ID,
    LOG_TEST_OUTPUT,
)
from swesmith.constants import (
    KEY_PATCH,
    HF_DATASET,
    LOG_DIR_ISSUE_GEN,
    LOG_DIR_RUN_VALIDATION,
    TEST_OUTPUT_END,
    TEST_OUTPUT_START,
)
from swesmith.harness.utils import (
    matches_instance_filter,
    run_patch_in_container,
)
from swesmith.issue_gen.ast_enricher import enrich_patch, SemanticDiffReport
from swesmith.issue_gen.patch_parser import parse_patch, FileDiff, Hunk
from swesmith.issue_gen.utils import get_test_function
from swesmith.profiles import registry
from typing import Any, Literal
from tenacity import (
    retry,
    retry_if_not_exception_type,
    stop_after_attempt,
    wait_exponential,
    before_sleep_log,
)
from pydantic import BaseModel

try:
    from portkey_ai import Portkey
except ImportError:
    Portkey = None

logging.getLogger("LiteLLM").setLevel(logging.WARNING)
litellm.drop_params = True
litellm.suppress_debug_info = True


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


class PortkeyModelConfig(BaseModel):
    model_name: str
    model_kwargs: dict[str, Any] = {}
    provider: str = ""
    litellm_model_name_override: str = ""
    cost_tracking: Literal["default", "ignore_errors"] = "default"


# =============================================================================
# Structured Reasoning Data Classes (from generate_tasks_unified.py patterns)
# =============================================================================

@dataclass
class SemanticDiffItem:
    """A single semantic diff item similar to extract_diffs.py format."""
    id: str  # Complete AST ID: path/to/file.rs::Type::method
    file: str
    kind: str  # function, struct, enum, trait, impl, etc.
    status: str  # added, modified, removed
    parent: str | None = None
    before_code: str | None = None
    after_code: str | None = None
    before_context: str = ""
    after_context: str = ""
    imports_before: list[str] = field(default_factory=list)
    imports_after: list[str] = field(default_factory=list)


@dataclass
class ReasoningUpdateEntry:
    """Reasoning for a single function/struct modification."""
    operation: str  # MODIFY, ADD, DELETE
    current_state: str  # What the code currently does/not do
    required_change: str  # WHAT specific modification is needed
    interaction: str  # HOW this connects to OTHER selected items
    failure_mode: str  # What SPECIFIC behavior breaks if omitted


@dataclass
class StructuredReasoning:
    """Enhanced reasoning structure similar to generate_tasks_unified.py output."""
    # Core task description
    task: str

    # Selection reasoning: WHY these specific functions/locations together
    reasoning_selection: str

    # Selected items: Complete AST IDs
    selected_functions: list[str]

    # Per-item reasoning: Detailed reasoning for EACH selected item
    reasoning_update: dict[str, str]  # AST ID -> reasoning string

    # Old format compatibility fields
    locations: list[dict[str, Any]] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "task": self.task,
            "reasoning_selection": self.reasoning_selection,
            "selected_functions": self.selected_functions,
            "reasoning_update": self.reasoning_update,
            "locations": self.locations,
            "summary": self.summary,
        }


class StructuredReasoningExtractor:
    """Extract structured reasoning from patches using AST analysis.

    Similar to DiffExtractor in extract_diffs.py but adapted for patch strings.
    """

    # Language-agnostic patterns for identifying code constructs
    FUNCTION_PATTERNS = {
        "python": re.compile(
            r"^(\s*)(?:async\s+)?def\s+(\w+)\s*\([^)]*\)\s*(?:->\s*[^:]+)?:",
            re.MULTILINE,
        ),
        "rust": re.compile(
            r"^(\s*)(?:pub\s+)?(?:async\s+)?(?:unsafe\s+)?fn\s+(\w+)",
            re.MULTILINE,
        ),
        "general": re.compile(
            r"^(\s*)(?:def|fn|func|function)\s+(\w+)",
            re.MULTILINE,
        ),
    }

    CLASS_PATTERNS = {
        "python": re.compile(r"^(\s*)class\s+(\w+)(?:\([^)]*\))?:", re.MULTILINE),
        "rust": re.compile(r"^(\s*)(?:pub\s+)?(?:struct|enum|trait|impl)\s+(?:<[^>]*>\s*)?(\w+)", re.MULTILINE),
        "general": re.compile(r"^(\s*)(?:class|struct|enum|trait|impl|interface)\s+(\w+)", re.MULTILINE),
    }

    def __init__(self):
        self.logger = logging.getLogger(__name__)

    def detect_language(self, file_path: str) -> str:
        """Detect programming language from file extension."""
        ext = file_path.rsplit(".", 1)[-1].lower() if "." in file_path else ""
        lang_map = {
            "py": "python",
            "rs": "rust",
            "java": "java",
            "cpp": "cpp",
            "c": "c",
            "js": "javascript",
            "ts": "typescript",
            "go": "go",
            "rb": "ruby",
            "php": "php",
        }
        return lang_map.get(ext, "general")

    def extract_ast_id(self, file_path: str, item_name: str, kind: str, parent: str | None = None) -> str:
        """Build complete AST ID similar to extract_diffs.py format.

        Format: path/to/file.ext::Parent::kind::item_name
        Examples:
            - src/main.rs::function::main
            - src/models.rs::User::struct::User
            - src/handlers.rs::ApiHandler::method::process_request
        """
        if parent and kind in ("method", "trait_method"):
            return f"{file_path}::{parent}::{kind}::{item_name}"
        elif parent:
            return f"{file_path}::{parent}::{kind}::{item_name}"
        else:
            return f"{file_path}::{kind}::{item_name}"

    def extract_items_from_code(self, code: str, file_path: str, language: str) -> list[dict[str, Any]]:
        """Extract function/class/item definitions from code snippet.

        Similar to extract_rust_items in extract_diffs.py but language-agnostic.
        """
        items = []

        func_pattern = self.FUNCTION_PATTERNS.get(language, self.FUNCTION_PATTERNS["general"])
        class_pattern = self.CLASS_PATTERNS.get(language, self.CLASS_PATTERNS["general"])

        # Track current class/context for method association
        current_class = None
        lines = code.splitlines()

        for i, line in enumerate(lines):
            stripped = line.strip()

            # Check for class/struct definitions
            class_match = class_pattern.search(line)
            if class_match:
                class_name = class_match.group(2)
                indent = len(class_match.group(1))
                kind = "class" if language == "python" else "type"

                items.append({
                    "name": class_name,
                    "kind": kind,
                    "indent": indent,
                    "line": i,
                    "parent": None,
                })
                current_class = class_name

            # Check for function definitions
            func_match = func_pattern.search(line)
            if func_match:
                func_name = func_match.group(2)
                indent = len(func_match.group(1))

                # Determine if this is a method (inside a class)
                kind = "method" if (current_class and indent > 0) else "function"
                parent = current_class if kind == "method" else None

                items.append({
                    "name": func_name,
                    "kind": kind,
                    "indent": indent,
                    "line": i,
                    "parent": parent,
                })

        return items

    def build_semantic_diffs(self, patch: str) -> list[SemanticDiffItem]:
        """Build semantic diffs from patch string.

        Similar to build_semantic_diff in extract_diffs.py.
        """
        file_diffs = parse_patch(patch)
        semantic_items = []

        for file_diff in file_diffs:
            language = self.detect_language(file_diff.path)

            for hunk in file_diff.hunks:
                before = hunk.before_snippet
                after = hunk.after_snippet

                # Skip empty or whitespace-only changes
                if not before.strip() and not after.strip():
                    continue
                if before.strip() == after.strip():
                    continue

                # Extract items from before and after
                before_items = self.extract_items_from_code(before, file_diff.path, language)
                after_items = self.extract_items_from_code(after, file_diff.path, language)

                # Build maps by name
                before_map = {item["name"]: item for item in before_items}
                after_map = {item["name"]: item for item in after_items}

                all_names = set(before_map.keys()) | set(after_map.keys())

                for name in all_names:
                    before_item = before_map.get(name)
                    after_item = after_map.get(name)

                    if before_item and after_item:
                        status = "modified"
                        kind = after_item["kind"]
                        parent = after_item.get("parent")
                    elif before_item and not after_item:
                        status = "removed"
                        kind = before_item["kind"]
                        parent = before_item.get("parent")
                    else:  # not before_item and after_item
                        status = "added"
                        kind = after_item["kind"]
                        parent = after_item.get("parent")

                    # Build AST ID
                    ast_id = self.extract_ast_id(file_diff.path, name, kind, parent)

                    # Build context around changes
                    before_context = self._extract_context(before, before_item["line"] if before_item else 0)
                    after_context = self._extract_context(after, after_item["line"] if after_item else 0)

                    semantic_items.append(SemanticDiffItem(
                        id=ast_id,
                        file=file_diff.path,
                        kind=kind,
                        status=status,
                        parent=parent,
                        before_code=before if before.strip() else None,
                        after_code=after if after.strip() else None,
                        before_context=before_context,
                        after_context=after_context,
                    ))

        return semantic_items

    def _extract_context(self, code: str, center_line: int, context_lines: int = 3) -> str:
        """Extract context lines around a specific line."""
        lines = code.splitlines()
        if not lines:
            return ""

        start = max(0, center_line - context_lines)
        end = min(len(lines), center_line + context_lines + 1)

        return "\n".join(lines[start:end])

    def group_related_items(self, items: list[SemanticDiffItem], max_group_size: int = 5) -> list[list[SemanticDiffItem]]:
        """Group related semantic items by file and relationships.

        Similar to group_related_diffs in generate_tasks_unified.py.
        """
        if not items:
            return []

        # Group by file first
        file_groups: dict[str, list[SemanticDiffItem]] = {}
        for item in items:
            if item.file not in file_groups:
                file_groups[item.file] = []
            file_groups[item.file].append(item)

        # Create sub-groups if needed
        final_groups = []
        for file_path, file_items in file_groups.items():
            if len(file_items) <= max_group_size:
                final_groups.append(file_items)
            else:
                # Split into chunks
                for i in range(0, len(file_items), max_group_size):
                    chunk = file_items[i:i + max_group_size]
                    final_groups.append(chunk)

        return final_groups

    def build_prompt_for_group(
        self,
        group: list[SemanticDiffItem],
        test_output: str,
        semantic_report: SemanticDiffReport | None = None,
    ) -> str:
        """Build LLM prompt for a group of related changes.

        Similar to build_prompt in generate_tasks_unified.py.
        """
        changes_summary = []
        for item in group:
            changes_summary.append(
                f"  - {item.status.upper()}: {item.id} ({item.kind}) in {item.file}"
            )

        changes_str = "\n".join(changes_summary)

        # Build before/after code blocks
        code_blocks = []
        for item in group:
            code_blocks.append(f"\n### {item.id}")
            if item.before_code:
                code_blocks.append("--- BEFORE (buggy) ---")
                code_blocks.append(item.before_code)
            if item.after_code:
                code_blocks.append("--- AFTER (fixed) ---")
                code_blocks.append(item.after_code)

        code_str = "\n".join(code_blocks)

        prompt = f"""**Code Changes ({len(group)} items):**
{changes_str}

**Detailed Code:**
{code_str}

**Test Output:**
{test_output[:2000] if test_output else "No test output available"}

**Semantic Analysis:**
{semantic_report.to_markdown()[:3000] if semantic_report else "No semantic analysis available"}

Generate a problem statement with structured reasoning for these code changes.
"""
        return prompt


class PortkeyModel:
    def __init__(self, *, config_class: type = PortkeyModelConfig, **kwargs):
        if Portkey is None:
            raise ImportError(
                "The portkey-ai package is required to use PortkeyModel. Please install it with: pip install portkey-ai"
            )

        self.config = config_class(**kwargs)
        self.cost = 0.0
        self.n_calls = 0

        # Get API key from environment or raise error
        self._api_key = os.getenv("PORTKEY_API_KEY")
        if not self._api_key:
            raise ValueError(
                "Portkey API key is required. Set it via the "
                "PORTKEY_API_KEY environment variable."
            )

        # Get virtual key from environment
        virtual_key = os.getenv("PORTKEY_VIRTUAL_KEY")

        # Initialize Portkey client
        client_kwargs = {"api_key": self._api_key}
        if virtual_key:
            client_kwargs["virtual_key"] = virtual_key
        elif self.config.provider:
            client_kwargs["provider"] = self.config.provider

        self.client = Portkey(**client_kwargs)

    @retry(
        reraise=True,
        stop=stop_after_attempt(10),
        wait=wait_exponential(multiplier=1, min=4, max=60),
        retry=retry_if_not_exception_type((KeyboardInterrupt, TypeError, ValueError)),
        before_sleep=before_sleep_log(logger, logging.WARNING),
    )
    def _query(self, messages: list[dict[str, str]], **kwargs):
        return self.client.chat.completions.create(
            model=self.config.model_name,
            messages=messages,
            **(self.config.model_kwargs | kwargs),
        )

    def query(self, messages: list[dict[str, str]], **kwargs) -> Any:
        # Simple adapter to match what generate.py expects (return an object with choices and usage for cost)
        response = self._query(
            [{"role": msg["role"], "content": msg["content"]} for msg in messages],
            **kwargs,
        )
        return response


TEST_SRC_CODE_PROMPT = r"""
**Test Source Code:**
Use the following test source code to help you write reasonable, effective reproduction code.

{test_src_code}
"""

# =============================================================================
# Structured Reasoning System Prompt (from generate_tasks_unified.py patterns)
# =============================================================================

STRUCTURED_REASONING_SYSTEM_PROMPT = """You are generating HIGH-QUALITY PROBLEM STATEMENTS with structured reasoning for code bug datasets.

You will be given:
1. Code changes (patch/diff) showing what was modified to fix a bug
2. Test output showing failures
3. AST-level semantic analysis of the changes

Your goal: Generate a realistic GitHub-style issue with DEEP, STRUCTURED REASONING about the bug.

## OUTPUT FORMAT - JSON REQUIRED:

```json
{
  "task": "Clear, concise problem statement describing the bug from user's perspective",
  "reasoning_selection": "4-8 lines explaining WHY these specific code locations are involved in the bug",
  "selected_functions": [
    "complete::ast::id::with::path::to::function"
  ],
  "reasoning_update": {
    "complete::ast::id": "4-6 lines with operation type, current bug state, required fix, interaction, and failure mode"
  }
}
```

---

## FIELD REQUIREMENTS:

### 1. **task** (2-4 sentences)
A realistic GitHub issue describing:
- Observable symptoms and behaviors
- Expected vs actual behavior
- Steps to reproduce
- Impact on users/system

CRITICAL: Describe ONLY observable symptoms. NEVER mention:
- File names or paths
- Function/class names
- Implementation details
- The specific fix

---

### 2. **reasoning_selection** (4-8 lines)
Deep architectural analysis showing WHY these code locations must be examined together:

**MANDATORY STRUCTURE:**
a) **Problem Context** (1-2 lines): What is the core bug and its architectural significance?

b) **Data/Control Flow Analysis** (2-3 lines):
   - Trace the COMPLETE path of data/control flow through the buggy code
   - Show causal chains: "X calls Y which transforms data for Z"
   - Identify WHERE in the flow the bug originates
   - Format: `FunctionName (path/to/file.rs)` for every code item mentioned

c) **Coupling Justification** (1-2 lines):
   - Explain WHY these specific items are related to the bug
   - What would fail if ANY item is not examined? Be specific about failure modes
   - Show dependencies: type dependencies, call chains, shared state

d) **Boundary Analysis** (1 line):
   - Why ADJACENT items were NOT selected (e.g., "Helper functions work correctly")

**ABSOLUTE RULES:**
- NEVER reference "the patch", "the diff", or "provided changes"
- Write as if analyzing a live codebase from first principles
- Every code item mention MUST include its file path: `item_name (path/to/file.rs)`
- Show architectural understanding: identify layers (API, domain, storage), boundaries
- Use precise technical terms: "deserializes", "propagates", "validates", "transforms"

**ANTI-PATTERNS TO AVOID:**
❌ "These functions need to be updated for consistency"
❌ "The code handles authentication"
❌ "This is a bug in the error handling"
✅ "Function A (src/api/handler.rs::process) receives the request but fails to validate the signature BEFORE calling Function B (src/parser/json.rs::deserialize), causing invalid data to propagate to downstream business logic"

---

### 3. **selected_functions**
List of complete AST IDs in format: `path/to/file.rs::Type::method` or `path/to/file.rs::function::name`

Include ALL items that:
- Contain the bug
- Need to be examined to understand the bug
- Participate in the buggy data/control flow

---

### 4. **reasoning_update** (4-6 lines PER item)
For EACH selected function/struct, provide surgical reasoning about its role in the bug.

**MANDATORY STRUCTURE:**
a) **Operation Type**: Start with: BUGGY / MISSING / INCORRECT

b) **Current Bug State** (1 line):
   - What does this code currently do wrong?
   - What's the specific problem? (missing validation, wrong logic, incorrect parameter, etc.)

c) **Required Fix** (1-2 lines):
   - WHAT specific change fixes the bug
   - Include file path when mentioning the item: `function_name (path/to/file.rs)`
   - Be surgical: "Add null check before dereferencing pointer"

d) **Interaction with Other Items** (1-2 lines):
   - HOW does this bug connect to OTHER selected items?
   - Show the data/control flow: "This bug causes X to receive incorrect data from Y (path/to/y.rs::function)"
   - Reference other selected items WITH their file paths

e) **Failure Mode** (1 line):
   - What SPECIFIC behavior manifests due to this bug?
   - Be concrete: "NullPointerException thrown when processing empty input" NOT "Crashes sometimes"

**ABSOLUTE RULES:**
- ALWAYS include file path when referencing code items: `item (path/to/file.rs)`
- Show functional understanding of WHY the bug occurs
- Explain the "why" in terms of system behavior and architectural role
- Connect each bug location to the broader problem

**EXAMPLE GOOD REASONING:**
BUGGY: The connector_update function (crates/router/src/routes/admin.rs) currently extracts merchant_id from route parameters rather than the authenticated session context. This allows any authenticated user to specify arbitrary merchant IDs in the URL, bypassing ownership checks. This is the entry point vulnerability—it fails to validate merchant ownership BEFORE calling update_connector_config (crates/core/src/connector_ops.rs::update_connector_config). Without this validation, an attacker can modify connectors belonging to other merchants by crafting requests with their merchant_id in the path, leading to horizontal privilege escalation.

**EXAMPLE BAD REASONING:**
BUGGY: The connector_update function has a security bug. It needs to check authentication. This is important for security.

---

## QUALITY CHECKLIST (verify before submitting):

**reasoning_selection:**
- [ ] Mentions EVERY selected item with file path?
- [ ] Shows complete data/control flow chain leading to the bug?
- [ ] Explains coupling (why these items together reveal the bug)?
- [ ] States what behavior is wrong if any item is not examined?
- [ ] Mentions why adjacent code NOT selected?
- [ ] Zero references to patches/diffs/test output?
- [ ] Uses precise architectural terminology?

**reasoning_update (each item):**
- [ ] Starts with BUGGY/MISSING/INCORRECT?
- [ ] States current bug state clearly?
- [ ] Describes specific fix with file path?
- [ ] Shows interaction with other selected items (with file paths)?
- [ ] Identifies concrete failure mode?
- [ ] Demonstrates functional understanding of the bug?

**task (problem statement):**
- [ ] Describes observable symptoms only?
- [ ] NO file names, function names, or implementation details?
- [ ] NO mention of the specific fix?
- [ ] Includes clear reproduction steps?
- [ ] Realistic GitHub issue style?

---

## REMEMBER:
You are teaching an LLM to THINK like a senior engineer analyzing bug root causes, not just describing surface-level symptoms. Every sentence should add educational value about HOW to reason about bug location and causality.

End response with <END> token.
"""

load_dotenv()


def maybe_shorten(text_str: str, max_tokens: int, model: str) -> str:
    """Shorten text if it exceeds the max_tokens limit.
    If shortening, return a string with the first and last max_tokens//2 tokens.
    """
    if get_token_count([{"content": text_str}], model) < max_tokens:
        return text_str
    return text_str[: max_tokens // 2] + "\n\n(...)\n\n" + text_str[-max_tokens // 2 :]


class IssueGen:
    def __init__(
        self,
        config_file: Path,
        workers: int,
        instance_ids: list | None = None,
        dataset_path: str = HF_DATASET,
        redo_existing: bool = False,
        use_ast_context: bool = False,
        use_structured_reasoning: bool = False,
        max_group_size: int = 5,
    ):
        self.dataset_path = dataset_path
        self.redo_existing = redo_existing
        self.workers = workers
        self.use_ast_context = use_ast_context
        self.use_structured_reasoning = use_structured_reasoning
        self.max_group_size = max_group_size

        # Initialize structured reasoning extractor if needed
        self.reasoning_extractor = None
        if self.use_structured_reasoning:
            self.reasoning_extractor = StructuredReasoningExtractor()
            logger.info("Structured reasoning mode enabled")

        self.config = yaml.safe_load(config_file.read_text())
        self.model = self.config.get("model", "openai/gpt-4o")
        settings = self.config.get("settings", {})
        self.n_instructions = settings.get("n_instructions", 1)
        self.max_var_tokens = settings.get("max_var_tokens", 10_000)

        # Initialize Portkey model if needed
        self.portkey_model = None
        if (
            self.model.startswith("portkey/")
            or self.config.get("provider") == "portkey"
        ):
            self.portkey_model = PortkeyModel(
                model_name=self.model.replace("portkey/", ""),
                provider=self.config.get("provider", "openai"),
                litellm_model_name_override=self.config.get(
                    "litellm_model_name_override", ""
                ),
                **settings.get("portkey_kwargs", {}),
            )

        data_smith = [x for x in load_dataset(HF_DATASET, split="train")]
        self.dataset = (
            data_smith
            if dataset_path == HF_DATASET
            else json.loads(Path(dataset_path).read_text())
        )
        logger.info(f"Loaded {len(self.dataset)} instances from {dataset_path}")

        # Filter out instances that already have problem statements in HF dataset
        existing_problems = {
            d["instance_id"] for d in data_smith if d.get("problem_statement")
        }
        self.dataset = [
            x for x in self.dataset if x[KEY_INSTANCE_ID] not in existing_problems
        ]
        logger.info(
            f"Found {len(self.dataset)} instances without existing problem statements"
        )

        # Further filter based on other criteria
        self.dataset = sorted(
            [
                x
                for x in self.dataset
                if self._should_do_instance(x, instance_ids, redo_existing, self.model)
            ],
            key=lambda x: x[KEY_INSTANCE_ID],
        )
        logger.info(f"Will create issues for {len(self.dataset)} instances")

        if len(self.dataset) == 0:
            logger.warning(
                "No instances to process after filtering. Exiting gracefully."
            )
            return

        if FAIL_TO_PASS not in self.dataset[0]:
            raise ValueError(
                "Must be called with the result of swesmith.harness.gather, not the _all_patches.json file"
            )
        self.swebv = load_dataset("princeton-nlp/SWE-bench_Verified", split="test")

    def _should_do_instance(
        self, instance: dict, instance_ids: list | None, redo_existing: bool, model: str
    ) -> bool:
        repo = instance["repo"].split("/")[-1]

        output_file = LOG_DIR_ISSUE_GEN / repo / f"{instance[KEY_INSTANCE_ID]}.json"
        if not matches_instance_filter(instance[KEY_INSTANCE_ID], instance_ids):
            return False
        if redo_existing:
            return True
        if not output_file.exists():
            return True
        metadata = json.loads(output_file.read_text())
        if "responses" not in metadata:
            return True
        if model not in metadata["responses"]:
            return True
        return False

    def get_test_output(self, instance: dict) -> str:
        rp = registry.get_from_inst(instance)

        # Get execution output from running pytest for this instance (from validation step)
        test_output_path = (
            LOG_DIR_RUN_VALIDATION
            / instance["repo"].split("/")[-1]
            / instance[KEY_INSTANCE_ID]
            / LOG_TEST_OUTPUT
        )
        if not test_output_path.exists():
            run_patch_in_container(
                instance,
                instance["repo"].split("/")[-1],
                LOG_DIR_RUN_VALIDATION,
                rp.timeout,
                patch=instance[KEY_PATCH],
            )
        if not test_output_path.exists():
            return ""
        test_output = test_output_path.read_text()

        return maybe_shorten(
            test_output[
                test_output.find(TEST_OUTPUT_START)
                + len(TEST_OUTPUT_START) : test_output.find(TEST_OUTPUT_END)
            ],
            self.max_var_tokens,
            self.model,
        )

    def get_test_functions(self, instance: dict) -> tuple[list[str], list[str]]:
        """
        Get the source code for tests associated with the instance.

        Returns:
            list of test functions, list of repos to remove
        """
        test_funcs = []
        repos_to_remove = []
        test_idxs = list(range(len(instance[FAIL_TO_PASS])))
        random.shuffle(test_idxs)
        for test_idx in test_idxs:
            test_func = get_test_function(instance, test_idx)
            if test_func["cloned"]:
                repos_to_remove.append(test_func["repo_name"])
            test_funcs.append(test_func["test_src"])
        return test_funcs, repos_to_remove

    def get_demo_issues(self) -> list[str]:
        """
        Get a list of demonstration issues from the config file.
        """
        problem_statements = [
            maybe_shorten(instance["problem_statement"], 2000, self.model)
            for instance in self.swebv
        ]  # type: ignore[index]
        random.shuffle(problem_statements)
        return problem_statements

    def extract_structured_reasoning(
        self,
        patch: str,
        test_output: str,
        semantic_report: SemanticDiffReport | None = None,
    ) -> list[StructuredReasoning]:
        """Extract structured reasoning from patch using AST analysis + LLM.

        Similar to process_commit_async in generate_tasks_unified.py.
        """
        if not self.reasoning_extractor:
            return []

        # Build semantic diffs from patch
        semantic_items = self.reasoning_extractor.build_semantic_diffs(patch)
        if not semantic_items:
            return []

        # Group related items
        groups = self.reasoning_extractor.group_related_items(
            semantic_items, self.max_group_size
        )

        structured_reasonings = []

        for group_idx, group in enumerate(groups):
            # Build prompt for this group
            prompt = self.reasoning_extractor.build_prompt_for_group(
                group, test_output, semantic_report
            )

            # Call LLM for structured reasoning
            try:
                reasoning = self._call_llm_for_reasoning(prompt)
                if reasoning:
                    # Add location/summary info
                    reasoning.locations = [
                        {
                            "file": item.file,
                            "item": item.id.split("::")[-1],
                            "kind": item.kind,
                            "status": item.status,
                        }
                        for item in group
                    ]
                    reasoning.summary = {
                        "total_changes": len(group),
                        "files_changed": len(set(item.file for item in group)),
                        "items_added": sum(1 for item in group if item.status == "added"),
                        "items_modified": sum(1 for item in group if item.status == "modified"),
                        "items_removed": sum(1 for item in group if item.status == "removed"),
                        "group_id": group_idx,
                    }
                    structured_reasonings.append(reasoning)
            except Exception as e:
                logger.warning(f"Failed to generate structured reasoning for group {group_idx}: {e}")
                continue

        return structured_reasonings

    def _call_llm_for_reasoning(self, prompt: str) -> StructuredReasoning | None:
        """Call LLM to generate structured reasoning from prompt."""
        messages = [
            {"role": "system", "content": STRUCTURED_REASONING_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ]

        if self.portkey_model:
            response = self.portkey_model.query(
                messages, n=1, temperature=0.3, max_tokens=8000
            )
        else:
            response = completion(
                model=self.model,
                messages=messages,
                n=1,
                temperature=0.3,
                max_tokens=8000,
            )

        content = response.choices[0].message.content.strip()

        # Parse JSON from response
        reasoning_data = self._extract_json_from_response(content)
        if not reasoning_data:
            logger.warning("Failed to extract JSON from LLM response")
            return None

        # Validate required fields
        required_fields = ["task", "reasoning_selection", "selected_functions", "reasoning_update"]
        for field in required_fields:
            if field not in reasoning_data:
                logger.warning(f"Missing required field in LLM response: {field}")
                return None

        return StructuredReasoning(
            task=reasoning_data["task"],
            reasoning_selection=reasoning_data["reasoning_selection"],
            selected_functions=reasoning_data["selected_functions"],
            reasoning_update=reasoning_data["reasoning_update"],
        )

    def _extract_json_from_response(self, text: str) -> dict | None:
        """Extract JSON from LLM response text."""
        # Try to find JSON block
        json_match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
        if json_match:
            json_str = json_match.group(1)
        else:
            # Try to find raw JSON object
            json_match = re.search(r"(\{[\s\S]*\})", text)
            if json_match:
                json_str = json_match.group(1)
            else:
                json_str = text

        try:
            return json.loads(json_str)
        except json.JSONDecodeError:
            return None

    def generate_issue(self, instance: dict) -> dict:
        # Set up logging information
        repo = instance["repo"].split("/")[-1]
        inst_dir = LOG_DIR_ISSUE_GEN / repo
        inst_dir.mkdir(parents=True, exist_ok=True)

        output_file = inst_dir / f"{instance[KEY_INSTANCE_ID]}.json"
        output_file_exists = output_file.exists()

        # Get a reference instance from SWE-bench
        instance_curr = instance.copy()

        def format_prompt(prompt: str | None, config: dict, candidate: dict) -> str:
            if not prompt:
                return ""
            env = jinja2.Environment()

            def jinja_shuffle(seq):
                result = list(seq)
                random.shuffle(result)
                return result

            env.filters["shuffle"] = jinja_shuffle
            template = env.from_string(prompt)
            return template.render(**candidate, **config.get("parameters", {}))

        metadata = {}
        if output_file_exists:
            metadata = json.loads(output_file.read_text())

        if "messages" not in metadata:
            # Generate prompt
            messages = [
                {"content": self.config["system"], "role": "system"},
            ]
            if self.config["demonstration"]:
                messages.append(
                    {
                        "content": format_prompt(
                            self.config["demonstration"],
                            self.config,
                            {"demo_problem_statements": self.get_demo_issues()},
                        ),
                        "role": "user",
                    },
                )
            test_funcs, repos_to_remove = self.get_test_functions(instance_curr)
            prompt_vars = instance_curr | {
                "test_output": self.get_test_output(instance_curr),
                "test_funcs": test_funcs,
            }
            if self.use_ast_context:
                patch_text = instance_curr.get(KEY_PATCH, "")
                if patch_text:
                    semantic_report = enrich_patch(patch_text)
                    prompt_vars["ast_context"] = semantic_report.to_markdown()
            messages.append(
                {
                    "content": format_prompt(
                        self.config["instance"],
                        self.config,
                        prompt_vars,
                    ),
                    "role": "user",
                },
            )
            metadata = {"messages": messages, "repos_to_remove": repos_to_remove}
            with open(output_file, "w") as f_:
                json.dump(metadata, f_, indent=4)
        else:
            # If messages already exist, get repos_to_remove from existing metadata
            _, repos_to_remove = self.get_test_functions(instance_curr)
            messages = metadata["messages"]

        # Generate n_instructions completions containing problem statements
        if self.portkey_model:
            response = self.portkey_model.query(
                messages, n=self.n_instructions, stream=False
            )
        else:
            response = completion(
                model=self.model,
                messages=messages,
                n=self.n_instructions,
                temperature=0,
            )

        model_for_cost = self.model
        if self.portkey_model and self.portkey_model.config.litellm_model_name_override:
            model_for_cost = self.portkey_model.config.litellm_model_name_override

        cost = completion_cost(response, model=model_for_cost)

        metadata["cost"] = (0 if "cost" not in metadata else metadata["cost"]) + cost

        # Extract problem statements from response
        problem_statements = [
            choice.message.content  # type: ignore[attr-defined]
            for choice in response.choices  # type: ignore[attr-defined]
        ]

        if "responses" not in metadata:
            # Initialize responses dict if it doesn't exist
            metadata["responses"] = {}
        elif self.model in metadata["responses"]:
            # If responses for this model already exist, prepend them to the new ones
            problem_statements = metadata["responses"][self.model] + problem_statements

        # Add/update the response for current model
        metadata["responses"][self.model] = problem_statements

        # Generate structured reasoning if enabled
        if self.use_structured_reasoning:
            patch_text = instance_curr.get(KEY_PATCH, "")
            test_output = self.get_test_output(instance_curr)
            semantic_report = None

            if self.use_ast_context and patch_text:
                semantic_report = enrich_patch(patch_text)

            if patch_text:
                try:
                    structured_reasonings = self.extract_structured_reasoning(
                        patch_text, test_output, semantic_report
                    )
                    if structured_reasonings:
                        # Convert to serializable dicts
                        reasoning_dicts = [sr.to_dict() for sr in structured_reasonings]

                        if "structured_reasoning" not in metadata:
                            metadata["structured_reasoning"] = {}
                        metadata["structured_reasoning"][self.model] = reasoning_dicts
                        logger.debug(
                            f"Generated {len(reasoning_dicts)} structured reasoning entries for {instance[KEY_INSTANCE_ID]}"
                        )
                except Exception as e:
                    logger.warning(f"Failed to generate structured reasoning: {e}")

        with open(output_file, "w") as f_:
            json.dump(metadata, f_, indent=4)

        return {
            "status": "completed",
            "cost": cost,
            "repos_to_remove": repos_to_remove,
        }

    def _cleanup_repos(self, repos_to_remove):
        """Remove cloned repositories."""
        if not repos_to_remove:
            return

        logger.info(f"Cleaning up {len(repos_to_remove)} cloned repositories...")
        for repo_path in repos_to_remove:
            if os.path.exists(repo_path):
                try:
                    shutil.rmtree(repo_path)
                    logger.debug(f"Removed repository: {repo_path}")
                except Exception as e:
                    logger.warning(f"Failed to remove repository {repo_path}: {e}")
        logger.info("Repository cleanup completed.")

    def run(self):
        # Check if dataset is empty (initialization returned early)
        if not hasattr(self, "dataset") or len(self.dataset) == 0:
            logger.info("No instances to process. Exiting.")
            return

        stats = {
            "💰": 0.0,
            "⏭️": 0,
            "❌": 0,
            "✅": 0,
        }

        # Track repos to remove for cleanup
        all_repos_to_remove = set()

        # Pre-clone all required repositories to avoid race conditions in parallel execution
        # (RepoProfile.clone is not thread-safe)
        unique_repos = {instance["repo"].split("/")[-1] for instance in self.dataset}
        for repo_name in unique_repos:
            try:
                # registry.get(repo_name).clone() returns (dest, cloned)
                # cloned is True if it actually cloned, False if it already existed
                _, cloned = registry.get(repo_name).clone()
                if cloned:
                    all_repos_to_remove.add(repo_name)
            except Exception as e:
                logger.error(f"Failed to pre-clone {repo_name}: {e}")
                # We continue, assuming it might work later or will fail properly in the thread

        # Create a thread pool and call generate_issue for each instance
        with ThreadPoolExecutor(max_workers=self.workers) as executor:
            futures = []
            for instance in self.dataset:
                future = executor.submit(self.generate_issue, instance)
                futures.append(future)

            # Wait for all futures to complete
            with logging_redirect_tqdm():
                with tqdm(total=len(futures), desc="Generating issues") as pbar:
                    for future in as_completed(futures):
                        try:
                            result = future.result()
                        except KeyboardInterrupt:
                            raise
                        except Exception as e:
                            logger.error(
                                f"Error processing instance: {e}", exc_info=True
                            )
                            stats["❌"] += 1
                            continue
                        if result["status"] == "skipped":
                            stats["⏭️"] += 1
                        elif result["status"] == "completed":
                            stats["✅"] += 1
                            stats["💰"] += result["cost"]
                            # Collect repos to remove
                            if "repos_to_remove" in result:
                                all_repos_to_remove.update(result["repos_to_remove"])
                        pbar.set_postfix(stats, refresh=True)
                        pbar.update(1)

        # Cleanup cloned repositories
        self._cleanup_repos(all_repos_to_remove)

        # Merge generated issues into task instances
        if self.dataset_path == HF_DATASET:
            return
        dataset_path = Path(self.dataset_path)
        full_dataset = json.loads(dataset_path.read_text())
        kept = []
        for instance in full_dataset:
            repo = instance["repo"].split("/")[-1]
            output_file = LOG_DIR_ISSUE_GEN / repo / f"{instance[KEY_INSTANCE_ID]}.json"
            if not output_file.exists():
                continue
            metadata = json.loads(output_file.read_text())
            if "responses" not in metadata or self.model not in metadata["responses"]:
                continue
            instance["problem_statement"] = metadata["responses"][self.model][0]
            kept.append(instance)

        if kept:
            out_path = dataset_path.parent / f"{dataset_path.stem}__ig_llm.json"

            # Also include structured reasoning if available
            if self.use_structured_reasoning:
                for instance in kept:
                    repo = instance["repo"].split("/")[-1]
                    output_file = LOG_DIR_ISSUE_GEN / repo / f"{instance[KEY_INSTANCE_ID]}.json"
                    if output_file.exists():
                        metadata = json.loads(output_file.read_text())
                        if "structured_reasoning" in metadata and self.model in metadata["structured_reasoning"]:
                            instance["structured_reasoning"] = metadata["structured_reasoning"][self.model]

            with open(out_path, "w") as f:
                json.dump(kept, f, indent=2)
            print(
                f"Wrote {len(kept)}/{len(full_dataset)} instances with problem statements to {out_path}"
            )
            if self.use_structured_reasoning:
                print(f"  (Includes structured reasoning for {sum(1 for k in kept if 'structured_reasoning' in k)} instances)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "-d",
        "--dataset_path",
        type=str,
        help="Path to the dataset to annotate with bugs.",
        default=HF_DATASET,
    )
    parser.add_argument(
        "-i",
        "--instance_ids",
        type=str,
        help="Instance IDs to evaluate (supports exact matches and glob patterns like 'repo__name.*')",
        nargs="+",
    )
    parser.add_argument(
        "-c", "--config_file", type=Path, help="Path to the template config file."
    )
    parser.add_argument(
        "-w",
        "--workers",
        type=int,
        help="Number of workers to use for generation.",
        default=1,
    )
    parser.add_argument(
        "-r",
        "--redo_existing",
        action="store_true",
        help="Whether to redo instances that already have an output file.",
    )
    parser.add_argument(
        "--use_ast_context",
        action="store_true",
        help="Enable AST-based semantic context enrichment from patch hunks.",
    )
    parser.add_argument(
        "--use_structured_reasoning",
        action="store_true",
        help="Enable structured reasoning generation with reasoning_selection, selected_functions, and reasoning_update fields (similar to generate_tasks_unified.py).",
    )
    parser.add_argument(
        "--max_group_size",
        type=int,
        default=5,
        help="Maximum number of code items to group together for structured reasoning generation.",
    )
    args = parser.parse_args()
    if args.workers == 1:
        logger.warning(
            "Using only 1 worker for generation. You can speed up the generation by setting --workers > 1."
        )
    IssueGen(**vars(args)).run()
