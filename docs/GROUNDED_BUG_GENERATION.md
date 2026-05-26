# Grounded Rust Bug Generation System

A repository-aware bug generation pipeline that addresses LLM hallucination through structured repository analysis.

## Overview

This system generates realistic, compilable Rust bugs by:

1. **Parsing** the repository structure (cargo metadata, AST extraction)
2. **Building** dependency graphs (call graph, import graph, module graph)
3. **Retrieving** strictly limited related context
4. **Generating** bugs with grounded constraints
5. **Validating** patches apply and compile

## Key Features

- **No hallucinated paths**: Only modifies files that exist
- **No invented imports**: Works with existing code only
- **Strict file limits**: Max 4-8 related files per context
- **Automatic validation**: Applies patches, runs cargo check
- **Quality scoring**: Filters low-quality generations

## Installation

```bash
# Install dependencies
pip install networkx litellm pyyaml

# Tree-sitter is optional but recommended
pip install tree-sitter tree-sitter-rust
```

## Usage

### Basic Usage

```bash
python generate_grounded_bugs.py \
    --repo ./juspay__hyperswitch.fece9bc3 \
    --max-bugs 5 \
    --output bugs.json
```

### Advanced Options

```bash
python generate_grounded_bugs.py \
    --repo ./my_project \
    --model private-large \
    --max-bugs 10 \
    --quality-threshold 0.7 \
    --output ./output/my_bugs.json
```

## Architecture

```
swesmith/bug_gen/rust_grounded/
├── parser/              # Repository parsing
│   ├── repository_parser.py   # Cargo metadata, file discovery
│   └── rust_ast.py            # AST extraction
├── graph/               # Dependency graphs
│   └── builder.py       # Call/import/module graphs
├── retrieval/           # Context retrieval
│   └── context_retriever.py   # Strict context packing
├── generator/           # Bug generation
│   └── bug_generator.py       # Grounded LLM prompting
├── validator/           # Patch validation
│   └── patch_validator.py     # Apply, compile, check
├── scorer/              # Quality scoring
│   └── quality_scorer.py      # Multi-dimensional scoring
└── pipeline.py          # Main orchestration
```

## Pipeline Stages

### Stage 1: Repository Parsing

Parses:
- Workspace structure via `cargo metadata`
- All `.rs` files (excluding tests/vendor)
- Function signatures and bodies
- Import/use statements
- Module hierarchy

### Stage 2: Graph Building

Builds:
- **Call Graph**: Function calls between functions
- **Import Graph**: File-to-file dependencies
- **Module Graph**: Module hierarchy

### Stage 3: Context Retrieval

Retrieves ONLY:
- Target function (full body)
- Direct callers (max 2)
- Direct callees (max 2)
- Same-directory files (max 2)
- Test files (if found)

Enforces:
- Max 4-8 related files
- Token budget limits
- Explicit allowed-files list

### Stage 4: Bug Generation

Prompt constraints:
- Only modify allowed files
- No new files
- No import changes
- Unified diff format only
- Exact line numbers required

Bug types:
- Off-by-one errors
- Flipped conditionals
- Missing checks
- Wrong variable usage
- Error handling issues

### Stage 5: Validation

Checks:
1. File existence
2. Patch format (unified diff)
3. Hunk header correctness
4. Clean apply (`git apply`)
5. Compilation (`cargo check`)
6. No new files created

### Stage 6: Quality Scoring

Scores on:
- **Realism** (0-1): Typical developer mistake
- **Complexity** (0-1): Reasoning required
- **Minimality** (0-1): Focused change
- **Testability** (0-1): Detectable behavior change

## Example Output

```json
{
  "instance_id": "hyperswitch.fece9bc.list_all_themes_in_lineage",
  "repo": "juspay/hyperswitch",
  "base_commit": "fece9bc3...",
  "patch": "diff --git a/crates/router/src/routes/user/theme.rs...",
  "test_patch": "diff --git a/crates/router/src/routes/user/theme.rs...",
  "problem_statement": "Changed entity_type parameter handling...",
  "affected_files": [
    "crates/router/src/routes/user/theme.rs"
  ],
  "hints_text": "Look for list_all_themes_in_lineage in crates/router/src/routes/user/theme.rs"
}
```

## Configuration

Set environment variables for LLM:

```bash
export LITE_LLM_API_KEY="your-key"
export LITE_LLM_URL="https://your-endpoint"
```

## Troubleshooting

### Issue: "No patch found in response"

The LLM didn't generate a valid diff. Try:
- Increasing temperature
- Checking model availability
- Verifying prompt format

### Issue: "Patch does not apply cleanly"

Usually caused by:
- Wrong line numbers in hunk headers
- Missing context lines
- File content mismatch

The system attempts to auto-fix hunk headers.

### Issue: "Compilation failed"

Check:
- Generated imports don't exist
- Type mismatches introduced
- Missing trait implementations

### Issue: "Files don't exist"

The LLM hallucinated file paths. The system filters these out.

## Differences from Original Approach

| Aspect | Original | Grounded |
|--------|----------|----------|
| File discovery | Random/guessed | Parsed from repo |
| Related files | Unlimited | Max 4-8 |
| Path validation | None | Strict |
| Import changes | Allowed | Forbidden |
| New files | Sometimes | Never |
| Compilation check | After save | Before accept |
| Quality filtering | None | Automatic |

## Performance

Typical timings for hyperswitch-sized repo:
- Parsing: 10-30s
- Graph building: 5-10s
- Per-bug generation: 30-60s
- Validation: 60-180s (includes cargo check)

## Future Enhancements

- [ ] Tree-sitter integration for better AST parsing
- [ ] Incremental parsing for large repos
- [ ] Parallel bug generation
- [ ] Test case generation improvements
- [ ] Support for workspace dependencies

## License

Same as SWE-Smith project.
