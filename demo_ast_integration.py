"""Demo script showing the AST-enriched problem statement generation pipeline.

Usage:
    python demo_ast_integration.py \
        --dataset data/hyperswitch_validated_dataset.json \
        --instance 0 \
        --config configs/issue_gen/ig_v2_ast.yaml
"""

import argparse
import json
import jinja2
import yaml
from pathlib import Path

from swesmith.issue_gen.ast_enricher import enrich_patch
from swesmith.constants import KEY_PATCH


def build_prompt(config: dict, instance: dict, ast_context: str) -> str:
    """Build the LLM prompt with AST-enriched context."""
    env = jinja2.Environment()
    template = env.from_string(config["instance"])

    return template.render(
        patch=instance.get(KEY_PATCH, ""),
        test_output="",  # Would come from validation logs
        test_funcs=[],    # Would come from get_test_functions
        ast_context=ast_context,
        **config.get("parameters", {}),
    )


def build_old_prompt(config: dict, instance: dict) -> str:
    """Build the old-style prompt (patch only, no AST context)."""
    env = jinja2.Environment()
    instance_template = config["instance"].replace("<semantic_context>\n{{ast_context}}\n</semantic_context>\n\n", "")
    template = env.from_string(instance_template)

    return template.render(
        patch=instance.get(KEY_PATCH, ""),
        test_output="",
        test_funcs=[],
        **config.get("parameters", {}),
    )


def demo_instance(dataset_path: str, instance_idx: int, config_path: str):
    """Run the AST enrichment demo on a single instance."""
    with open(dataset_path) as f:
        data = json.load(f)

    instance = data[instance_idx]
    config = yaml.safe_load(Path(config_path).read_text())

    patch = instance.get(KEY_PATCH, "")
    report = enrich_patch(patch)
    ast_context = report.to_markdown()

    print("=" * 80)
    print(f"INSTANCE: {instance.get('instance_id', 'unknown')}")
    print(f"REPO: {instance.get('repo', 'unknown')}")
    print("=" * 80)

    print("\n--- AST ENRICHMENT SUMMARY ---")
    print(json.dumps(report.summary, indent=2))

    print(f"\n--- AST CONTEXT ({len(ast_context)} chars) ---")
    print(ast_context)

    print("\n" + "=" * 80)
    print("FULL PROMPT (what the LLM sees)")
    print("=" * 80)

    prompt = build_prompt(config, instance, ast_context)
    print(prompt)

    print("\n" + "=" * 80)
    print(f"TOTAL PROMPT LENGTH: {len(prompt)} chars")
    print("=" * 80)

    # Compare with old-style prompt (no AST context)
    old_prompt = build_old_prompt(config, instance)
    print(f"\nOLD-STYLE PROMPT LENGTH (patch only): {len(old_prompt)} chars")
    print(f"AST-ENRICHED PROMPT LENGTH: {len(prompt)} chars")
    print(f"ADDITIONAL CONTEXT ADDED: {len(prompt) - len(old_prompt)} chars")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Demo AST-enriched issue generation")
    parser.add_argument("--dataset", type=str, default="data/hyperswitch_validated_dataset.json")
    parser.add_argument("--instance", type=int, default=0, help="Instance index in dataset")
    parser.add_argument("--config", type=str, default="configs/issue_gen/ig_v2_ast.yaml")
    args = parser.parse_args()

    demo_instance(args.dataset, args.instance, args.config)
