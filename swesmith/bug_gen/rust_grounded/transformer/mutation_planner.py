"""Mutation planner - LLM outputs JSON plans, not patches."""

import json
import os
import re
from dataclasses import dataclass, asdict
from typing import List, Optional, Dict, Any

from litellm import completion


@dataclass
class MutationInstruction:
    """Single mutation instruction."""
    type: str
    target_file: str
    target_function: str
    location_hint: str
    original_fragment: str
    mutation_details: Dict[str, Any]
    reasoning: str


@dataclass
class MutationPlan:
    """Complete mutation plan for bug generation."""
    strategy: str
    target_symbols: List[str]
    mutations: List[MutationInstruction]
    affected_behavior: str
    difficulty: str
    reasoning_chain: List[str]
    cross_file_impact: List[str] = None
    detection_difficulty: str = "medium"

    def to_json(self) -> str:
        """Serialize to JSON."""
        return json.dumps(asdict(self), indent=2)

    @classmethod
    def from_json(cls, json_str: str) -> "MutationPlan":
        """Deserialize from JSON."""
        data = json.loads(json_str)
        data["mutations"] = [MutationInstruction(**m) for m in data["mutations"]]
        return cls(**data)


class MutationPlanner:
    """Generate structured mutation plans (JSON) instead of patches."""

    SYSTEM_PROMPT = """You are an expert Rust code analyzer specializing in creating SUBTLE, COMPILABLE bugs.

YOUR TASK: Generate mutation plans that COMPILE but have INCORRECT behavior.

CRITICAL RULES:
1. SINGLE-FILE ONLY: Generate bugs in ONE file only (the target file)
2. MUST COMPILE: All mutations must produce syntactically valid Rust
3. SIMPLE SEMANTIC BUGS: Focus on operator swaps, condition flips, off-by-one, missing checks
4. NO TYPE CHANGES: Do not change types, lifetimes, or function signatures
5. CONSERVATIVE: Only modify what's explicitly in the provided code context

APPROVED MUTATION TYPES:
- "flip_comparison": Change == to !=, > to <, >= to <=, etc.
- "off_by_one": Change < to <=, or adjust loop bounds by 1
- "remove_guard": Remove an if check or boundary validation
- "logic_swap": Change && to || in conditions
- "invert_boolean": Negate a boolean condition
- "missing_return": Remove an early return or break
- "remove_error_handling": Remove ? operator from error propagation
- "swap_arguments": Swap order of two function arguments
- "change_operator": Change + to -, * to / in arithmetic

FORBIDDEN (Will cause compile/runtime errors):
- Multi-file mutations
- Type changes (String to &str, etc.)
- Lifetime modifications
- Adding/removing function parameters
- Modifying struct definitions
- Async reordering
- State machine changes

OUTPUT FORMAT:
```json
{
  "strategy": "Brief description of the bug",
  "target_symbols": ["function_name"],
  "mutations": [
    {
      "type": "flip_comparison",
      "target_file": "src/file.rs",
      "target_function": "func_name",
      "location_hint": "Line X, inside condition",
      "original_fragment": "exact code",
      "mutation_details": {
        "operator": ">",
        "new_operator": "<",
        "new_fragment": "modified code"
      },
      "reasoning": "Why this causes a bug"
    }
  ],
  "affected_behavior": "What breaks",
  "difficulty": "medium",
  "reasoning_chain": ["Step 1", "Step 2", "Step 3"]
}
```
- "permission_check": Modify authorization/permission validation
- "transaction_boundary": Alter transaction begin/commit points

FORBIDDEN (Too Obvious):
- Simple variable renames
- Deleting entire functions
- Changing public API signatures
- Breaking obvious compilation

MULTI-FILE STRATEGIES:
1. Caller/callee mismatch: Change function behavior AND all call sites
2. State inconsistency: Modify struct definition AND its usage sites
3. Error handling drift: Change error type AND conversion sites
4. Trait/impl divergence: Modify trait AND implementation differently
5. Config validation gap: Change config parsing AND validation logic

OUTPUT FORMAT - STRICT JSON:
```json
{
  "strategy": "Detailed description of multi-file bug strategy (2-3 sentences)",
  "target_symbols": ["func_a", "struct_b", "trait_c"],
  "mutations": [
    {
      "type": "state_mismatch",
      "target_file": "src/state_machine.rs",
      "target_function": "transition",
      "location_hint": "Line 45, in the match arm for State::Processing",
      "original_fragment": "exact multi-line code snippet",
      "mutation_details": {
        "change": "Allow transition from Processing to Completed without validation",
        "new_fragment": "modified code",
        "impact_files": ["src/validator.rs", "src/api.rs"]
      },
      "reasoning": "This invalidates the state invariant that Processing must have validated data"
    }
  ],
  "affected_behavior": "Detailed description of what breaks and why it's hard to detect",
  "difficulty": "hard",
  "detection_difficulty": "high",
  "reasoning_chain": [
    "Step 1: The original code maintains invariant X through mechanism Y",
    "Step 2: File A establishes the precondition for this invariant",
    "Step 3: File B relies on this invariant for safe operation",
    "Step 4: File C assumes the invariant holds when calling File B",
    "Step 5: Our mutation breaks the invariant in File A",
    "Step 6: File B now operates on invalid state",
    "Step 7: File C triggers the bug through normal usage",
    "Step 8: The failure manifests far from the root cause, making debugging difficult"
  ],
  "cross_file_impact": [
    "src/state_machine.rs: Invalid state transitions now possible",
    "src/validator.rs: Assumes states are valid, skips checks",
    "src/api.rs: Calls validator, propagates corrupt data"
  ]
}
```

REMEMBER:
- Generate 2-4 mutations across multiple files when context allows
- Each mutation should be subtle on its own but combine to create bugs
- Focus on semantic bugs that compile but violate invariants
- Prioritize async, concurrent, or stateful code"""

    INSTANCE_TEMPLATE = """{context}

GENERATE A SINGLE-FILE MUTATION PLAN:

CRITICAL: Only modify the TARGET FILE. Do NOT generate mutations for other files.

Requirements:
1. SINGLE-FILE ONLY: Mutate ONLY in the target file ({target_file})
2. SIMPLE BUGS: flip_comparison, off_by_one, remove_guard, logic_swap
3. MUST COMPILE: Changes must not break compilation
4. TARGET: Find a conditional, loop bound, or comparison to modify

Available mutation types:
- flip_comparison: Change == to !=, > to <, >= to <=
- off_by_one: Change < to <= or adjust by 1
- remove_guard: Remove an if check
- logic_swap: Change && to ||
- invert_boolean: Add/remove ! before condition

Focus on ONE specific location in the target function.

Output ONLY the JSON mutation plan in a ```json block. No other text."""

    def __init__(self, model: str = "private-large"):
        self.model = model
        self.api_key = os.getenv("LITE_LLM_API_KEY", "")
        self.api_base = os.getenv("LITE_LLM_URL", "")

    def generate_plan(
        self,
        context_str: str,
        target_file: str = "",
        max_retries: int = 3
    ) -> Optional[MutationPlan]:
        """Generate a mutation plan with retry logic."""
        prompt = self.INSTANCE_TEMPLATE.format(context=context_str, target_file=target_file)

        for attempt in range(max_retries):
            try:
                response = completion(
                    model=f"openai/{self.model}",
                    messages=[
                        {"role": "system", "content": self.SYSTEM_PROMPT},
                        {"role": "user", "content": prompt}
                    ],
                    temperature=0.5 + (attempt * 0.1),
                    max_tokens=6000,
                    api_key=self.api_key,
                    base_url=self.api_base,
                )

                content = response.choices[0].message.content

                # Extract JSON plan
                plan = self._extract_plan(content)
                if plan:
                    return plan

                print(f"Attempt {attempt + 1}: Failed to extract valid plan")

            except Exception as e:
                print(f"Planning attempt {attempt + 1} failed: {e}")
                continue

        return None

    def _extract_plan(self, content: str) -> Optional[MutationPlan]:
        """Extract mutation plan from LLM response."""
        patterns = [
            r'```json\s*\n(.*?)```',
            r'```\s*\n(.*?)```',
        ]

        json_str = None
        for pattern in patterns:
            match = re.search(pattern, content, re.DOTALL)
            if match:
                json_str = match.group(1).strip()
                break

        if not json_str:
            start = content.find('{')
            end = content.rfind('}')
            if start != -1 and end != -1 and end > start:
                json_str = content[start:end+1]

        if not json_str:
            print(f"  Warning: No JSON found in response")
            return None

        try:
            data = json.loads(json_str)

            required = ["strategy", "target_symbols", "mutations", "affected_behavior"]
            for field in required:
                if field not in data:
                    print(f"  Warning: Missing required field '{field}' in mutation plan")
                    return None

            mutations = []
            for i, m in enumerate(data.get("mutations", [])):
                mut_required = ["type", "target_file", "target_function", "location_hint",
                               "original_fragment", "mutation_details", "reasoning"]
                missing = [f for f in mut_required if f not in m]
                if missing:
                    print(f"  Warning: Mutation {i} missing fields: {missing}")
                    continue

                mutations.append(MutationInstruction(
                    type=m["type"],
                    target_file=m["target_file"],
                    target_function=m["target_function"],
                    location_hint=m["location_hint"],
                    original_fragment=m["original_fragment"],
                    mutation_details=m["mutation_details"],
                    reasoning=m["reasoning"],
                ))

            if not mutations:
                print("  Warning: No valid mutations found in plan")
                return None

            return MutationPlan(
                strategy=data["strategy"],
                target_symbols=data["target_symbols"],
                mutations=mutations,
                affected_behavior=data["affected_behavior"],
                difficulty=data.get("difficulty", "medium"),
                reasoning_chain=data.get("reasoning_chain", []),
                cross_file_impact=data.get("cross_file_impact", []),
                detection_difficulty=data.get("detection_difficulty", "medium"),
            )

        except json.JSONDecodeError as e:
            print(f"  Warning: JSON decode error: {e}")
            preview = json_str[:200] if len(json_str) > 200 else json_str
            print(f"  JSON preview: {preview}...")
            return None
        except KeyError as e:
            print(f"  Warning: Missing key in mutation plan: {e}")
            return None

    def refine_plan(
        self,
        original_plan: MutationPlan,
        error_message: str,
        context_str: str,
    ) -> Optional[MutationPlan]:
        """Refine a plan that failed to apply."""
        refine_prompt = f"""The previous mutation plan failed to apply.

Original plan:
```json
{original_plan.to_json()}
```

Error: {error_message}

Context:
{context_str}

Please provide a REVISED mutation plan that:
1. Maintains multi-file complexity
2. Fixes the identified issues
3. Uses more precise location hints
4. Ensures the original_fragment exactly matches the source code

Output ONLY the revised JSON mutation plan."""

        try:
            response = completion(
                model=f"openai/{self.model}",
                messages=[
                    {"role": "system", "content": self.SYSTEM_PROMPT},
                    {"role": "user", "content": refine_prompt}
                ],
                temperature=0.4,
                max_tokens=6000,
                api_key=self.api_key,
                base_url=self.api_base,
            )

            content = response.choices[0].message.content
            return self._extract_plan(content)

        except Exception as e:
            print(f"Plan refinement failed: {e}")
            return None
