# Foundation lead prompt

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

You own F-01–F-04 and HC-03. Direct F-B1 (shared primitives, binding and admission), F-B2 (recall), and F-B3 (session/ledger, then capture) using Sol Max. One Opus 5.5 Max reviewer independently reviews your contracts and changes.

Start from integrations/lifecycle/, cmd/cairn/locked_state_file.go, internal/claudechannel/, localapi/, and existing installed-lifecycle tests. Proposed internal/statefile, internal/procpeer and internal/lifecycle packages are destinations, not claims that the code already exists.

Create concrete Plane items for the F plan keys. F-01 must deliver a callable admission/binding path and exercised shared primitives. F-04 preserves native-session and grant-ledger persistence through a real consumer and lands before F-02/F-03. Those items replace recall/capture subprocess hops with direct localapi.Client calls. F-B2 can capture fixtures while F-B3 completes F-04. Sequence shared-file edits. F-04's state primitives do not wait for J-05, which later integrates coordination turn/custody policy.

## Preserve and prove

Preserve /2 bundle validation while introducing /3 binary bindings. A /2 pin authenticates its Python bundle, never the Go executable: use the preserved legacy execution path or require an explicit drain and /3 repin. Verify token/profile selection without reading credentials into model context. Test ownership, permissions, hash mismatch, missing or malformed state, PID reuse/start identity and stale bindings.

Preserve each supported harness's exact hook response, stdout/stderr and exit behavior, including required-context refusal, Codex SessionStart stop envelope and optional recall degradation. Preserve recall/capture meters, selected-content boundaries, budgets, request identity and store-attributed fields. Do not treat a payload principal as authentication.

Retain flock, timeout and atomic write/rename/directory-sync behavior. Test concurrent access and process death at persistence boundaries. Compare old and new implementations with fixtures independently reviewed before the port. Document only deliberate normalization of nondeterministic fixture fields.

Publish narrow shared APIs for Coordination and Release with fixtures and a revision. Do not give each harness its own copy of admission, peer validation or state writing. A shared extraction lands with an existing or new exercised consumer. Report any intentional behavior change separately from compatibility work.

## Handoff and completion

Provide the Coordination lead with state, peer and binding contracts plus F-01/F-04 commits. Provide Release with supported hook grammar, output contracts, fixture matrix and /2-/3 staging requirements. Update affected lifecycle/reference docs as each behavior lands.

Close HC-03 only after F-01–F-04 are reviewed, integrated, pushed and verified across the supported fixture matrix. Provide exact test commands/results and unresolved limitations; installed production cutover remains Release's responsibility.
