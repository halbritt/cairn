# Coordination lead prompt

Model: Argon. Reasoning: high.

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

You own J-01–J-06, HC-04 and reconciliation of CAIRN-276–278. Direct J-B1 (journal execution/recovery), J-B2 (wake and peer transport), and J-B3 (watcher/transcript/session integration) using Sol Max.

Assign two Opus 5.5 Max reviewers distinct briefs. J-R1 checks retry, refusal, completion and reply behavior. J-R2 checks peer identity, session custody, locking, cancellation, process death and recovery. They form findings independently before comparing results.

Read integrations/lifecycle/coordination.py, native_session.py, wake transports, transcript recovery, scripts/test_inbox_recovery.py, scripts/test_inbox_drain.py, and the current full bodies/comments of CAIRN-276–278. Inspect relevant Cairn decisions and current Go peer/wakeup primitives.

## Resolve the existing tracker work

CAIRN-276 is the characterization gate. CAIRN-277/278 currently describe extracting a Python InboxJournal and migrating Python callers. Revise or explicitly supersede their implementation scope under the campaign parent to implement the journal owner once in Go. Preserve the original evidence, acceptance requirements and history. Record the replacement mapping. Do not mark a planning reconciliation as implemented, and do not require a redundant Python port.

## Preserve and prove

Characterize explicit retries through the authenticated API separately from ordinary pending-slot watcher replay and the existing committed-completion exact-result recovery exception. Capture write-before-send ordering, immutable intent, request/argv/stdin identity, exact result/reply references, refused-slot finality, legacy missing-kind behavior, lock paths and different timeout policies. Record the settled_journal_index / finish_reply discrepancy explicitly before translating it.

Inventory the full existing inbox command family, including journaled completion/reply, acknowledgement, drain and compact operations. Preserve commands already issued to active sessions throughout coexistence. The installed watcher uses --config-dir to discover multiple bindings; the proposal's shortened --config sketch must not narrow that behavior. Publish the complete grammar and compatibility fixtures before implementing it.

Give journal execution, recovery and inspection one owner. Inspection reports named facts without mutation or sends. Keep conversation discovery, active-attempt selection, admission, custody and wake policy with their specified owners. Do not collapse completion, response obligation and possible late reply into one done flag.

Use Foundation's peer/state primitives. Preserve SO_PEERCRED and process-start/boot identity, ancestry restrictions, bounded framing and private runtime sockets. Codex WebSocket work must preserve its actual handshake, framing, busy/refusal and cancellation semantics. Supported Claude, OpenCode and Hermes transports receive their own fixtures.

Test watcher restart, delayed/lost responses, lock contention, corrupt state, cancellation, stale processes and other-session isolation through real process boundaries. Ordinary automatic watcher replay sends pending slots and does not replay refused work. Preserve the existing `finish_reply` recovery exception: when completion committed but its exact result reference is missing and no response exists, retry the same completion to recover the reference before creating the reply. Preserve ordinary explicit retries through the authenticated API for unchanged intents, including locally committed or refused slots. Do not use live held/refused deliveries as test material or adopt another session's deliveries. Native transcript recovery must preserve strict bounded parsing and byte/encoding behavior.

## Sequence and handoff

Complete J-01 before journal translation. Start J-04 after Foundation's peer/binding contract. J-02/J-03 remain one serialized journal work stream; J-B3 can build watcher/transcript acceptance fixtures concurrently. J-05/J-06 integrate only after their stated dependencies.

Publish fixture traces, state contract, CLI grammar, supported harness matrix and exact commits to Release. Update coordination/operator documentation as each path lands. Close HC-04 only after independent reviews, required local checks, trunk integration and the complete recovery matrix pass.
