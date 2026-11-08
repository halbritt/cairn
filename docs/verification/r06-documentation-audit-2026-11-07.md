# R-06 documentation source audit — 2026-11-07

This report records the documentation preparation portion of CAIRN-308. It
checks selected claims against source at
`198d4ab005249da309db3795e7fdd20460341946`, including the accepted CAIRN-424
refusal diagnostics. The candidate changes 17 existing Markdown files and adds
this report. It changes no implementation, schema, installed runtime or policy.

## Coverage and review bounds

The tracked inventory contained 478 Markdown files before this report: 16
project, developer and operator surfaces; 106 API, MCP, CLI and memory surfaces;
48 coordination and workflow surfaces; and 308 protected instructions,
historical records, plans and source documents. These sets are disjoint and
cover the tracked inventory. The documentation checker's file count differs
because its scanner excludes hidden directories and directories named
`sources`, among others; it is not the ownership inventory.

| Area | Changed files | Independent review carried into preparation |
| --- | --- | --- |
| Project, developer and operator guides | 8 | Exact document bytes were compared with the peer-reviewed candidate. The review covered all changed hunks, five complete guides, relevant status passages, the documentation guide's base plus changes, and the changelog addition. |
| API, MCP, CLI and memory guides | 5 | The earlier five-file review and subsequent bounded corrections cover the changed contracts. The final MCP review checked the exact sentence replacement and affected receipts; it did not repeat a full-document review. |
| Coordination and workflow guides | 4 | Exact four-file content and changed claims carry bounded acceptance. Corrected receipt views require their accompanying qualification and attribution; original author summaries do not independently establish coverage. |

Author inventories distinguish source checks, mechanical checks, historical
records and fixtures. Inventory membership and author status labels are not an
independent semantic certificate for every document. The combined review read
all changed hunks, compared their overlapping contracts and linked references,
and verified that the 17 resulting blobs equal their reviewed inputs. No
merge conflict required a new interpretation. A separate independent review
of the exact combined candidate remains required before landing.

## Corrected claims and source basis

| Surface | Checked claim and source |
| --- | --- |
| Quickstart, deployment and maintenance | The quickstart selects a separate state directory. `start` migrates the selected store even when it already exists; backup catalog checksums use `hashlib.file_digest`. The deployment guide distinguishes its service defaults from the quickstart directory. See [`local-store.sh`](../../scripts/local-store.sh) and the [store unit](../../deploy/cairn-store.service). Setup examples were inspected, not executed. |
| Architecture, implementation status and roadmap | Lifecycle entrypoints remain Python; Go supervises external semantic workers. Installing a Go binary does not update installed lifecycle modules. See [`lifecycle_bundle.py`](../../integrations/lifecycle/lifecycle_bundle.py), [`install-semantic.sh`](../../scripts/install-semantic.sh) and [`semantic/`](../../semantic). The [release contract](../plans/hybrid-runtime-contracts/release.md#work-items-and-release-gates) retains implementation, installation, whole-unit qualification and production transition gates. |
| Documentation guide and changelog | The guide now distinguishes source inspection from running examples. [`verify_docs.py`](../../scripts/verify_docs.py) checks links, CLI syntax, JSON and shell syntax, and required-guide presence. Those checks do not execute the documented workflows. The changelog describes these bounded documentation changes. |
| API contract, local API and evidence refresh | The contract includes local-role metadata. `set-sections` is included in operation/help lists and receives a 1 MiB encoded envelope, consistent with `replace`; decoded source limits are unchanged. See [`document.go`](../../internal/apicontract/document.go), [`server.go`](../../localapi/server.go) and [`request_limit.go`](../../localapi/request_limit.go). |
| MCP edits and declarations | Edit-mode lists include `set_sections`; evidence citations are structured objects. The Codex example includes `cairn_client_info`. See [`mcpapi/server.go`](../../mcpapi/server.go), [`evidence.go`](../../core/evidence.go) and [`codex_config.go`](../../cmd/cairn/codex_config.go). The compact-profile figures are attributed to the [dated declaration comparison](claude-compact-all-tools-2026-10-25.md), not presented as current provider-token or task savings. |
| MCP search and spans | The search catalog includes failure signatures, separate semantic input and request IDs. An unacknowledged semantic-input response may follow commitment: do not retry or downgrade that request, even with the field retained. See [`capabilities.go`](../../localapi/capabilities.go), [`retrieval.go`](../../internal/indexview/retrieval.go) and the [semantic-input contract](../semantic-discovery.md#separate-lexical-and-semantic-input). Positive span lengths and operation-specific bounds match [`index.go`](../../core/index.go), [`evidence_expansion.go`](../../core/evidence_expansion.go), [`record_history.go`](../../core/record_history.go) and [`byte_span.go`](../../core/byte_span.go). |
| Work CLI | Session associations use the agent/execution pair format; blocker resolution requires the item and expected revision. See [`work.go`](../../cmd/cairn/work.go) and [`work_associations.go`](../../core/work_associations.go). |
| Journaled sends and inbox watching | Initial intent persistence precedes API effects. Later journal failures can follow commitment; cursor checkpoint failures can follow output. File replacement and parent-directory sync have distinct failure consequences. See [`agent_send.go`](../../cmd/cairn/agent_send.go) and [`agent_watch.go`](../../cmd/cairn/agent_watch.go). |
| Sessions and native inbox | Context updates replace metadata. Native inbox commands distinguish ordinary completion/acknowledgment/reply from journaled `finish-reply`; compact-handle settlement is a separate interface. Lock waits and per-send timeouts are not a single end-to-end deadline. Recovery can send an already-selected reply without replaying task work. See [`agent_sessions.go`](../../cmd/cairn/agent_sessions.go), [`coordination.py`](../../integrations/lifecycle/coordination.py) and [compact inbox](../compact-inbox.md). |

The cross-document pass checked setup directory selection, runtime transition
language, API envelope versus decoded-source limits, MCP edit/search/span
contracts and native inbox retry/recovery distinctions. The accepted CAIRN-424
implementation and contract blobs remain unchanged. Its native-inbox refusal
diagnostic passage remains byte-identical within the expanded guide.

## Local validation

The combined candidate passes `make check`, documentation tiers 1–4 via
`python3 -B scripts/verify_docs.py --tier 1,2,3,4 --json`, and
`git diff --check`. The exact candidate commit, tree, file hashes, full diff
and command results are bound in the external review manifest. Keeping the
final commit there avoids a circular commit identifier in this report.

`make check` covers Go vet, generated API contract drift, Go formatting and
the four pinned design-source hashes. The documentation tiers provide
mechanical checks only. No store/native integration suite, production database,
effectful example, model or worker experiment was run for this audit. Pinned
design text was hash-checked by the ordinary repository check; protected
capture-source execution was not part of this work.

## Remaining acceptance boundaries

Historical reports retain their dated claims and artifact availability limits;
see the [verification record guidance](README.md). This audit did not reproduce
their experiments or establish the identity of installed files or processes.
Detailed review attribution and operational receipts remain outside Git.

Documentation preparation does not complete R-06, R-02, native/model
qualification, drain or cutover. Module/kit readiness and actual acceptance
remain unestablished. The [release contract](../plans/hybrid-runtime-contracts/release.md)
still requires the complete watcher unit, bindings, shared-state users,
source/model identity and rollback evidence. Runtime controller-consent and
O-C4-1 holds remain separate from documentation landing review. No failed or
uncertain runtime work was replayed. This report establishes neither full
design acceptance nor measured usefulness.
