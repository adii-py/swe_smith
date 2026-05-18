#!/usr/bin/env python3
"""Filter dataset to remove low-recovery instances."""

import json
import difflib

INPUT_PATH = "/Users/aditya.singh.001/Desktop/SWE-smith/logs/bug_gen/juspay__hyperswitch.fece9bc3/pr_mirror/recovered_dataset.json"
OUTPUT_PATH = "/Users/aditya.singh.001/Desktop/SWE-smith/logs/bug_gen/juspay__hyperswitch.fece9bc3/pr_mirror/recovered_dataset_clean.json"
SIM_THRESHOLD = 0.5

with open(INPUT_PATH) as f:
    data = json.load(f)

def sim(a, b):
    return difflib.SequenceMatcher(None, a, b).ratio()

kept, removed = [], []
for inst in data:
    s = sim(inst.get("patch", ""), inst.get("reference_patch", ""))
    if s >= SIM_THRESHOLD:
        kept.append(inst)
    else:
        removed.append((inst["instance_id"], inst["pull_number"], s, inst["title"]))

print(f"Removed {len(removed)} low-recovery instances (< {SIM_THRESHOLD} similarity):")
for iid, pr, s, title in removed:
    print(f"  {iid} (PR #{pr}, sim={s:.3f}): {title[:70]}")

print(f"\nKept {len(kept)} / {len(data)} instances")

with open(OUTPUT_PATH, "w") as f:
    json.dump(kept, f, indent=2)

print(f"Saved clean dataset to: {OUTPUT_PATH}")
