#!/usr/bin/env python3
"""
Generate LM rewrite bugs using private-large model from .env
Uses the actual rewrite.py system with proper validation
"""

import os
import sys
import subprocess
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables
load_dotenv(Path(__file__).parent.parent / ".env")

# Set up environment
os.environ["LITELLM_API_KEY"] = os.getenv("LITE_LLM_API_KEY", "")
os.environ["LITELLM_BASE_URL"] = os.getenv("LITE_LLM_URL", "")

REPO = "juspay__hyperswitch.fece9bc3"
CONFIG_FILE = "configs/bug_gen/lm_unified_bugs.yml"
MODEL = "openai/private-large"  # Format for LiteLLM
MAX_BUGS = 50


def main():
    print("=" * 60)
    print("GENERATING LM BUGS WITH PRIVATE-LARGE MODEL")
    print("=" * 60)
    print()
    print(f"Repository: {REPO}")
    print(f"Model: {MODEL}")
    print(f"Max bugs: {MAX_BUGS}")
    print()

    # Check environment
    if not os.getenv("LITELLM_API_KEY"):
        print("ERROR: LITE_LLM_API_KEY not found in .env")
        sys.exit(1)

    print("Environment configured:")
    print(f"  API URL: {os.getenv('LITE_LLM_URL')}")
    print(f"  Model: {MODEL}")
    print()

    # Build command
    cmd = [
        sys.executable,
        "-m",
        "swesmith.bug_gen.llm.rewrite",
        REPO,
        "-c",
        CONFIG_FILE,
        "--model",
        MODEL,
        "-w",
        "4",  # 4 workers
        "-m",
        str(MAX_BUGS),
        "--redo_existing",
    ]

    print("Running command:")
    print(" ".join(cmd))
    print()

    # Run the rewrite script
    result = subprocess.run(cmd, cwd=Path(__file__).parent.parent)

    if result.returncode == 0:
        print()
        print("=" * 60)
        print("BUG GENERATION COMPLETE")
        print("=" * 60)
        print()
        print("Next steps:")
        print("1. Check generated bugs in: logs/bug_gen/juspay__hyperswitch.fece9bc3/")
        print(
            "2. Validate with: python -m swesmith.harness.valid <json_file> --workers 2"
        )
    else:
        print()
        print("=" * 60)
        print("BUG GENERATION FAILED")
        print("=" * 60)
        sys.exit(1)


if __name__ == "__main__":
    main()
