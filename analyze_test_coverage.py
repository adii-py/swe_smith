#!/usr/bin/env python3
"""
Analyze all 97 instances to determine test coverage strategy.
For each instance:
1. Check which files are modified by the patch
2. Find existing tests that import/cover those files
3. Determine if we need to generate test patches
4. Recommend approach to achieve f2p and p2p

EXPLANATION: Why p2p is empty even when tests pass in both versions
===============================================================
In SWE-bench style validation, p2p (pass-to-pass) requires:
1. Tests that PASS in pre-gold (before patch applied)
2. Same tests that PASS in post-gold (after patch applied)
3. Both test runs must COMPLETE and produce parseable output

The current issue: Compilation takes so long that tests TIME OUT before
actually running. No test output = no p2p and no f2p.

Solution approaches:
1. Generate targeted unit tests that compile/run quickly
2. Use smaller, more focused test commands per crate
3. Increase timeouts significantly
4. Pre-compile the project before running validation
"""

import json
import re
from pathlib import Path
from collections import defaultdict
from unidiff import PatchSet

REPO_PATH = Path("/tmp/hyperswitch")
DATASET_PATH = Path("/Users/aditya.singh.001/Desktop/SWE-smith/logs/bug_gen/juspay__hyperswitch.fece9bc3/pr_mirror/recovered_dataset.json")
OUTPUT_PATH = Path("/Users/aditya.singh.001/Desktop/SWE-smith/logs/bug_gen/juspay__hyperswitch.fece9bc3/pr_mirror/test_coverage_analysis.json")

def get_modified_files(patch_text: str) -> list[tuple[str, str]]:
    """Get list of (filepath, file_ext) modified by patch."""
    files = []
    try:
        ps = PatchSet(patch_text)
        for pf in ps:
            ext = Path(pf.path).suffix.lstrip('.')
            files.append((pf.path, ext))
    except Exception as e:
        print(f"  Error parsing patch: {e}")
    return files

def get_crate_from_path(filepath: str) -> str | None:
    """Extract crate name from path like 'crates/router/src/...' -> 'router'."""
    parts = filepath.split('/')
    if len(parts) >= 2 and parts[0] == 'crates':
        return parts[1]
    return None

def get_module_path(filepath: str) -> str:
    """Convert 'crates/router/src/core/payments.rs' to 'router::core::payments'."""
    parts = filepath.replace('.rs', '').split('/')
    if parts[0] == 'crates' and len(parts) >= 3:
        crate = parts[1]
        mod_parts = parts[3:]  # skip crates/X/src/
        return '::'.join([crate] + mod_parts)
    return filepath

def find_test_files_for_crate(crate: str) -> list[Path]:
    """Find all test files for a given crate."""
    crate_path = REPO_PATH / 'crates' / crate
    if not crate_path.exists():
        return []

    test_files = []
    # Look for tests/ subdirectory
    tests_dir = crate_path / 'tests'
    if tests_dir.exists():
        test_files.extend(tests_dir.rglob('*.rs'))

    # Look for #[cfg(test)] modules in src/
    src_dir = crate_path / 'src'
    if src_dir.exists():
        for rs_file in src_dir.rglob('*.rs'):
            content = rs_file.read_text(errors='ignore')
            if '#[cfg(test)]' in content or '#[test]' in content:
                test_files.append(rs_file)

    return test_files

def check_test_covers_module(test_path: Path, module: str) -> bool:
    """Check if a test file likely covers a given module."""
    try:
        content = test_path.read_text(errors='ignore')

        # Direct module reference
        if module in content:
            return True

        # Check for use statements importing from the module
        parts = module.split('::')
        for i in range(len(parts)):
            prefix = '::'.join(parts[:i+1])
            if f'use {prefix}' in content or f'use crate::{prefix}' in content:
                return True
            if f'mod {parts[-1]}' in content:
                return True

        # Check for the filename as indicator
        if parts[-1] in str(test_path):
            return True

    except Exception:
        pass
    return False

def extract_changed_functions(patch_text: str) -> list[str]:
    """Extract function names that were modified in the patch."""
    funcs = []
    try:
        ps = PatchSet(patch_text)
        for pf in ps:
            for hunk in pf:
                for line in hunk:
                    if line.is_added or line.is_removed:
                        # Match fn declarations
                        m = re.search(r'fn\s+(\w+)', line.value)
                        if m and m.group(1) not in funcs:
                            funcs.append(m.group(1))
                        # Match impl blocks (struct/trait methods)
                        m = re.search(r'impl.*\s+(\w+)\s*(?:<.*>)?\s*\{', line.value)
                        if m and m.group(1) not in funcs:
                            funcs.append(m.group(1))
    except Exception:
        pass
    return funcs

def estimate_compilation_time(crate: str) -> tuple[str, int]:
    """Estimate compilation time based on crate size. Returns (label, loc)."""
    crate_path = REPO_PATH / 'crates' / crate
    if not crate_path.exists():
        return "unknown", 0

    # Count lines of code
    loc = 0
    for rs_file in crate_path.rglob('*.rs'):
        try:
            loc += len(rs_file.read_text().splitlines())
        except:
            pass

    if loc < 1000:
        return "fast (< 2 min)", loc
    elif loc < 10000:
        return "medium (2-5 min)", loc
    elif loc < 50000:
        return "slow (5-15 min)", loc
    else:
        return "very slow (15+ min)", loc

