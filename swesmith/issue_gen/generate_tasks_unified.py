"""
Unified task generation combining old format with enhanced reasoning.

Output format combines:
- Old fields: sha, chunk_id, locations, summary
- New fields: reasoning_selection, selected_functions, reasoning_update
"""

import json
import os
import logging
import asyncio
import re
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from openai import AsyncOpenAI
from rich.console import Console
from rich.logging import RichHandler

load_dotenv()

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    handlers=[RichHandler(rich_tracebacks=True, show_time=False)]
)
logger = logging.getLogger(__name__)

console = Console()


UNIFIED_SYSTEM_PROMPT_1 = """You are generating LLM TRAINING DATA for code modification tasks.

You will be given:
1. A group of related code changes (functions/structs from same logical feature)
2. Commit message and PR context
3. AST-level semantic diffs showing what changed

Your goal: Generate SIMPLE, ATOMIC tasks with IN-DEPTH reasoning.

CRITICAL REQUIREMENTS:

1. **Simple Task**: 1-2 sentences describing WHAT needs to be done (not HOW)

2. **reasoning_selection** (2-5 lines): Provide deep, architecture-aware justification for WHY these specific functions/locations must be modified together.
   - Whenever you mention a code item, append its file path immediately in parentheses. Format: `ItemName (path/to/file.rs)` or full AST ID `src/module/file.rs::Type::method (path/to/file.rs)`.
   - Explain how control flow and data flow move between these items (e.g., “Function A (src/transformers.rs::normalize) normalizes the request that Function B (src/validators.rs::validate) later validates, so both must change together when structure X evolves”).
   - Show concrete causal relationships: call chains, type dependencies, enum propagation, shared structs, transformers → mappers → business logic pipelines.
   - Include architectural reasons why these modules are the correct modification points (boundary-layer transformer, domain-layer mapper, storage-layer converter).
   - Explain exactly what would break—logically, structurally, or behaviorally—if ANY of the selected items were omitted.
   - NEVER reference commit messages, diffs, or provided changes. This must read like an expert engineer analyzing the live codebase.

3. **selected_functions**: List of complete AST IDs that need modification

4. **reasoning_update** (2-5 lines PER function): For EACH selected function, provide precise, architecture-level reasoning for the required modification.
   - Start with an operation prefix: MODIFY / ADD / DELETE.
   - When you mention the AST ID or any item in the explanation, append its file path in parentheses immediately. Format: `crates/foo/src/bar.rs::Struct::method (crates/foo/src/bar.rs)`.
   - Describe WHAT specific change the function or struct needs (field added, enum expanded, mapping updated, validation introduced).
   - Explain WHY the change is necessary in terms of system behavior (e.g., “This transformer must surface the new field so the downstream status mapper receives the updated state”).
   - Show HOW this item interacts with the others in the task (shared structs, propagation paths, conversion logic, entry/exit points). When referencing the other items here, also include their file paths.
   - Detail WHAT breaks if this change is not applied (incorrect state propagation, failing deserialization, inconsistent domain mapping, unreachable code paths).
   - The explanation must reflect deep understanding of the functional role of this code location within the wider system.

IMPORTANT:
- Keep tasks simple and focused (1 logical change)
- Make reasoning DETAILED and EDUCATIONAL (2-5 lines each)
- Use complete AST IDs (e.g., "src/file.rs::Struct::function::method_name")
- reasoning_update MUST be a dict with AST ID keys
- Show deep causal reasoning, not surface-level descriptions
- End response with <END> token

EXAMPLE (showing depth):
```json
{
  "tasks": [
    {
      "task": "Add signature validation for incoming webhook payloads",
      "reasoning_selection": "To implement secure webhook processing in the payment gateway, I need to identify the request handling chain and security validation points. In this architecture, src/webhooks/handler.rs contains the main 'process_webhook' function which receives raw HTTP requests and extracts the payload - this is the entry point that must validate authenticity before any processing occurs. The function 'src/security/validators.rs::validate_hmac_signature' needs to be added because webhooks require cryptographic verification using HMAC-SHA256 to prevent payload tampering and replay attacks. These functions form a critical security dependency: process_webhook must call validate_hmac_signature BEFORE deserializing the payload, otherwise malicious requests could exploit the JSON parser or inject false transaction data. Without signature validation in the handler, the entire webhook system is vulnerable to spoofing attacks.",
      "selected_functions": [
        "src/webhooks/handler.rs::function::process_webhook",
        "src/security/validators.rs::function::validate_hmac_signature"
      ],
      "reasoning_update": {
        "src/webhooks/handler.rs::function::process_webhook": "MODIFY: The process_webhook function currently accepts and deserializes incoming HTTP webhook payloads directly without any authentication. This change adds a validation step BEFORE deserialization by calling validate_hmac_signature with the raw request body and the signature from the X-Webhook-Signature header. This is critical because without signature verification, attackers could send fake webhook events to trigger unauthorized payment confirmations or refunds. The modification must happen at the very beginning of the function, immediately after receiving the request but before any data parsing, to prevent exploitation of potential JSON parsing vulnerabilities.",
        "src/security/validators.rs::function::validate_hmac_signature": "ADD: This new function implements HMAC-SHA256 signature verification for webhook authenticity. It takes the raw request body bytes and the received signature, computes the expected HMAC using the merchant's secret key from configuration, and performs a constant-time comparison to prevent timing attacks. This function is essential because it provides the cryptographic proof that the webhook genuinely came from the payment gateway and hasn't been tampered with in transit. Without this validation function, there's no way for process_webhook to distinguish legitimate events from malicious forgeries, leaving the system open to payment fraud through fake webhook injection."
      }
    }
  ]
}
```
<END>
<END>
<END>
"""

