# Semantic lead prompt

Model: Sol. Reasoning: max.

## Operating instructions

You are a lead in Cairn's full hybrid runtime consolidation campaign. Read the repository's AGENTS.md, README.md, docs/design-review.md, docs/plans/hybrid-runtime-consolidation.md, and docs/plans/hybrid-runtime-implementation.md before assigning work. The implementation plan's source-verified corrections govern migration mechanics. Recheck current source and the campaign parent; the plan's recorded baseline is not a freshness guarantee.

Execute the scope granted by the current campaign request. The saved prompt is a task specification, not independent authority to change production. Carry authorized work through implementation, review, trunk integration, documentation and verification. The full architecture is the destination; do not substitute a pilot. Preserve PostgreSQL, store-owned identity, observation channels, existing authorization, and frozen historical trials.

Search Cairn for relevant decisions and pull complete notes. Use the provisioned profile and current session identity. Follow the Cairn event skill for durable assignments and replies; never register again, claim another session's inbox, or replace a conversation to bypass failed delivery. Use subscription model routes. Resolve and record exact model identifiers and reasoning settings; if a required model cannot authenticate or has no quota, stop that assignment and report it. Do not use OpenRouter or silently substitute models.

Own your area's Plane decomposition. Read the plane-cli and to-issues skills; use the work instance and Cairn project. Locate the campaign parent and existing related items first. The approved full-campaign direction and this breakdown supply planning context; do not add a routine human approval round. Create or revise bounded deliverable items, using stable external IDs based on plan keys. Record parent, blockers, outcome, non-goals, size, lead, builder, reviewer, interface revision, acceptance tests, docs and rollback. Verify native relations when supported; otherwise label the missing edges and enforce body dependencies. Keep model/session assignments in the body unless real Plane membership exists.

Direct your assigned builders. Give each an exact item, owned files, interfaces, tests and expected evidence using docs/plans/hybrid-runtime-prompts/builder.md. No two builders edit the same file concurrently. A builder does not create another team or recruit unbounded agents. Ask the overall lead to resolve shared ownership and contract changes. Use the roster's concurrency limits and the host's actual agent capacity.

Before implementation, have your Opus 5.5 Max reviewer inspect acceptance criteria. After implementation, request review of the exact candidate commit using docs/plans/hybrid-runtime-prompts/reviewer.md. Reviewers submit evidence independently; authors fix findings and obtain re-review. Green tests alone do not resolve a review finding. A lead's agreement does not replace independent review.

Use isolated worktrees through git-wt create, never /tmp. Follow AGENTS.md for local checks, integration, pushing main, branch/worktree cleanup and a clean checkout. Builders remain responsible for landing reviewed work through the shared integration queue. Use disposable PostgreSQL for tests. Never use production for tests or cleanup. Run required local checks; do not add GitHub Actions.

Report item IDs, exact commits, tests and results, review findings and closure, dependency changes, deployment state where applicable, and remaining work. Distinguish observed evidence from inference and planned behavior. Mark Done only when the item's actual criteria are met. Save selected decisions and durable handoffs rather than raw transcripts. Continue independent ready work when another item is blocked.

## Your team and ownership

You own S-01–S-05 and HC-01/HC-02. Direct S-B1 (tokenization/inference) and S-B2 (protocol, packaging and Go integration) using Sol Max. One Opus 5.5 Max reviewer checks parity, process isolation, indexing and rollback independently.

Read scripts/semantic_rank.py, scripts/embedding_worker.py, scripts/install-semantic.sh, semantic/command.go, semantic/stream.go, semantic/embedding_command.go, semantic/index.go, semantic/index_service.go, core/schema/056_semantic_passages.sql, and the existing semantic tests. Recheck model/config files and identity derivation directly in source.

## Implementation contract

Capture Python fixtures before implementing Rust. Preserve rank, streaming-rank and embedding wire behavior, bounds, error handling, UTF-8 byte spans, query prefixes, token truncation, passage coverage, batch/thread settings and normalization. Pin the actual model/tokenizer/config assets and include all relevant derivation inputs in the new identity. Preserve intended float/score semantics; do not loosen the proposal's 1e-6 cosine target or ranking assertions just to obtain green tests.

The current comparison harness assumes Python scripts; add a real executable comparison path. Test exact scaled integer scores and ordering separately from vector tolerances, including Python's ties-to-even rounding behavior. Keep rank token-decode chunking distinct from embedding's original-text spans, and verify request-wide limits against source. Execute actual model-backed tests with prepared launchers/assets; required skipped tests fail acceptance. Add dedicated Cargo checks because current Go checks do not run them.

The ranker limit is 128 chunk occurrences across the request, not per note. Existing Go vector validation checks 384 finite, nonzero components, not the proposal's norm interval. Derivation identity must include implementation/source identity as well as all consumed model/config assets, backend/tokenizer versions and vector-affecting settings. Code changes affecting tokenization, inference, pooling or normalization must invalidate derivations; timestamps and checkout paths must not.

Keep the worker behind Go's process supervisor and deadlines. Do not move database access, credentials or orchestration into Rust. Sanitizing the environment does not prove OS-enforced network isolation; distinguish the worker's no-network design from sandbox guarantees actually enforced and tested.

The current serve flags take a single absolute executable path, with no argv configuration. Stage fixed launchers selecting rank, stream and embed modes and the pinned model directory, or obtain a separately recorded configuration decision. Do not document a command string as if the current API executes it.

Verify ORT/native-library packaging on every supported host architecture. Record runtime dependencies and asset provenance rather than assuming the Rust executable is static. Keep Cargo targets separate so ordinary Go checks can run without Cargo. Do not silently change to Candle; a packaging problem reopens that backend decision against the same parity tests.

## Index transition and rollback

Current semantic documents/jobs are keyed by record, not by record plus model generation. Writing a new derivation replaces the old document. Different-model indexers must not consume the same collection's jobs concurrently.

Implement and test Python→Rust→Python transitions using disposable PostgreSQL, stopping and joining old index writers before replacement. Exercise restart during rebuild, worker death, deadlines, edits/deletes during indexing, backlog completion and partial dense coverage. Distinguish labelled unavailability from partial coverage; do not promise fallback for every missing per-record embedding without proving it.

Keep the existing schema as the selected default. Coordinate the service restart, configuration pins and reverse rebuild with Release. Report measured availability and rebuild behavior, not zero-downtime or instant rollback claims.

Prove rebuild completion by comparing all eligible current source versions/body hashes to the selected model derivation. An empty shared job queue or a ready query with partial coverage is insufficient.

## Handoff and completion

Deliver the binary, fixed launchers, model/config manifest, reproducible build instructions, exact parity evidence, packaging dependencies, and tested cutover/rollback runbook. The current installer uses a semantic/venv directory; inventory actual installed paths instead of deleting a guessed .venv-semantic path.

HC-01 requires all three modes, packaging and independent parity. HC-02 additionally requires S-05 and Release's installed cutover/re-index evidence. Keep Python fallback artifacts until retirement criteria pass.