def analyze_instance(instance: dict) -> dict:
    """Analyze a single instance and recommend test strategy."""
    iid = instance['instance_id']
    patch = instance.get('patch', '')
    test_cmd = instance.get('test_cmd', '')

    result = {
        'instance_id': iid,
        'title': instance.get('title', ''),
        'pull_number': instance.get('pull_number', ''),
        'test_cmd': test_cmd,
        'modified_files': [],
        'crates_affected': [],
        'changed_functions': [],
        'existing_tests_found': [],
        'test_strategy': '',
        'recommendation': '',
        'estimated_compile_time': '',
        'loc': 0,
        'risk_factors': [],
        'why_p2p_empty': ''
    }

    # Get modified files
    modified = get_modified_files(patch)
    result['modified_files'] = [f[0] for f in modified]

    # Get affected crates
    crates = set()
    for filepath, ext in modified:
        crate = get_crate_from_path(filepath)
        if crate:
            crates.add(crate)
    result['crates_affected'] = list(crates)

    # Estimate compilation time
    total_loc = 0
    max_time_label = "fast"
    for crate in crates:
        label, loc = estimate_compilation_time(crate)
        total_loc += loc
        if 'very slow' in label:
            max_time_label = 'very slow'
        elif 'slow' in label and max_time_label not in ['very slow']:
            max_time_label = 'slow'
        elif 'medium' in label and max_time_label not in ['very slow', 'slow']:
            max_time_label = 'medium'

    result['loc'] = total_loc
    result['estimated_compile_time'] = max_time_label

    # Extract changed functions
    result['changed_functions'] = extract_changed_functions(patch)

    # Find existing tests for each crate
    all_test_files = []
    for crate in crates:
        test_files = find_test_files_for_crate(crate)
        all_test_files.extend(test_files)

    # Check which test files cover the modified modules
    covering_tests = []
    for filepath, _ in modified:
        module = get_module_path(filepath)
        for test_file in all_test_files:
            if check_test_covers_module(test_file, module):
                rel_path = str(test_file.relative_to(REPO_PATH))
                if rel_path not in covering_tests:
                    covering_tests.append(rel_path)

    result['existing_tests_found'] = covering_tests

    # Determine strategy and risk factors
    risks = []
    why_p2p_empty = []

    if not crates:
        result['test_strategy'] = 'UNKNOWN'
        result['recommendation'] = 'Skip or manual review - no Rust files modified'
        result['why_p2p_empty'] = 'No code files to test'
        return result

    # Check for large crates that will timeout
    if 'router' in crates or 'hyperswitch_connectors' in crates:
        risks.append('Large crate - compilation may timeout')
        why_p2p_empty.append('Compilation of router/hyperswitch_connectors exceeds typical timeout (1800s)')

    if total_loc > 50000:
        risks.append('Very large codebase - tests cannot complete in time')
        why_p2p_empty.append(f'Large codebase ({total_loc} LOC) takes too long to compile')

    # Determine test strategy
    if not covering_tests:
        risks.append('No existing tests found for modified modules')
        result['test_strategy'] = 'GENERATE_TEST_PATCH'
        result['recommendation'] = 'Generate targeted unit test using LLM - inject #[cfg(test)] module into modified file'
        why_p2p_empty.append('No existing tests covering this module')
    else:
        if len(covering_tests) <= 2:
            risks.append('Limited test coverage')
            result['test_strategy'] = 'GENERATE_AND_USE_EXISTING'
            result['recommendation'] = 'Use existing tests + generate targeted test for specific function'
        else:
            result['test_strategy'] = 'USE_EXISTING_TESTS'
            result['recommendation'] = 'Use existing tests - but ensure timeout is sufficient for compilation'

        why_p2p_empty.append('Even with existing tests, compilation timeout prevents test execution')

    if max_time_label in ['slow', 'very slow']:
        risks.append('High timeout risk - increase timeout or pre-compile')
        why_p2p_empty.append(f'{max_time_label} compilation prevents tests from running')

    result['risk_factors'] = risks
    result['why_p2p_empty'] = ' | '.join(why_p2p_empty) if why_p2p_empty else 'Tests should pass if compilation completes'

    return result

