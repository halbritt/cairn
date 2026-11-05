# Cairn Documentation Testing Infrastructure (`TEST_INFRA.md`)

## 1. Overview & Purpose

The Cairn documentation testing infrastructure provides automated, multi-tier verification for all documentation files across the repository (`README.md`, `AGENTS.md`, `CHANGELOG.md`, `GLOSSARY.md`, and all documents in `docs/`).

Its purpose is to enforce documentation integrity, correctness, and freshness:
- Ensure 100% of internal links, images, and anchor targets resolve accurately.
- Guarantee that all documented CLI commands, subcommands, arguments, and configuration flags strictly match the Go codebase implementation.
- Validate that code blocks (JSON payloads, shell commands) are syntactically valid and executable.
- Enforce the complete production documentation architecture across User Guides, Developer Guides, Operator Guides, MCP Guides, and Reference manuals.

The test suite is implemented in Python 3 (`scripts/verify_docs.py`) using only standard libraries, requiring no external packages or virtual environments.

---

## 2. Multi-Tier Architecture

The verification suite is organized into four hierarchical verification tiers:

| Tier | Name | Target Scope | Authoritative Source of Truth |
|------|------|--------------|-------------------------------|
| **Tier 1** | Link & Anchor Integrity | All repository `.md` files | Local filesystem & document AST headings |
| **Tier 2** | CLI Command & Flag Alignment | `README.md`, `docs/**/*.md` | `cmd/cairn/*.go` & `localapi/*.go` |
| **Tier 3** | Code Block Syntax | Fenced code blocks (`json`, `sh`, `bash`) | RFC 8259 JSON & bash AST parser |
| **Tier 4** | Document Architecture & Completeness | Architecture suites | `PROJECT.md` documentation blueprint |

---

## 3. Tier Specifications

### Tier 1: Link & Anchor Integrity
- **Objective**: Prevent 404 dead links, dangling references, and broken heading anchors across all documentation.
- **Mechanics**:
  1. Recursively discovers all markdown files outside excluded directories (`.git`, `.agents`, `vendor`, `node_modules`, `bin`).
  2. Parses inline markdown links `[text](target)` and reference link definitions `[id]: target`.
  3. Ignores external schemes (`https://`, `http://`, `mailto:`, `ftp:`).
  4. Resolves relative targets against the parent directory of the source document and against repository root.
  5. Extracts heading anchors using standard GitHub markdown slugification (lowercase, strip formatting and punctuation, convert whitespace to hyphens).
  6. Recognizes explicit HTML anchors (`<a id="...">`, `<a name="...">`) and extended markdown syntax `{#custom-anchor}`.
  7. Validates that the target file exists, and if an anchor is specified (`#heading`), verifies that the anchor exists in the destination document.

### Tier 2: CLI Command & Argument Alignment
- **Objective**: Prevent documentation drift where CLI syntax examples fail to execute on the current Cairn binary.
- **Mechanics**:
  1. Dynamically discovers CLI ground truth from `cmd/cairn/*.go` and `localapi/*.go`:
     - 113 root commands and aliases.
     - 88 declarative `localapi` operations exposed via `cairn agent OPERATION`.
     - 127 flag definitions categorized into boolean flags and value-taking flags.
  2. Extracts all documented command invocations (`cairn ...`, `bin/cairn ...`, `go run ./cmd/cairn ...`).
  3. Tokenizes invocations while properly binding value arguments to preceding value-taking flags (e.g. `--token-file PATH`).
  4. Enforces command-specific positional argument constraints:
     - Detects `cairn agent pull` invocations omitting required `RECEIPT_UUID HANDLE_UUID` arguments.
  5. Validates that every documented flag matches registered flag sets in the Go source code.