UNIFIED_SYSTEM_PROMPT = """You are generating HIGH-QUALITY LLM TRAINING DATA for code modification tasks.

You will be given:
1. A group of related code changes (functions/structs from same logical feature)
2. Commit message and PR context
3. AST-level semantic diffs showing what changed

Your goal: Generate SIMPLE, ATOMIC tasks with EXCEPTIONALLY DEEP reasoning that teaches an LLM HOW TO THINK about code location selection.

## CRITICAL REQUIREMENTS:

### 1. **Task Description** (1-2 sentences)
Describe WHAT needs to be done, not HOW. Focus on the business/technical objective, not implementation details.

Example: "Add signature validation for incoming webhook payloads"
NOT: "Add an HMAC function and call it in the handler"

---

### 2. **reasoning_selection** (4-8 lines)

This is the MOST CRITICAL section. It must teach the model systematic thinking about code location selection.

**MANDATORY STRUCTURE:**

a) **Problem Context** (1-2 lines): What is the core issue/feature and its architectural significance?

b) **Data/Control Flow Analysis** (2-3 lines): 
   - Trace the COMPLETE path of data/control flow through the system
   - Show causal chains: "X calls Y which transforms data for Z"
   - Identify WHERE in the flow each modification must occur
   - Format: `FunctionName (path/to/file.rs)` for every code item mentioned

c) **Coupling Justification** (1-2 lines):
   - Explain WHY these specific items must change TOGETHER
   - What breaks if ANY item is omitted? Be specific about failure modes
   - Show dependencies: type dependencies, enum exhaustiveness, interface contracts

d) **Boundary Analysis** (1 line):
   - Briefly explain why ADJACENT items were NOT selected
   - Example: "Other admin functions already use the correct auth pattern"

**ABSOLUTE RULES:**
- NEVER reference "the commit", "the PR", "the diff", or "provided changes"
- Write as if analyzing a live codebase from first principles
- Every code item mention MUST include its file path: `item_name (path/to/file.rs)`
- Show architectural understanding: identify layers (API, domain, storage), boundaries, responsibility segregation
- Use precise technical terms: "deserializes", "propagates", "validates", "transforms", "maps"

**ANTI-PATTERNS TO AVOID:**
❌ "These functions need to be updated for consistency"
❌ "The code handles authentication"
❌ "This change is important for security"
✅ "Function A (src/api/handler.rs::process) receives the request and must validate the signature BEFORE calling Function B (src/parser/json.rs::deserialize), because deserialization of untrusted input could trigger parser exploits or inject malicious data into downstream business logic"

---

### 3. **selected_functions**
Complete AST IDs in format: `path/to/file.rs::Type::method`

---

### 4. **reasoning_update** (4-6 lines PER item)

For EACH selected function/struct, provide surgical, architecture-aware reasoning.

**MANDATORY STRUCTURE:**

a) **Operation Type**: Start with: MODIFY / ADD / DELETE

b) **Current State** (1 line):
   - What does this code currently do/not do?
   - What's the specific problem? (missing validation, wrong parameter source, etc.)

c) **Required Change** (1-2 lines):
   - WHAT specific modification is needed (new field, enum variant, validation call, parameter change)
   - Include file path when mentioning the item: `function_name (path/to/file.rs)`
   - Be surgical: "Replace route parameter extraction with auth context access"

d) **Interaction with Other Changes** (1-2 lines):
   - HOW does this change connect to the OTHER selected items?
   - Show the data/control flow: "This change ensures X passes correct data to Y (path/to/y.rs::function)"
   - Reference other selected items WITH their file paths

e) **Failure Mode** (1 line):
   - What SPECIFIC behavior breaks if this change is omitted?
   - Be concrete: "Merchants could access other merchants' connector data" NOT "Security issue"

**ABSOLUTE RULES:**
- ALWAYS include file path when referencing code items: `item (path/to/file.rs)`
- Show functional understanding, not just surface changes
- Explain the "why" in terms of system behavior and architectural role
- Connect each change to the broader task context

**EXAMPLE GOOD REASONING:**
MODIFY: The connector_update function (crates/router/src/routes/admin.rs) currently extracts merchant_id from route parameters rather than the authenticated session context. This change replaces parameter extraction with auth.merchant_id access, ensuring only the authenticated merchant can modify their connectors. This modification is the entry point that prevents unauthorized access—it must validate merchant ownership BEFORE calling update_connector_config (crates/core/src/connector_ops.rs::update_connector_config), which performs the actual database update. Without this fix, an attacker could craft requests with arbitrary merchant_ids in the URL path, bypassing authentication to modify connectors belonging to other merchants, leading to privilege escalation.

**EXAMPLE BAD REASONING:**
MODIFY: Update the connector_update function to use proper authentication. This is important for security.

---

## OUTPUT FORMAT:
```json
{
  "tasks": [
    {
      "task": "Simple task description",
      "reasoning_selection": "4-8 lines with problem context, flow analysis, coupling justification, and boundary analysis. Every code item mention includes (path/to/file.rs).",
      "selected_functions": [
        "complete::ast::id::with::path"
      ],
      "reasoning_update": {
        "complete::ast::id": "4-6 lines with operation type, current state, required change with (file paths), interaction with other changes (with file paths), and specific failure mode."
      }
    }
  ]
}
```
<END>
<END>
<END>
---

## QUALITY CHECKLIST (verify before submitting):

**reasoning_selection:**
- [ ] Mentions EVERY selected item with file path?
- [ ] Shows complete data/control flow chain?
- [ ] Explains coupling (why these items together)?
- [ ] States what breaks if any item omitted?
- [ ] Mentions why adjacent code NOT selected?
- [ ] Zero references to commits/diffs/PRs?
- [ ] Uses precise architectural terminology?

**reasoning_update (each item):**
- [ ] Starts with MODIFY/ADD/DELETE?
- [ ] States current problem clearly?
- [ ] Describes specific change needed with file path?
- [ ] Shows interaction with other selected items (with file paths)?
- [ ] Identifies concrete failure mode?
- [ ] Demonstrates functional understanding?

---

## REMEMBER:
You are teaching an LLM to THINK like a senior engineer analyzing code architecture, not just describing surface-level changes. Every sentence should add educational value about HOW to reason about code modification scope.

End response with <END> token.
"""


