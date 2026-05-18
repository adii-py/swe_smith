#!/usr/bin/env python3
"""
Standalone issue generation for a single instance.
Loads ig_v2.yaml config, constructs prompt from patch + test output,
and calls the LLM to generate a problem statement.
"""
import json
import os
import random
import sys
import yaml

from dotenv import load_dotenv
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from litellm import completion, completion_cost
from litellm.utils import get_token_count

try:
    from portkey_ai import Portkey
except ImportError:
    Portkey = None

load_dotenv()

# Configure litellm for custom endpoint if LITE_LLM_* vars are set
if os.getenv("LITE_LLM_URL") and not os.getenv("OPENAI_API_BASE"):
    os.environ["OPENAI_API_BASE"] = os.getenv("LITE_LLM_URL")
if os.getenv("LITE_LLM_API_KEY") and not os.getenv("OPENAI_API_KEY"):
    os.environ["OPENAI_API_KEY"] = os.getenv("LITE_LLM_API_KEY")

# Paths
DATASET_PATH = Path("data/hyperswitch_validated_dataset.json")
CONFIG_PATH = Path("configs/issue_gen/ig_v2.yaml")
TEST_OUTPUT_PATH = Path("logs/run_validation/juspay__hyperswitch.fece9bc3/juspay__hyperswitch.fece9bc3.pr_12234/test_output.txt")
OUTPUT_PATH = Path("logs/issue_gen/hyperswitch/juspay__hyperswitch.fece9bc3.pr_12234.json")

# SWE-bench Verified dataset for demo issues
from datasets import load_dataset

class PortkeyModel:
    """Simple Portkey model wrapper matching the generate.py implementation."""
    def __init__(self, model_name: str, provider: str = "openai", litellm_model_name_override: str = ""):
        if Portkey is None:
            raise ImportError("portkey-ai package required. Install with: pip install portkey-ai")

        self.model_name = model_name
        self.provider = provider
        self.litellm_model_name_override = litellm_model_name_override
        self.cost = 0.0
        self.n_calls = 0

        self._api_key = os.getenv("PORTKEY_API_KEY")
        if not self._api_key:
            raise ValueError("PORTKEY_API_KEY environment variable required")

        virtual_key = os.getenv("PORTKEY_VIRTUAL_KEY")
        client_kwargs = {"api_key": self._api_key}
        if virtual_key:
            client_kwargs["virtual_key"] = virtual_key
        elif provider:
            client_kwargs["provider"] = provider

        self.client = Portkey(**client_kwargs)

    def query(self, messages: list[dict], n: int = 1, stream: bool = False, **kwargs):
        self.n_calls += 1
        return self.client.chat.completions.create(
            model=self.model_name,
            messages=[{"role": msg["role"], "content": msg["content"]} for msg in messages],
            n=n,
            stream=stream,
            **kwargs,
        )


def maybe_shorten(text_str: str, max_tokens: int, model: str) -> str:
    """Shorten text if it exceeds max_tokens limit."""
    if get_token_count([{"content": text_str}], model) < max_tokens:
        return text_str
    half = max_tokens // 2
    return text_str[:half] + "\n\n(...)\n\n" + text_str[-half:]


def format_prompt(prompt: str | None, config: dict, candidate: dict) -> str:
    """Render a Jinja2 template with the given variables."""
    import jinja2
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


