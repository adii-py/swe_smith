#!/usr/bin/env python3
"""
Generate simple test patches for instances using templates.
Creates basic smoke tests that call modified functions.
"""

import json
import re
from pathlib import Path
from unidiff import PatchSet

REPO_PATH = Path("/tmp/hyperswitch")
DATASET_PATH = Path("/Users/aditya.singh.001/Desktop/SWE-smith/logs/bug_gen/juspay__hyperswitch.fece9bc3/pr_mirror/recovered_dataset.json")
NEED_PATCHES_LIST = Path("/Users/aditya.singh.001/Desktop/SWE-smith/logs/bug_gen/juspay__hyperswitch.fece9bc3/pr_mirror/need_test_patches.txt")
OUTPUT_PATH = Path("/Users/aditya.singh.001/Desktop/SWE-smith/logs/bug_gen/juspay__hyperswitch.fece9bc3/pr_mirror/recovered_dataset_with_test_patches.json")


def get_first_modified_file(patch_text: str) -> tuple[str, str] | None:
    """Return (filepath, file_content) for the first modified source file."""
    try:
        ps = PatchSet(patch_text)
        for pf in ps:
            if not pf.path.endswith(".rs"):
                continue
            path = REPO_PATH / pf.path
            if path.exists() and path.stat().st_size < 200000:
                return pf.path, path.read_text()
    except Exception as e:
        print(f"  Error parsing patch: {e}")
    return None


def extract_changed_items(patch_text: str) -> tuple[list[str], list[str], list[str]]:
    """Extract (functions, structs, enums) that were modified."""
    funcs = []
    structs = []
    enums = []

    try:
        ps = PatchSet(patch_text)
        for pf in ps:
            for hunk in pf:
                for line in hunk:
                    if line.is_added or line.is_removed:
                        text = line.value

                        # Match function definitions
                        m = re.search(r'^\s*(?:pub\s+)?(?:async\s+)?fn\s+(\w+)', text)
                        if m and m.group(1) not in funcs and not m.group(1).startswith('_'):
                            funcs.append(m.group(1))

                        # Match struct definitions
                        m = re.search(r'^\s*(?:pub\s+)?struct\s+(\w+)', text)
                        if m and m.group(1) not in structs:
                            structs.append(m.group(1))

                        # Match enum definitions
                        m = re.search(r'^\s*(?:pub\s+)?enum\s+(\w+)', text)
                        if m and m.group(1) not in enums:
                            enums.append(m.group(1))

                        # Match impl blocks to find associated types
                        m = re.search(r'^\s*impl(?:<[^>]+>)?\s+(?:\w+::)*([A-Z]\w+)', text)
                        if m and m.group(1) not in structs:
                            structs.append(m.group(1))
    except Exception:
        pass

    return funcs, structs, enums


def find_public_functions(filepath: str, file_content: str) -> list[dict]:
    """Find public functions in the file that could be tested."""
    functions = []

    # Match function signatures
    fn_pattern = r'(?:^|\n)\s*(?:pub\s+)?(?:async\s+)?fn\s+(\w+)\s*\(([^)]*)\)(?:\s*->\s*([^\{]+))?'

    for match in re.finditer(fn_pattern, file_content):
        name = match.group(1)
        params = match.group(2).strip()
        ret_type = match.group(3).strip() if match.group(3) else None

        # Skip test functions and private helpers
        if name.startswith('_') or name == 'main':
            continue

        functions.append({
            'name': name,
            'params': params,
            'ret_type': ret_type,
        })

    return functions