class UnifiedTaskGenerator:
    """Unified task generator with old format + enhanced reasoning."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        max_group_size: int = 5,
        max_concurrent_requests: int = 5,
        max_total_changes: int = 100
    ):
        """Initialize unified task generator.

        Args:
            api_key: LLM API key
            base_url: API base URL
            model: Model name
            max_group_size: Max functions per task group
            max_concurrent_requests: Max concurrent LLM calls
            max_total_changes: Max total changes per commit (skip commits exceeding this)
        """
        self.api_key = api_key or os.getenv("LLM_API_KEY")
        self.base_url = base_url or os.getenv("LLM_BASE_URL", "https://api.openai.com/v1")
        self.model = model or os.getenv("LLM_MODEL", "gpt-4o-mini")
        self.max_group_size = max_group_size
        self.max_concurrent = max_concurrent_requests
        self.max_total_changes = max_total_changes

        if not self.api_key:
            raise ValueError("LLM_API_KEY is required")

        self.client = AsyncOpenAI(api_key=self.api_key, base_url=self.base_url, timeout=6000)
        self.semaphore = asyncio.Semaphore(max_concurrent_requests)

        logger.info(f"Unified Task Generator initialized")
        logger.info(f"  Model: {self.model}")
        logger.info(f"  Max group size: {self.max_group_size}")
        logger.info(f"  Max concurrent: {self.max_concurrent}")
        logger.info(f"  Max total changes per commit: {self.max_total_changes}")

    def group_related_diffs(self, diffs: list[dict]) -> list[list[dict]]:
        """Group related diffs by file and semantic relationships."""
        if not diffs:
            return []

        # Group by file first
        file_groups = {}
        for diff in diffs:
            file_path = diff.get("file", "")
            if file_path not in file_groups:
                file_groups[file_path] = []
            file_groups[file_path].append(diff)

        # For each file, create sub-groups
        final_groups = []
        for file_path, file_diffs in file_groups.items():
            if len(file_diffs) <= self.max_group_size:
                final_groups.append(file_diffs)
            else:
                # Split into chunks
                for i in range(0, len(file_diffs), self.max_group_size):
                    chunk = file_diffs[i:i + self.max_group_size]
                    final_groups.append(chunk)

        return final_groups

    def summarize_group_changes(self, diff_group: list[dict]) -> dict:
        """Summarize changes in a diff group."""
        files_changed = set()
        locations = []
        added = 0
        modified = 0
        removed = 0

        for diff in diff_group:
            status = diff.get("status", "modified")
            file_path = diff.get("file", "")
            item_id = diff.get("id", "")
            kind = diff.get("kind", "")

            files_changed.add(file_path)
            item_name = item_id.split("::")[-1] if item_id else "unknown"

            locations.append({
                "file": file_path,
                "item": item_name,
                "kind": kind,
                "status": status
            })

            if status == "added":
                added += 1
            elif status == "modified":
                modified += 1
            elif status == "removed":
                removed += 1

        return {
            "total_changes": len(diff_group),
            "files_changed": len(files_changed),
            "added": added,
            "modified": modified,
            "removed": removed,
            "locations": locations,
            "files": list(files_changed)
        }

    def build_prompt(self, diff_group: list[dict], pr_context: str, commit_message: str) -> str:
        """Build prompt for a group of related diffs."""
        # Summarize the diff group
        changes_summary = []
        for diff in diff_group:
            ast_id = diff.get("id", "")
            status = diff.get("status", "modified")
            kind = diff.get("kind", "")
            file = diff.get("file", "")

            changes_summary.append(f"  - {status.upper()}: {ast_id} ({kind}) in {file}")

        changes_str = "\n".join(changes_summary)

        prompt = f"""