### Tier 3: Markdown Code Block Syntax
- **Objective**: Ensure that code blocks provided to users and automated agents are syntactically well-formed.
- **Mechanics**:
  1. Scans every fenced code block (` ```lang ... ``` `).
  2. For `json` blocks: validates complete JSON payloads using `json.loads`. Flags syntax errors such as missing braces, trailing commas, or unquoted identifiers.
  3. For `sh`, `bash`, `shell`, `zsh` blocks: validates shell grammar using `bash -n` to catch unclosed quotes, broken heredocs, or invalid shell syntax.

### Tier 4: Document Completeness & Architecture
- **Objective**: Enforce the presence of all required documentation suites defined in the project architecture.
- **Mechanics**:
  1. Checks for the existence of required files across 7 documented suites:
     - `root`: `README.md`, `AGENTS.md`, `CHANGELOG.md`, `GLOSSARY.md`
     - `quickstart`: `docs/quickstart.md`
     - `guides`: `docs/guides/{memory-search,memory-capture,session-coordination,agent-workflows}.md`
     - `developer`: `docs/developer/{architecture,testing,contributing}.md`
     - `operator`: `docs/operator/{deployment,configuration,maintenance,daemon-deployment}.md`
     - `mcp`: `docs/mcp/{overview,claude-code,codex,opencode,agy-hermes}.md`
     - `reference`: `docs/reference/{cli,mcp-tools,api-rest,lifecycle-slices}.md`
  2. Enforces substantive content verification (verifies documents are not empty stubs and contain structured headings).

---

## 4. Test Execution & CLI Usage

The test runner is standalone and can be run from the repository root or within `scripts/`:

```bash
# Standard execution (all tiers, strict exit code 1 on any failure)
python3 scripts/verify_docs.py

# Non-strict execution (prints full report, exits 0)
python3 scripts/verify_docs.py --no-strict

# Run specific tier(s)
python3 scripts/verify_docs.py --tier 1
python3 scripts/verify_docs.py --tier 1,2

# Run milestone-scoped checks
python3 scripts/verify_docs.py --milestone M1
python3 scripts/verify_docs.py --milestone M2
python3 scripts/verify_docs.py --milestone M3
python3 scripts/verify_docs.py --milestone M4

# Output machine-readable JSON results
python3 scripts/verify_docs.py --json

# Verbose output (detailed traces of all verified links and blocks)
python3 scripts/verify_docs.py -v
```

### Exit Codes
- `0`: All executed tiers passed without violations.
- `1`: One or more violations detected in the executed tiers (when `--strict` is enabled, which is default).

---

## 5. Progressive Milestone Alignment

The test suite is designed for progressive testability across the documentation overhaul milestones:

| Milestone | Target Scope | Expected State | Validation Command |
|-----------|--------------|----------------|--------------------|
| **M1** | Stale Document Excision & Link Cleanup | Tier 1 passes (0 broken links); Tier 3 invalid JSONs eliminated | `python3 scripts/verify_docs.py --tier 1` |
| **M2** | Core Documentation & CLI Help Alignment | Tier 2 passes (README syntax fixed, CLI help aligned) | `python3 scripts/verify_docs.py --tier 1,2` |
| **M3** | Comprehensive User & Operator Docs | Tier 4 passes (25/25 docs present & substantive) | `python3 scripts/verify_docs.py --tier 4` |
| **M4** | Final Verification Gate | All 4 Tiers pass 100% green | `python3 scripts/verify_docs.py` |

---

## 6. Maintenance & Extension Guide

1. **Adding New CLI Commands or Flags**:
   - Register the flags in `cmd/cairn/*.go` or operations in `localapi/*.go`. The test runner automatically scans these files on each run.
2. **Adding New Documentation Files**:
   - Add the relative path to `REQUIRED_DOC_SUITES` in `scripts/verify_docs.py` if the file is part of a required architecture suite.
   - Ensure all internal links use relative paths with lowercase slug anchors.
3. **Validating Code Snippets**:
   - When writing JSON snippets in docs, ensure valid JSON syntax or mark non-JSON explanatory snippets as text or shell.
