#!/usr/bin/env python3
"""
CLI for grounded Rust bug generation.

Usage:
    python generate_grounded_bugs.py --repo ./juspay__hyperswitch.fece9bc3 --max-bugs 5
"""

import argparse
import sys
from pathlib import Path

# Add project to path
sys.path.insert(0, str(Path(__file__).parent))

from swesmith.bug_gen.rust_grounded.pipeline import GroundedBugPipeline


def main():
    parser = argparse.ArgumentParser(
        description="Generate grounded, compilable Rust bugs for SWE-Smith",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Generate 5 bugs for a repo
  %(prog)s --repo ./my_rust_project --max-bugs 5

  # Use specific model
  %(prog)s --repo ./my_rust_project --model claude-sonnet-4-5-20251001 --max-bugs 10

  # Save to specific output
  %(prog)s --repo ./my_rust_project --output ./my_bugs.json
        """
    )

    parser.add_argument(
        "--repo",
        type=str,
        required=True,
        help="Path to Rust repository",
    )

    parser.add_argument(
        "--model",
        type=str,
        default="private-large",
        help="LLM model to use (default: private-large)",
    )

    parser.add_argument(
        "--max-bugs",
        type=int,
        default=10,
        help="Maximum number of bugs to generate (default: 10)",
    )

    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Output JSON file for generated bugs",
    )

    parser.add_argument(
        "--quality-threshold",
        type=float,
        default=0.6,
        help="Minimum quality score to accept bug (0-1, default: 0.6)",
    )

    args = parser.parse_args()

    # Validate repo path
    repo_path = Path(args.repo)
    if not repo_path.exists():
        print(f"Error: Repository not found: {args.repo}")
        sys.exit(1)

    cargo_toml = repo_path / "Cargo.toml"
    if not cargo_toml.exists():
        print(f"Warning: No Cargo.toml found in {args.repo}")
        print("This may not be a valid Rust repository.")

    # Run pipeline
    print(f"Generating grounded bugs for: {args.repo}")
    print(f"Model: {args.model}")
    print(f"Max bugs: {args.max_bugs}")
    print()

    try:
        pipeline = GroundedBugPipeline(
            repo_path=str(repo_path),
            model=args.model,
            max_bugs=args.max_bugs,
        )

        bugs = pipeline.run()

        print(f"\n✓ Generated {len(bugs)} valid bugs")

        if args.output:
            import json
            with open(args.output, "w") as f:
                json.dump(bugs, f, indent=2)
            print(f"✓ Saved to {args.output}")

    except KeyboardInterrupt:
        print("\n\nInterrupted by user")
        sys.exit(0)

    except Exception as e:
        print(f"\n✗ Error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