**PR Context:**
{pr_context}

**Commit Message:**
{commit_message}

**Code Changes ({len(diff_group)} items):**
{changes_str}

Generate a simple, focused task with detailed reasoning for these related changes.

Remember:
- Task should be 1-2 sentences
- reasoning_selection shows codebase analysis (don't reference commit/PR)
- reasoning_update is a dict with entries for each selected function
- Include operation type (MODIFY/ADD/DELETE) in reasoning_update
"""
        return prompt

    async def generate_task_for_group(
        self,
        diff_group: list[dict],
        pr_context: str,
        commit_message: str,
        group_id: str
    ) -> dict | None:
        """Generate a task for a group of related diffs."""
        prompt = self.build_prompt(diff_group, pr_context, commit_message)

        async with self.semaphore:
            try:
                response = await self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": UNIFIED_SYSTEM_PROMPT},
                        {"role": "user", "content": prompt}
                    ],
                    temperature=0.5,
                    max_tokens=30000,  # Increased for detailed reasoning (2-5 lines per field)
                    stop=["<END>"]
                )

                content = response.choices[0].message.content.strip()

                # Extract JSON
                task_data = self.extract_json(content)
                if not task_data or "tasks" not in task_data:
                    logger.warning(f"Invalid response for {group_id}")
                    return None

                tasks = task_data["tasks"]
                if not tasks:
                    return None

                task = tasks[0]
                if self.validate_task(task):
                    logger.debug(f"Generated task for {group_id}")
                    return task
                else:
                    logger.warning(f"Invalid task format for {group_id}")
                    return None

            except Exception as e:
                logger.error(f"Error generating task for {group_id}: {e}")
                return None

    def extract_json(self, text: str) -> dict | None:
        """Extract JSON from text response."""
        candidates = re.findall(r"\{[\s\S]*\}", text)
        for cand in sorted(candidates, key=len, reverse=True):
            try:
                return json.loads(cand)
            except json.JSONDecodeError:
                continue
        return None

    def validate_task(self, task: dict) -> bool:
        """Validate task structure."""
        required = ["task", "reasoning_selection", "selected_functions", "reasoning_update"]

        for field in required:
            if field not in task:
                return False

            if field == "selected_functions":
                if not isinstance(task[field], list) or not task[field]:
                    return False
            elif field == "reasoning_update":
                if not isinstance(task[field], dict):
                    return False
                for func in task.get("selected_functions", []):
                    if func not in task[field]:
                        return False
            else:
                if not isinstance(task[field], str) or not task[field].strip():
                    return False

        return True

    def build_pr_context(self, pr_data: dict) -> str:
        """Build PR context string."""
        context_parts = []

        if pr_data.get("title"):
            context_parts.append(f"PR Title: {pr_data['title']}")

        if pr_data.get("description"):
            desc = pr_data["description"].strip()[:500]
            if desc:
                context_parts.append(f"PR Description: {desc}")

        return "\n".join(context_parts) if context_parts else "No PR context"

    async def process_commit_async(
        self,
        pr_context: str,
        commit_info: dict,
        diff_file: Path
    ) -> list[dict]:
        """Process a commit and generate unified tasks.

        Args:
            pr_context: PR context
            commit_info: Commit metadata
            diff_file: Path to diff.json

        Returns:
            List of tasks (old format + enhanced fields)
        """
        if not diff_file.exists():
            return []

        # Load diffs
        with open(diff_file, 'r') as f:
            diff_data = json.load(f)

        diffs = diff_data.get("diffs", [])
        if not diffs:
            return []

        commit_sha = commit_info["sha"]
        commit_sha_short = commit_sha[:7]
        commit_message = commit_info.get("message", "")

        # Check if commit exceeds max total changes limit
        if len(diffs) > self.max_total_changes:
            logger.warning(f"  Commit {commit_sha_short}: SKIPPED - {len(diffs)} changes exceeds max ({self.max_total_changes})")
            return []

        # Group related diffs
        diff_groups = self.group_related_diffs(diffs)
        logger.info(f"  Commit {commit_sha_short}: {len(diffs)} changes -> {len(diff_groups)} groups")

        # Generate tasks for all groups concurrently (respecting semaphore)
        task_coroutines = []
        for group_idx, group in enumerate(diff_groups):
            group_id = f"{commit_sha_short}_group{group_idx}"
            task_coro = self.generate_task_for_group(
                group, pr_context, commit_message, group_id
            )
            task_coroutines.append(task_coro)

        # Wait for all tasks to complete (semaphore controls concurrency)
        enhanced_tasks = await asyncio.gather(*task_coroutines)

        # Combine formats
        results = []
        for group_idx, (group, enhanced_task) in enumerate(zip(diff_groups, enhanced_tasks)):
            if enhanced_task is None:
                continue

            # Get change summary for old format fields
            change_summary = self.summarize_group_changes(group)

            # UNIFIED FORMAT: Old fields + Enhanced fields
            unified_task = {
                # Old format fields
                "sha": commit_sha,
                "message": commit_message,
                "author": commit_info.get("author", ""),
                "date": commit_info.get("date", ""),
                "url": commit_info.get("url", ""),
                "chunk_id": group_idx,
                "total_chunks": len(diff_groups),
                "task": enhanced_task.get("task", ""),
                "locations": change_summary["locations"],
                "summary": {
                    "total_changes": change_summary["total_changes"],
                    "files_changed": change_summary["files_changed"],
                    "added": change_summary["added"],
                    "modified": change_summary["modified"],
                    "removed": change_summary["removed"]
                },
                "skipped": False,

                # NEW: Enhanced reasoning fields
                "reasoning_selection": enhanced_task.get("reasoning_selection", ""),
                "selected_functions": enhanced_task.get("selected_functions", []),
                "reasoning_update": enhanced_task.get("reasoning_update", {})
            }

            results.append(unified_task)

        return results

    async def process_pr_async(
        self,
        pr_file: Path,
        diff_dir: Path,
        output_dir: Path
    ) -> dict:
        """Process a PR and generate unified tasks."""
        with open(pr_file, 'r') as f:
            pr_data = json.load(f)

        pr_number = pr_data["number"]
        pr_context = self.build_pr_context(pr_data)

        logger.info(f"Processing PR #{pr_number}: {pr_data.get('title', '')[:50]}...")

        # Process all commits
        commit_tasks = []
        for commit_info in pr_data.get("commits", []):
            commit_sha_short = commit_info["sha"][:7]
            diff_file = diff_dir / f"pr_{pr_number}" / commit_sha_short / "diff.json"

            task = self.process_commit_async(pr_context, commit_info, diff_file)
            commit_tasks.append(task)

        # Wait for all commits
        all_results = await asyncio.gather(*commit_tasks)

        # Flatten results
        all_task_chunks = []
        for result_list in all_results:
            all_task_chunks.extend(result_list)

        # Save output (old format structure)
        output_file = output_dir / f"pr_{pr_number}_tasks.json"
        output_data = {
            "pr_number": pr_number,
            "pr_title": pr_data.get("title", ""),
            "pr_description": pr_data.get("description", ""),
            "pr_url": pr_data.get("url", ""),
            "pr_author": pr_data.get("author", ""),
            "pr_merged_at": pr_data.get("merged_at", ""),
            "task_chunks": all_task_chunks  # Each chunk has old + new fields
        }

        with open(output_file, 'w') as f:
            json.dump(output_data, f, indent=2)

        return {
            "pr_number": pr_number,
            "tasks_generated": len(all_task_chunks)
        }

    def process_all(
        self,
        pr_dir: Path,
        diff_dir: Path,
        output_dir: Path,
        repo_name: str
    ) -> dict:
        """Process all PRs (sync wrapper)."""
        return asyncio.run(self.process_all_async(pr_dir, diff_dir, output_dir, repo_name))

    async def process_all_async(
        self,
        pr_dir: Path,
        diff_dir: Path,
        output_dir: Path,
        repo_name: str,
        resume: bool = True
    ) -> dict:
        """Process all PRs to generate unified tasks (sequentially, one PR at a time).
        
        Args:
            pr_dir: Directory with PR JSON files
            diff_dir: Directory with diff data
            output_dir: Output directory for tasks
            repo_name: Repository name
            resume: If True, skip PRs that already have task files (default: True)
        """
        pr_files = sorted(pr_dir.glob("pr_*.json"), key=lambda x: int(x.stem.replace("pr_", "")))

        if not pr_files:
            logger.warning(f"No PR files found in {pr_dir}")
            return {"total_prs": 0, "total_tasks": 0}

        output_dir.mkdir(parents=True, exist_ok=True)

        # Check for existing task files if resume mode
        existing_tasks = set()
        skipped_count = 0
        if resume:
            for task_file in output_dir.glob("pr_*_tasks.json"):
                # Extract PR number from filename (e.g., pr_10002_tasks.json -> 10002)
                pr_num = task_file.stem.replace("pr_", "").replace("_tasks", "")
                existing_tasks.add(f"pr_{pr_num}.json")
            
            if existing_tasks:
                console.print(f"[dim]Resume mode: Found {len(existing_tasks)} existing task files[/dim]")

        console.print(f"\n[bold cyan]Generating Unified Tasks for: {repo_name}[/bold cyan]")
        console.print(f"[dim]Model: {self.model}[/dim]")
        console.print(f"[dim]Max group size: {self.max_group_size}[/dim]")
        console.print(f"[dim]Max concurrent: {self.max_concurrent}[/dim]")
        console.print(f"[dim]Format: Old format + Enhanced reasoning[/dim]")
        console.print(f"[dim]Resume mode: {'enabled' if resume else 'disabled'}[/dim]")
        console.print(f"[dim]Input: {pr_dir}[/dim]")
        console.print(f"[dim]Output: {output_dir}[/dim]\n")

        # Filter out already processed PRs if resume mode
        prs_to_process = []
        for pr_file in pr_files:
            if resume and pr_file.name in existing_tasks:
                skipped_count += 1
                continue
            prs_to_process.append(pr_file)

        if resume and skipped_count > 0:
            console.print(f"[cyan]Skipping {skipped_count} already processed PRs[/cyan]")
        
        console.print(f"[cyan]Processing {len(prs_to_process)} PRs sequentially...[/cyan]\n")

        # Process PRs sequentially (one at a time)
        total_prs = 0
        total_tasks = 0

        for idx, pr_file in enumerate(prs_to_process, 1):
            console.print(f"[yellow]PR {idx}/{len(prs_to_process)}:[/yellow] {pr_file.name}")
            
            try:
                stats = await self.process_pr_async(pr_file, diff_dir, output_dir)
                total_prs += 1
                total_tasks += stats["tasks_generated"]
                console.print(f"  [green]✓ PR #{stats['pr_number']} completed - Generated {stats['tasks_generated']} tasks[/green]\n")
            except Exception as e:
                logger.error(f"  [red]✗ Error processing {pr_file.name}: {e}[/red]\n")
                continue

        console.print(f"\n[bold green]✓ Unified task generation complete![/bold green]")
        console.print(f"\n[bold]Summary:[/bold]")
        if resume and skipped_count > 0:
            console.print(f"  PRs skipped (already processed): {skipped_count}")
        console.print(f"  PRs processed: {total_prs}")
        console.print(f"  Tasks generated: {total_tasks}")
        console.print(f"  Avg tasks per PR: {total_tasks/total_prs:.1f}" if total_prs > 0 else "")
        console.print(f"  Output: {output_dir}")

        return {
            "total_prs": total_prs,
            "total_tasks": total_tasks,
            "skipped_prs": skipped_count,
            "output_dir": str(output_dir)
        }