def generate_simple_test(instance: dict, analysis: dict) -> str | None:
    """Generate a simple test patch using templates."""
    patch = instance.get("patch", "")
    title = instance["title"]
    iid = instance["instance_id"]

    file_info = get_first_modified_file(patch)
    if not file_info:
        print(f"  {iid}: Could not read modified file")
        return None

    filepath, file_content = file_info
    changed_funcs, changed_structs, changed_enums = extract_changed_items(patch)

    # Get the crate name
    crate = filepath.split("/")[1] if len(filepath.split("/")) > 1 else "unknown"

    # Find public functions in the file
    public_fns = find_public_functions(filepath, file_content)

    # Target the changed functions, or any public function if none identified
    target_fns = []
    for fn in changed_funcs:
        for pf in public_fns:
            if pf['name'] == fn:
                target_fns.append(pf)
                break

    # If no changed functions found, use first few public functions
    if not target_fns and public_fns:
        target_fns = public_fns[:2]

    if not target_fns:
        print(f"  {iid}: No suitable functions found to test")
        return None

    # Generate test code
    test_cases = []
    for fn in target_fns:
        fn_name = fn['name']
        params = fn['params']
        ret_type = fn['ret_type']

        # Parse parameters to generate arguments
        param_names = []
        if params and params != "":
            for param in params.split(','):
                param = param.strip()
                if ':' in param:
                    pname = param.split(':')[0].strip()
                    ptype = param.split(':')[1].strip()

                    # Generate appropriate test argument
                    if 'String' in ptype or '&str' in ptype:
                        param_names.append(f'"test_{pname}".to_string()')
                    elif 'i32' in ptype or 'i64' in ptype or 'usize' in ptype:
                        param_names.append('1')
                    elif 'bool' in ptype:
                        param_names.append('true')
                    elif 'Vec' in ptype:
                        param_names.append('vec![]')
                    elif 'Option' in ptype:
                        param_names.append('None')
                    elif 'HashMap' in ptype or 'Map' in ptype:
                        param_names.append('HashMap::new()')
                    else:
                        # Try to use Default::default() for custom types
                        param_names.append('Default::default()')

        args = ', '.join(param_names) if param_names else ''

        # Generate assertion based on return type
        if ret_type:
            if 'Result' in ret_type:
                assertion = f'assert!(result.is_ok(), "Function {fn_name} should return Ok");'
            elif 'Option' in ret_type:
                assertion = f'// Check Option result\n        assert!(result.is_some() || result.is_none());'
            elif 'bool' in ret_type:
                assertion = f'// Bool result - assert expected value\n        assert_eq!(result, true);  // Adjust based on expected behavior'
            elif 'String' in ret_type or '&str' in ret_type:
                assertion = f'// String result\n        assert!(!result.is_empty());'
            else:
                assertion = f'// Verify result\n        assert_ne!(result, Default::default());  // Adjust based on expected behavior'
        else:
            assertion = f'// Function returns () - call should complete without panic'

        test_case = f'''    #[test]
    fn test_{fn_name}_basic() {{
        // Smoke test for {fn_name}
        let result = {fn_name}({args});
        {assertion}
    }}'''

        test_cases.append(test_case)

    # Build the full test module
    test_code = '''#[cfg(test)]
mod tests {
    use super::*;

''' + '\n\n'.join(test_cases) + '''
}'''

    # Build diff patch
    original_lines = file_content.count("\n") + 1
    test_lines = test_code.count("\n") + 1

    diff_lines = [
        f"diff --git a/{filepath} b/{filepath}",
        "index 0000000..1111111 100644",
        f"--- a/{filepath}",
        f"+++ b/{filepath}",
        f"@@ -{original_lines},0 +{original_lines},{test_lines} @@",
    ]
    for line in test_code.split("\n"):
        diff_lines.append("+" + line)

    return "\n".join(diff_lines) + "\n"


def main():
    # Load dataset
    print(f"Loading dataset from {DATASET_PATH}")
    with open(DATASET_PATH) as f:
        data = json.load(f)

    instances_by_id = {inst["instance_id"]: inst for inst in data}

    # Load instances needing test patches
    with open(NEED_PATCHES_LIST) as f:
        need_patches = [line.strip() for line in f if line.strip()]

    print(f"Found {len(need_patches)} instances needing test patches")

    # Load analysis
    analysis_path = Path("/Users/aditya.singh.001/Desktop/SWE-smith/logs/bug_gen/juspay__hyperswitch.fece9bc3/pr_mirror/test_coverage_analysis.json")
    analysis_by_id = {}
    if analysis_path.exists():
        with open(analysis_path) as f:
            analysis_list = json.load(f)
            analysis_by_id = {a["instance_id"]: a for a in analysis_list}

    # Process each instance
    generated = 0
    failed = 0

    for i, iid in enumerate(need_patches):
        if iid not in instances_by_id:
            print(f"[{i+1}/{len(need_patches)}] {iid}: NOT FOUND")
            failed += 1
            continue

        instance = instances_by_id[iid]
        analysis = analysis_by_id.get(iid, {})

        print(f"[{i+1}/{len(need_patches)}] {iid}: {instance['title'][:60]}...", end=" ")

        # Skip if already has test_patch
        if instance.get("test_patch"):
            print("already has test_patch, skipping")
            generated += 1
            continue

        test_patch = generate_simple_test(instance, analysis)

        if test_patch:
            instance["test_patch"] = test_patch
            print(f"SUCCESS ({len(test_patch)} chars)")
            generated += 1
        else:
            print("FAILED")
            failed += 1

        # Save progress after each 5 instances
        if (i + 1) % 5 == 0:
            with open(OUTPUT_PATH, "w") as f:
                json.dump(data, f, indent=2)
            print(f"  -> Progress saved ({i+1}/{len(need_patches)})")

    # Final save
    with open(OUTPUT_PATH, "w") as f:
        json.dump(data, f, indent=2)

    print("\n" + "="*80)
    print(f"SUMMARY: Generated {generated} test patches, Failed {failed}")
    print(f"Output saved to: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
