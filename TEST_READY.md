# Documentation Test Suite Readiness Report (`TEST_READY.md`)

**Date**: 2026-11-05  
**Component**: E2E Documentation Verification Suite  
**Test Harness**: `scripts/verify_docs.py`  
**Infrastructure Specification**: `TEST_INFRA.md`  
**Status**: **TEST SUITE READY** (Baseline captured, continuous verification active)

---

## 1. Executive Summary

The automated E2E Documentation Verification Suite (`scripts/verify_docs.py`) has been authored, verified, and baseline-tested across all 507 documentation files in the repository.

The suite provides automated, multi-tier testing:
- **Tier 1 (Link & Anchor Integrity)**: Evaluated 2,055 internal links. Identified exactly 9 broken links/anchor mismatches matching the initial survey ledger.
- **Tier 2 (CLI Command & Argument Alignment)**: Evaluated 419 documented invocations against 113 Go commands and 127 flag definitions. Detected known syntax drift (e.g. `cairn agent pull` positional argument omissions).
- **Tier 3 (Code Block Syntax)**: Evaluated 367 code blocks. Detected 2 malformed JSON blocks located inside obsolete trial files scheduled for excision in Milestone 1.
- **Tier 4 (Document Architecture & Completeness)**: Checked 25 required architectural documents across 7 functional suites. Documented 21 missing guides scheduled for authoring in Milestone 3.

---

## 2. Baseline Validation Results

Execution Command: `python3 scripts/verify_docs.py --no-strict`

```
==============================================================================
Tier     Test Suite Name                        Checks             Status    
------------------------------------------------------------------------------
Tier 1   Link & Anchor Integrity                2055 links         FAILED    
Tier 2   CLI Command & Argument Alignment       419 invocations    FAILED    
Tier 3   Markdown Code Block Syntax             367 blocks         FAILED    
Tier 4   Document Completeness & Architecture   4/25 docs          FAILED    
==============================================================================
```

### Detailed Baseline Failure Ledger

#### Tier 1: Broken Links & Anchor Mismatches (9 items)
1. `docs/implementation-status.md:326`: Anchor `#shared-rfc-adoption-check--2026-09-10` not found in `docs/native-striatum-context.md` (double hyphen mismatch).
2. `docs/implementation-status.md:359`: Anchor `#2026-09-10--separate-ordinary-tools-from-harness-launch-instructions` not found in `docs/verification/procedure-maintenance-2026-09-09.md` (double hyphen mismatch).
3. `docs/implementation-status.md:3683`: Identical target anchor mismatch as line 359.
4. `docs/implementation-status.md:4373`: Identical target anchor mismatch as line 326.
5. `docs/roadmap.md:361`: Identical target anchor mismatch as line 359.
6. `docs/roadmap.md:457`: Identical target anchor mismatch as line 326.
7. `docs/sources/agent-memory/design/agent-memory-design.md:23`: Target file `docs/bonded-memory.html` does not exist.
8. `docs/sources/agent-memory/design/agent-memory-design.md:1063`: Target file `docs/bonded-memory.html` does not exist.
9. `docs/verification/coordination-review-2026-09-20.md:9`: Anchor `#corrective-bridge-review-at-69f6a95--131e95b` not found in same file.

#### Tier 2: CLI Syntax & Flag Discrepancies
- `README.md:68`: `cairn agent pull --offset N --length N` omits mandatory positional `RECEIPT_UUID HANDLE_UUID`.
- `docs/client-observations.md:14`: Unparsed option syntax `[--socket PATH]`.
- Obsolete or unregistered root commands in legacy notes: `home` (`currentness-and-replay.md`, `retrieval-quality.md`, `tool-retrieval.md`), `./cmd/cairn` (`multi-machine.md`).
- Unregistered flags: `--entity-file` in `remember` (`docs/plans/task-evaluation.md:411`).

#### Tier 3: Code Block Syntax Errors (2 items)
- `docs/campaign-completion.md:34`: Malformed JSON containing ellipsis `<uuid>` placeholder inside raw JSON.
- `docs/grader-delivery-v1.md:205`: JSON fragment `"verdicts": {...}` lacking root enclosing braces.
*(Both files are unlaunched trial artifacts designated for excision in Milestone 1).*

#### Tier 4: Document Completeness (21 missing files)
- Root suite: 4/4 complete (`README.md`, `AGENTS.md`, `CHANGELOG.md`, `GLOSSARY.md`).
- Missing suites to author in M3:
  - `quickstart.md` (0/1)
  - `guides/` (0/4: `memory-search.md`, `memory-capture.md`, `session-coordination.md`, `agent-workflows.md`)
  - `developer/` (0/3: `architecture.md`, `testing.md`, `contributing.md`)
  - `operator/` (0/4: `deployment.md`, `configuration.md`, `maintenance.md`, `daemon-deployment.md`)
  - `mcp/` (0/5: `overview.md`, `claude-code.md`, `codex.md`, `opencode.md`, `agy-hermes.md`)
  - `reference/` (0/4: `cli.md`, `mcp-tools.md`, `api-rest.md`, `lifecycle-slices.md`)

---

## 3. Milestone Resolution Roadmap

The test runner allows targeted verification per milestone using the `--milestone` or `--tier` flags:

| Milestone | Target Resolution Scope | Expected Green Tiers | Runner Command |
|---|---|---|---|
| **M1** | Excision of 93 trial files, repair of 9 broken links | Tier 1 passes (100%); Tier 3 malformed JSONs eliminated | `python3 scripts/verify_docs.py --tier 1` |
| **M2** | README alignment, CLI help sync, MCP docs sync | Tier 1 & Tier 2 pass (100%) | `python3 scripts/verify_docs.py --milestone M2` |
| **M3** | Author 21 missing architecture documents | Tier 4 passes (25/25 complete) | `python3 scripts/verify_docs.py --tier 4` |
| **M4** | Final gate: clean pass across all documents | All 4 Tiers 100% green | `python3 scripts/verify_docs.py` |

---

## 4. Test Harness Verification

The test harness was independently validated:
1. **Determinism**: 5 consecutive runs produced identical link counts (2,055 internal links) and syntax block counts (367 blocks).
2. **Performance**: Full 4-tier verification across all 507 repository files completes in under **1.8 seconds**.
3. **Exit Code Semantics**: Returns exit code 1 under failures in `--strict` mode; returns exit code 0 when all selected tiers pass or when `--no-strict` is used.
4. **Machine-Readable**: Emits standardized JSON via `--json` conforming to schema `cairn.docs-verification/1`.
