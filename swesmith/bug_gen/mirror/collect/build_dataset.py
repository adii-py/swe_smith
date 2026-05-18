#!/usr/bin/env python3

import argparse
import json
import logging
import os
import re
from typing import Optional, Literal

from swesmith.bug_gen.mirror.collect.utils import (
    extract_patches,
    extract_problem_statement_and_hints,
    Repo,
)

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


# Complexity indicators for scoring PRs
COMPLEXITY_KEYWORDS = {
    # Hard/Expert indicators
    "race": 3, "deadlock": 4, "concurrent": 3, "thread": 2, "mutex": 3,
    "lock": 2, "async": 1, "await": 1, "parallel": 2, "synchronize": 3,
    "memory_leak": 4, "buffer_overflow": 4, "segfault": 3, "crash": 2,
    "corruption": 3, "invariant": 3, "atomic": 2, "ordering": 2,
    "timing": 3, "scheduler": 2, "distributed": 2, "consistency": 2,
    # Expert/Architecture indicators
    "refactor": 2, "architecture": 3, "redesign": 3, "breaking_change": 4,
    "backward_compatible": 3, "api_change": 2, "protocol": 2,
}


def get_complexity_score(pull: dict) -> tuple[int, str]:
    """
    Calculate complexity score for a PR based on multiple heuristics.

    Returns:
        tuple of (score, level) where level is 'simple', 'medium', 'hard', or 'expert'
    """
    score = 0

    # Factor 1: Number of files changed (0-10 points)
    files_changed = pull.get("changed_files", 0)
    score += min(files_changed, 5) * 2

    # Factor 2: Lines changed (0-10 points)
    additions = pull.get("additions", 0)
    deletions = pull.get("deletions", 0)
    score += min((additions + deletions) // 50, 10)

    # Factor 3: Complexity keywords in title/body (0-15 points)
    title = pull.get("title", "") or ""
    body = pull.get("body", "") or ""
    # Handle case where body might be a dict
    if isinstance(body, dict):
        body = body.get("content", "") or ""
    text = (title + " " + body).lower()
    keyword_score = 0
    for keyword, points in COMPLEXITY_KEYWORDS.items():
        if keyword.replace("_", " ") in text or keyword in text:
            keyword_score += points
    score += min(keyword_score, 15)

    # Factor 4: Review comments (indicates discussion/complexity)
    review_comments = pull.get("review_comments", 0)
    if review_comments >= 5:
        score += 2

    # Determine level
    if score >= 20:
        level = "expert"
    elif score >= 10:
        level = "hard"
    elif score >= 5:
        level = "medium"
    else:
        level = "simple"

    return score, level


def create_instance(repo: Repo, pull: dict) -> dict:
    """
    Create a single task instance from a pull request, where task instance is:

    {
        repo (str): owner/repo this task instance is from,
        pull_number (int): number of PR this task instance is from,
        base_commit (str): SHA of the base commit PR is based on,
        patch (str): reference solution as .patch (apply to base commit),
        test_patch (str): test suite as .patch (apply to base commit),
    }
    """
    patch, test_patch = extract_patches(pull, repo)
    problem_statement, hints = extract_problem_statement_and_hints(pull, repo)
    return {
        "repo": repo.repo.full_name,
        "pull_number": pull["number"],
        "instance_id": (repo.repo.full_name + "-" + str(pull["number"])).replace(
            "/", "__"
        ),
        "issue_numbers": pull["resolved_issues"],
        "base_commit": pull["base"]["sha"],
        "patch": patch,
        "test_patch": test_patch,
        "problem_statement": problem_statement,
        "hints_text": hints,
        "created_at": pull["created_at"],
    }


def is_valid_pull(pull: dict) -> bool:
    """
    Check whether PR has an associated issue and is merged

    Args:
        pull (dict): pull request object
    Returns:
        bool: whether PR is valid
    """
    if pull["merged_at"] is None:
        return False
    return True


def is_valid_instance(instance: dict) -> bool:
    """
    Check whether task instance has all required fields for task instance creation

    Args:
        instance (dict): task instance object
    Returns:
        bool: whether task instance is valid
    """
    if instance["patch"] is None or instance["patch"] == "":
        return False
    return True


def has_test_patch(instance: dict) -> bool:
    """
    Check whether task instance has a test suite

    Args:
        instance (dict): task instance object
    Returns:
        bool: whether task instance has a test suite
    """
    if instance["test_patch"] is None or instance["test_patch"].strip() == "":
        return False
    return True


def main(
    pr_file: str,
    output: str,
    token: Optional[str] = None,
    complexity_levels: Optional[list[str]] = None,
    min_files_changed: int = 0,
):
    """
    Main thread for creating task instances from pull requests

    Args:
        pr_file (str): path to pull request JSONL file
        output (str): output file name
        token (str): GitHub token
        complexity_levels (list): filter by complexity levels (simple/medium/hard/expert)
        min_files_changed (int): minimum number of files changed
    """
    if token is None:
        # Get GitHub token from environment variable if not provided
        token = os.environ.get("GITHUB_TOKEN")

    def load_repo(repo_name):
        # Return repo object for a given repo name
        owner, repo = repo_name.split("/")
        return Repo(owner, repo, token=token)

    repos = dict()
    completed = 0
    with_tests = 0
    total_instances = 0
    seen_prs = set()

    # Continue where we left off if output file already exists
    if os.path.exists(output):
        with open(output) as f:
            for line in f:
                pr = json.loads(line)
                if "instance_id" not in pr:
                    pr["instance_id"] = (
                        pr["repo"] + "-" + str(pr["pull_number"])
                    ).replace("/", "__")
                instance_id = pr["instance_id"]
                seen_prs.add(instance_id)
                if is_valid_instance(pr):
                    completed += 1
                    if has_test_patch(pr):
                        with_tests += 1
    logger.info(
        f"Will skip {len(seen_prs)} pull requests that have already been inspected"
    )
    if complexity_levels:
        logger.info(f"Filtering for complexity levels: {complexity_levels}")
    if min_files_changed > 0:
        logger.info(f"Minimum files changed required: {min_files_changed}")

    # Stats by complexity
    complexity_counts = {"simple": 0, "medium": 0, "hard": 0, "expert": 0}

    # Write to output file for PRs with test suites
    write_mode = "w" if not os.path.exists(output) else "a"
    with open(output, write_mode) as output:
        for ix, line in enumerate(open(pr_file)):
            total_instances += 1
            pull = json.loads(line)
            if ix % 100 == 0:
                logger.info(
                    f"[{pull['base']['repo']['full_name']}] (Up to {ix} checked) "
                    f"{completed} valid, {with_tests} with tests."
                )
            # Construct instance fields
            instance_id = pull["base"]["repo"]["full_name"] + "-" + str(pull["number"])
            instance_id = instance_id.replace("/", "__")
            if instance_id in seen_prs:
                seen_prs -= {instance_id}
                continue
            if not is_valid_pull(pull):
                # Throw out invalid PRs
                continue

            # Calculate complexity and filter
            complexity_score, complexity_level = get_complexity_score(pull)
            complexity_counts[complexity_level] += 1

            # Filter by complexity level if specified
            if complexity_levels and complexity_level not in complexity_levels:
                continue

            # Filter by minimum files changed
            if pull.get("changed_files", 0) < min_files_changed:
                continue

            # Create task instance
            repo_name = pull["base"]["repo"]["full_name"]
            if repo_name not in repos:
                repos[repo_name] = load_repo(repo_name)
            repo = repos[repo_name]
            instance = create_instance(repo, pull)
            from time import sleep

            sleep(
                60
            )  # TODO(john-b-yang) is there something better than this (to avoid timeouts by GitHub)
            if is_valid_instance(instance):
                # If valid, write to .all output file
                print(
                    json.dumps(instance), end="\n", flush=True, file=output
                )  # write all instances to a separate file
                completed += 1
                if has_test_patch(instance):
                    # If has test suite, write to output file
                    with_tests += 1
    logger.info(
        f"[{', '.join(repos.keys())}] Total instances: {total_instances}, completed: {completed}, with tests: {with_tests}"
    )
    logger.info(
        f"Complexity distribution: simple={complexity_counts['simple']}, "
        f"medium={complexity_counts['medium']}, hard={complexity_counts['hard']}, "
        f"expert={complexity_counts['expert']}"
    )
    logger.info(
        f"[{', '.join(repos.keys())}] Skipped {len(seen_prs)} pull requests that have already been inspected"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("pr_file", type=str, help="Path to pull request JSONL file")
    parser.add_argument("output", type=str, help="Output file name")
    parser.add_argument("--token", type=str, help="GitHub token")
    parser.add_argument(
        "--complexity",
        type=str,
        nargs="+",
        choices=["simple", "medium", "hard", "expert"],
        help="Filter by complexity level(s). Can specify multiple: --complexity medium hard",
    )
    parser.add_argument(
        "--min-files-changed",
        type=int,
        default=0,
        help="Minimum number of files changed (default: 0)",
    )
    args = parser.parse_args()
    main(
        pr_file=args.pr_file,
        output=args.output,
        token=args.token,
        complexity_levels=args.complexity,
        min_files_changed=args.min_files_changed,
    )