def main():
    # Load config
    config = yaml.safe_load(CONFIG_PATH.read_text())
    model = config.get("model", "openai/gpt-4o")
    settings = config.get("settings", {})
    n_instructions = settings.get("n_instructions", 1)
    max_var_tokens = settings.get("max_var_tokens", 10_000)

    # Override model from environment if LITE_LLM_MODEL is set (project convention)
    env_model = os.getenv("LITE_LLM_MODEL")
    if env_model:
        model = env_model
        print(f"Using model from LITE_LLM_MODEL: {model}")

    # Convert portkey/ models to openai/ format for litellm
    if model.startswith("portkey/"):
        model = model.replace("portkey/", "openai/")
        print(f"Converted portkey model to: {model}")

    # Ensure model has a provider prefix for litellm
    if "/" not in model:
        model = f"openai/{model}"
        print(f"Added openai/ provider prefix: {model}")

    # Load dataset
    dataset = json.loads(DATASET_PATH.read_text())
    instance = dataset[0]

    # Get test output
    test_output = TEST_OUTPUT_PATH.read_text()
    TEST_OUTPUT_START = ">>>>> Start Test Output"
    TEST_OUTPUT_END = ">>>>> End Test Output"
    start_idx = test_output.find(TEST_OUTPUT_START) + len(TEST_OUTPUT_START)
    end_idx = test_output.find(TEST_OUTPUT_END)
    test_output_trimmed = test_output[start_idx:end_idx].strip()
    test_output_shortened = maybe_shorten(test_output_trimmed, max_var_tokens, model)

    # Get demo issues from SWE-bench Verified
    print("Loading SWE-bench_Verified dataset for demo issues...")
    swebv = load_dataset("princeton-nlp/SWE-bench_Verified", split="test")
    problem_statements = [
        s[:2000] for s in [inst["problem_statement"] for inst in swebv]
    ]
    random.shuffle(problem_statements)
    demo_problem_statements = problem_statements[:5]

    # Build test source code from test_patch (since Rust tests can't be extracted via Python AST)
    # Extract the test functions from the test patch
    test_patch = instance.get("test_patch", "")
    # Parse the test patch to extract the test functions
    test_funcs = []
    if test_patch:
        # Extract function bodies from the patch
        in_func = False
        func_lines = []
        for line in test_patch.splitlines():
            if line.strip().startswith("fn test_") or line.strip().startswith("+    fn test_"):
                in_func = True
                func_lines = [line.lstrip("+").lstrip() if line.startswith("+") else line]
            elif in_func:
                if line.strip() == "" or (line.startswith("+") and line.lstrip("+").strip() == ""):
                    func_lines.append(line.lstrip("+").rstrip() if line.startswith("+") else line.rstrip())
                elif line.startswith("+    }") or line.strip() == "}":
                    func_lines.append(line.lstrip("+").rstrip() if line.startswith("+") else line.rstrip())
                    test_funcs.append("\n".join(func_lines))
                    in_func = False
                    func_lines = []
                else:
                    func_lines.append(line.lstrip("+").rstrip() if line.startswith("+") else line.rstrip())

    if not test_funcs:
        # Fallback: just include the raw test patch
        test_funcs = [test_patch]

    print(f"Found {len(test_funcs)} test functions")

    # Build messages
    messages = [
        {"content": config["system"], "role": "system"},
    ]

    if config.get("demonstration"):
        messages.append({
            "content": format_prompt(
                config["demonstration"],
                config,
                {"demo_problem_statements": demo_problem_statements},
            ),
            "role": "user",
        })

    messages.append({
        "content": format_prompt(
            config["instance"],
            config,
            instance | {
                "test_output": test_output_shortened,
                "test_funcs": test_funcs,
            },
        ),
        "role": "user",
    })

    # Save messages for debugging
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    print(f"\nCalling model: {model}")
    print(f"Number of instructions: {n_instructions}")

    # Call LLM
    if model.startswith("portkey/"):
        actual_model = model.replace("portkey/", "")
        provider = config.get("provider", "openai")
        if provider.startswith("@"):
            provider = provider[1:]
        portkey_model = PortkeyModel(
            model_name=actual_model,
            provider=provider,
            litellm_model_name_override=config.get("litellm_model_name_override", ""),
        )
        response = portkey_model.query(messages, n=n_instructions)
        model_for_cost = config.get("litellm_model_name_override", model)
    else:
        response = completion(
            model=model,
            messages=messages,
            n=n_instructions,
            temperature=0.7,
        )
        model_for_cost = model

    # Extract problem statements
    problem_statements = []
    for choice in response.choices:
        ps = choice.message.content.strip()
        problem_statements.append(ps)

    # Compute cost (may fail for unknown models, so wrap in try-except)
    try:
        cost = completion_cost(response, model=model_for_cost)
    except Exception as e:
        print(f"Warning: Could not compute cost: {e}")
        cost = 0.0

    # Save output
    metadata = {
        "messages": messages,
        "responses": {model: problem_statements},
        "cost": cost,
    }
    with open(OUTPUT_PATH, "w") as f:
        json.dump(metadata, f, indent=2)

    print(f"\nGenerated {len(problem_statements)} problem statement(s)")
    print(f"Cost: ${cost:.4f}")
    print(f"Saved to: {OUTPUT_PATH}")

    print("\n" + "=" * 60)
    print("PROBLEM STATEMENT")
    print("=" * 60)
    print(problem_statements[0])
    print("=" * 60)

    # Also update the dataset file with the problem statement
    instance["problem_statement"] = problem_statements[0]
    with open(DATASET_PATH, "w") as f:
        json.dump(dataset, f, indent=2)
    print(f"\nUpdated dataset with problem_statement: {DATASET_PATH}")


if __name__ == "__main__":
    main()