def print_summary(analysis: list[dict]):
    """Print a summary of the analysis."""
    print("\n" + "="*80)
    print("TEST COVERAGE ANALYSIS SUMMARY")
    print("="*80)

    # Strategy distribution
    strategies = defaultdict(int)
    for a in analysis:
        strategies[a['test_strategy']] += 1

    print(f"\nTotal instances analyzed: {len(analysis)}")
    print("\nStrategy Distribution:")
    for strategy, count in sorted(strategies.items(), key=lambda x: -x[1]):
        pct = count / len(analysis) * 100
        print(f"  {strategy}: {count} ({pct:.1f}%)")

    # Crate distribution
    crate_counts = defaultdict(int)
    for a in analysis:
        for crate in a['crates_affected']:
            crate_counts[crate] += 1

    print("\nCrates Affected:")
    for crate, count in sorted(crate_counts.items(), key=lambda x: -x[1]):
        print(f"  {crate}: {count}")

    # Compilation time distribution
    compile_dist = defaultdict(int)
    for a in analysis:
        compile_dist[a['estimated_compile_time']] += 1

    print("\nEstimated Compilation Time:")
    for time_label, count in sorted(compile_dist.items(), key=lambda x: -x[1]):
        pct = count / len(analysis) * 100
        print(f"  {time_label}: {count} ({pct:.1f}%)")

    # Risk analysis
    high_risk = [a for a in analysis if 'High timeout risk' in ' '.join(a['risk_factors'])]
    no_tests = [a for a in analysis if 'No existing tests' in ' '.join(a['risk_factors'])]
    large_crates = [a for a in analysis if 'router' in a['crates_affected'] or 'hyperswitch_connectors' in a['crates_affected']]

    print(f"\nRisk Analysis:")
    print(f"  High timeout risk (slow/very slow crates): {len(high_risk)}")
    print(f"  No existing test coverage: {len(no_tests)}")
    print(f"  Touch router/hyperswitch_connectors (large): {len(large_crates)}")

    print("\n" + "-"*80)
    print("WHY P2P IS EMPTY (even when tests should pass):")
    print("-"*80)
    print("""
1. COMPILATION TIMEOUT: The router crate takes 15+ minutes to compile.
   With a 1800s (30 min) timeout, there's barely time to compile + run tests.

2. NO TEST OUTPUT: When tests timeout during compilation, no test output
   is produced. Without test output, we cannot determine which tests passed.

3. P2P REQUIREMENTS: For a test to be in p2p, it must:
   - Pass in pre-gold (before patch)
   - Pass in post-gold (after patch)
   - BOTH runs must complete and produce parseable output

4. CURRENT STATE: Most validations are timing out during compilation,
   resulting in empty test output files → empty f2p AND empty p2p.
""")

    print("\n" + "-"*80)
    print("RECOMMENDED SOLUTIONS:")
    print("-"*80)
    print("""
SHORT-TERM (Quick fixes):
1. Increase timeout to 3600s (1 hour) or more for large crates
2. Run validation with --workers 1 to reduce memory pressure
3. Pre-build Docker images with compiled dependencies

MEDIUM-TERM (Better test coverage):
4. Generate targeted unit tests for each instance:
   - Add #[cfg(test)] module to the modified file
   - Test only the specific function that was changed
   - These compile faster than full crate tests

LONG-TERM (Optimal approach):
5. Create minimal test patches that:
   - Import only necessary modules
   - Mock external dependencies
   - Run in < 60 seconds
""")

    print("\n" + "-"*80)
    print("Sample High-Risk Instances (need generated tests):")
    print("-"*80)
    for a in high_risk[:5]:
        print(f"\n{a['instance_id']} (PR #{a['pull_number']}): {a['title'][:60]}")
        print(f"  Crates: {a['crates_affected']}")
        print(f"  Functions: {a['changed_functions'][:3]}")
        print(f"  Compile: {a['estimated_compile_time']} | LoC: {a['loc']}")
        print(f"  Strategy: {a['recommendation'][:80]}")

def main():
    print(f"Loading dataset from {DATASET_PATH}")
    with open(DATASET_PATH) as f:
        data = json.load(f)

    print(f"Analyzing {len(data)} instances...")

    analysis = []
    for i, inst in enumerate(data):
        print(f"  [{i+1}/{len(data)}] Analyzing {inst['instance_id']}...", end='\r')
        result = analyze_instance(inst)
        analysis.append(result)

    print(f"\nCompleted analysis of {len(analysis)} instances")

    # Print summary
    print_summary(analysis)

    # Save detailed analysis
    with open(OUTPUT_PATH, 'w') as f:
        json.dump(analysis, f, indent=2)

    print(f"\n\nDetailed analysis saved to: {OUTPUT_PATH}")

    # Generate lists for different strategies
    need_test_patches = [a for a in analysis if 'GENERATE' in a['test_strategy']]
    high_timeout_risk = [a for a in analysis if 'very slow' in a['estimated_compile_time']]

    print(f"\n\nQUICK STATS:")
    print(f"  Total instances: {len(analysis)}")
    print(f"  Need test patch generation: {len(need_test_patches)}")
    print(f"  High timeout risk: {len(high_timeout_risk)}")
    print(f"  Can use existing tests: {len([a for a in analysis if a['test_strategy'] == 'USE_EXISTING_TESTS'])}")

    # Save instance IDs that need test patches
    test_patch_list = Path("/Users/aditya.singh.001/Desktop/SWE-smith/logs/bug_gen/juspay__hyperswitch.fece9bc3/pr_mirror/need_test_patches.txt")
    with open(test_patch_list, 'w') as f:
        for a in need_test_patches:
            f.write(f"{a['instance_id']}\n")
    print(f"\n  Instance list needing test patches: {test_patch_list}")

if __name__ == "__main__":
    main()
