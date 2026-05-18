#!/usr/bin/env python3
"""
Quality audit for recovered PR mirror patches.
Checks: patch similarity, structure validity, and applicability.
"""

import json
import difflib
from pathlib import Path
from unidiff import PatchSet

DATASET_PATH = "/Users/aditya.singh.001/Desktop/SWE-smith/logs/bug_gen/juspay__hyperswitch.fece9bc3/pr_mirror/recovered_dataset.json"


def patch_similarity(a: str, b: str) -> float:
    """Return similarity ratio between two patch strings (0.0 - 1.0)."""
    return difflib.SequenceMatcher(None, a, b).ratio()


def analyze_patch(patch_text: str) -> dict:
    """Analyze patch structure."""
    result = {"valid": False, "files": 0, "hunks": 0, "additions": 0, "deletions": 0, "error": None}
    if not patch_text or not patch_text.strip():
        result["error"] = "Empty patch"
        return result
    try:
        ps = PatchSet(patch_text)
        result["valid"] = True
        result["files"] = len(ps)
        for pf in ps:
            result["hunks"] += len(pf)
            for hunk in pf:
                result["additions"] += hunk.added
                result["deletions"] += hunk.removed
    except Exception as e:
        result["error"] = str(e)
    return result


def main():
    with open(DATASET_PATH) as f:
        data = json.load(f)

    print(f"Dataset: {len(data)} instances")
    print("=" * 60)

    categories = {"exact": 0, "high": 0, "medium": 0, "low": 0, "empty_patch": 0, "invalid_patch": 0}
    sims = []
    size_diffs = []

    for inst in data:
        iid = inst["instance_id"]
        patch = inst.get("patch", "")
        ref = inst.get("reference_patch", "")

        # Patch analysis
        pa = analyze_patch(patch)
        ra = analyze_patch(ref)

        if not patch.strip():
            categories["empty_patch"] += 1
            continue

        if not pa["valid"]:
            categories["invalid_patch"] += 1
            print(f"INVALID: {iid} — {pa['error']}")
            continue

        # Similarity
        sim = patch_similarity(patch, ref)
        sims.append(sim)

        # Size comparison
        size_diff = abs(len(patch) - len(ref))
        size_diffs.append(size_diff)

        if sim >= 0.99:
            categories["exact"] += 1
        elif sim >= 0.8:
            categories["high"] += 1
        elif sim >= 0.5:
            categories["medium"] += 1
        else:
            categories["low"] += 1
            print(f"LOW SIMILARITY ({sim:.2f}): {iid} | "
                  f"recovered: {pa['files']}f/{pa['hunks']}h/{pa['additions']}+{pa['deletions']}- "
                  f"reference: {ra['files']}f/{ra['hunks']}h/{ra['additions']}+{ra['deletions']}- "
                  f"size_diff={size_diff}b")

    print("=" * 60)
    print("RECOVERY QUALITY SUMMARY")
    print(f"  Exact match (>=0.99):     {categories['exact']}")
    print(f"  High similarity (0.8-1):  {categories['high']}")
    print(f"  Medium similarity (0.5-0.8): {categories['medium']}")
    print(f"  Low similarity (<0.5):    {categories['low']}")
    print(f"  Empty patches:            {categories['empty_patch']}")
    print(f"  Invalid patches:          {categories['invalid_patch']}")
    print()
    print(f"Similarity stats: min={min(sims):.3f}, max={max(sims):.3f}, avg={sum(sims)/len(sims):.3f}, median={sorted(sims)[len(sims)//2]:.3f}")
    print(f"Size diff stats: min={min(size_diffs)}, max={max(size_diffs)}, median={sorted(size_diffs)[len(size_diffs)//2]}")

    # Show worst recoveries
    print()
    print("WORST 10 RECOVERIES:")
    indexed = list(enumerate(sims))
    indexed.sort(key=lambda x: x[1])
    for idx, sim in indexed[:10]:
        inst = data[idx]
        print(f"  {sim:.3f} — {inst['instance_id']} (PR #{inst['pull_number']}): {inst['title'][:80]}")


if __name__ == "__main__":
    main()
