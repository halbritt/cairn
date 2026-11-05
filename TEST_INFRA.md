# Cairn Test Infrastructure & Work Coordination E2E Specification

This document defines the test architecture, methodology, feature inventory,
and validation suites for Cairn, with primary focus on the Work Coordination
subsystem (delivery slices WC-01 through WC-06, covering acceptance checks
AC-01 through AC-45) and standing documentation integrity checks.

---

## 1. Testing Philosophy

Cairn employs an **opaque-box, requirement-driven** end-to-end testing methodology:

1. **Observable Behaviors Over Internal State**:
   Tests verify observable outputs across system boundaries: CLI exit codes,
   standard output/error JSON envelopes (`cairn.response/1`), HTTP response
   headers and status codes, PostgreSQL transactional state, and durable inbox
   events. Tests do not bind to private struct fields, internal unexported
   functions, or transient memory buffers.

2. **Strict Invariant Verification**:
   The test suites enforce the eight core design invariants:
   - **Dedicated OS-operator work transport**: Local-destination agent profiles
     lack `Channel.Operator` and cannot execute work mutations or advice apply.
     Presentation is served exclusively via dedicated loopback `work serve`.
   - **Pinned Plane identity**: Plane projects are bound to immutable UUID tuples
     `(origin, workspace_uuid, project_uuid, config_revision)`. Repointing configuration
     is rejected on refresh.
   - **Refresh/purge transaction serialization**: Staged snapshot imports commit
     in a single transaction; concurrent purges and exclusions increment the
     revision and take precedence.
   - **Historical execution isolation**: Associations link to `(AgentID, ExecutionID)`.
     Session resume preserves historical records without borrowing new task summaries.
   - **Wait destination ceiling + revalidation**: Scheduler rechecks destination
     ceilings against currently valid binding permissions upon resolution.
   - **Store-owned unique organic-turn root & 3-item cap**: Turn admissions bind
     `(collection, agent_id, execution_id, native_owner_turn_id)`. New request UUIDs
     cannot reopen or exceed the 3-item burst limit.
   - **Wait notification restore fence**: Incrementing database restore generation
     atomically fences published unclaimed notifications.
   - **Guarded undo**: Blocker creation undo withdraws annotations; blocker resolution
     undo reopens prior state. Intervening modifications trigger `UNDO_CONFLICT`.

3. **Strict Production Database Isolation**:
   Tests execute exclusively against disposable, process-owned PostgreSQL clusters
   (via `scripts/test-postgres.sh` or isolated socket endpoints in `/tmp/cairn-postgres.*`).
   Tests NEVER touch production configurations (`~/.config/cairn/`), production databases,
   or running Cairn daemons.

---

## 2. Feature Inventory: Work Coordination (WC-01 through WC-06)

The Work Coordination subsystem comprises five functional domains and one combined
release/retirement verification domain:

| Domain | Feature Capability | Slice | Primary Operations | Acceptance Checks |
|---|---|---|---|---|
| **Work View** | `work-view/1` | WC-01 | Project binding, Plane read-only sync, explicit associations, annotations, loopback browser server, metadata purge | AC-01 through AC-11 (11 checks) |
| **Work Artifacts** | `work-artifacts/1` | WC-02 | Artifact registration (4 kinds), immutable revision links, reverse impact queries | AC-12 through AC-17 (6 checks) |
| **Durable Waits** | `coordination-waits/1` | WC-03 | Request/group wait registration, atomic transaction resolution, outbox drain, restore fencing | AC-18 through AC-27 (10 checks) |
| **Next-Turn Notices** | `next-turn-notices/1` | WC-04 | Quiet notice publication, native owner-turn admission, sequential advance (max 3), publisher flush | AC-28 through AC-35 (8 checks) |
| **Model Advice** | `work-advice/1` | WC-05 | Bounded context snapshot, Claude subscription driver (empty tools), atomic subset apply, guarded undo | AC-36 through AC-43 (8 checks) |
| **Release & Retirement** | `work-features` | WC-06 | Cross-feature multi-agent journey, fault injection, reverse dependency retirement, purge reapplication | AC-44 through AC-45 (2 checks) |

**Total Acceptance Checks**: 45 checks across 6 delivery slices.

---

## 3. Four-Tier Test Architecture

The E2E test harness classifies test scenarios into four progressive tiers:

### Tier 1: Feature Coverage (AC-01..45 Happy Paths)
- **Objective**: Validate the nominal happy-path contract for every acceptance check.
- **Coverage**:
  - `work project add`, `work refresh`, `work list/show`, `work associate`, `work progress`, `work blocker`.
  - `work artifact register`, `work artifact link`, `work artifact impact`.
  - `wait create`, response resolution, scheduler outbox drain, native acknowledgment.
  - `notice-pending`, native owner-turn admission, sequential advance (1 to 3 items).
  - `work advice generate`, proposal review, atomic subset apply, and undo.
  - Reverse retirement order and status verification.
- **Threshold**: 100% of the 45 ACs must have an executable Tier 1 test.

### Tier 2: Boundary & Corner Cases
- **Objective**: Validate system behavior under extreme sizes, limits, timeouts, race conditions, and error paths.
- **Coverage**:
  - **Plane Limits**: Projects exceeding 10,000 items (`PROJECT_TOO_LARGE`); output exceeding 16 MiB; command timeout (>30s); external authentication rejection masking cached fields.
  - **Notice Limits**: Quiet queue cap (100 per recipient, 1,000 per collection; `QUEUE_FULL`); max 3 quiet notices per user turn; non-user turn origins rejected (`UNSUPPORTED_DELIVERY`).
  - **Advice Limits**: 32 KiB input cap; 32 sources; 12 actions; 16 KiB output; 90s deadline (`MODEL_TIMEOUT`); snapshot expiry (1 hour; `PROPOSAL_EXPIRED`).
  - **Concurrency & Conflicts**: Optimistic concurrency version mismatches (`VERSION_CONFLICT`); idempotency conflicts on modified intent (`IDEMPOTENCY_CONFLICT`); guarded undo conflicts on intervening edits (`UNDO_CONFLICT`).
  - **Fail-Stop Safety**: Subscription quota exhaustion (`QUOTA_EXHAUSTED`) or authentication failure (`SUBSCRIPTION_REQUIRED`) fails immediately with zero fallback.
  - **Sanitization**: Tool call requests, arbitrary URLs, unknown action types rejected (`INVALID_PROPOSAL`).

### Tier 3: Cross-Feature Interactions
- **Objective**: Validate correctness across overlapping subsystem boundaries.
- **Coverage**:
  - Durable wait satisfaction emitting an outbox notice that queues as a next-turn quiet notice.
  - Model advice generating proposals referencing registered artifact revisions and progress annotations.
  - Concurrent metadata purge invalidating staged Plane refresh snapshots and unapplied advice proposals.
  - Database restore fence invalidating waiting rows and unclaimed notices while preserving historical artifact links.

### Tier 4: Real-World Workload Scenarios
- **Objective**: End-to-end integration and multi-agent lifecycle journeys under real operating conditions.
- **Coverage**:
  - **AC-44 Journey**: Multi-agent lifecycle spanning a Plane card fixture, two agent sessions, wait registrations, quiet notices, advice review, atomic apply, and guarded undo.
  - **AC-45 Fault Recovery**: Injected daemon crashes, restore fencing at outbox/admission seams, reverse-dependency feature retirement, and purge reapplication.

---

## 4. Acceptance Checks Specification (AC-01 through AC-45)

### Slice WC-01: Shared Work View
| Check | Tier | Focus / Test Seam | Invariant Enforced |
|---|---|---|---|
| **AC-01** | 1 | Distinct identity by `(origin, workspace, project, config_rev)` tuple | Pinned Plane identity |
| **AC-02** | 2 | Refresh refuses changed configuration / repointed instance | Config pinning refusal |
| **AC-03** | 1 | Multiple executions linked to one item; historical isolation on resume | Historical execution isolation |
| **AC-04** | 2 | Local-destination agent profiles denied operator work mutations | Privilege separation (`Channel.Operator`) |
| **AC-05** | 1 | Linked requests display pending/leased/handled separate from Plane | Request status vs Plane state |
| **AC-06** | 2 | Full refresh replaces cache generation; errors cannot overwrite | Cache generation swap & masking |
| **AC-07** | 2 | External auth rejection hides cached fields without deleting links | Auth failure masking |
| **AC-08** | 2 | Duplicate mutations return retained response; version conflicts refuse | Idempotency & optimistic concurrency |
| **AC-09** | 2 | Refresh fixtures reject mutating commands; viewing changes no presence | Read-only connector & non-waking reads |
| **AC-10** | 2 | Purge removes text; exclusions persist and reapply on restore | Purge & exclusion persistence |
| **AC-11** | 2 | Staging-import barrier proves concurrent purge/exclusion wins | Concurrency barrier |

### Slice WC-02: Work-Artifact Relationships
| Check | Tier | Focus / Test Seam | Invariant Enforced |
|---|---|---|---|
| **AC-12** | 1 | Many-to-many links with independent roles (`input`/`output`/`evidence`) | Many-to-many links & role attribution |
| **AC-13** | 2 | Registering newer revision does not alter prior links; unverified locators labelled | Revision immutability |
| **AC-14** | 1 | Reverse lookup paginates matches; digest cannot merge unrelated IDs | Reverse impact navigation |
| **AC-15** | 2 | Unavailable source masks data; unlink and purge distinct | Masking vs unlink vs purge |
| **AC-16** | 2 | Malicious schemes/credentials refuse; no URL fetching or execution | Locator validation & no-fetch |
| **AC-17** | 1 | CLI and browser add/remove links, inspect attribution, navigate impact | CLI & browser navigation |

### Slice WC-03: Durable Coordination Waits
| Check | Tier | Focus / Test Seam | Invariant Enforced |
|---|---|---|---|
| **AC-18** | 1 | Response/closure before registration satisfies from retained state | Registration race resolution |
| **AC-19** | 2 | Failure report satisfies wait without becoming task success | Failure response vs task success |
| **AC-20** | 2 | Timestamp ordering resolves deadline, cancellation, and closure races | Deterministic race resolution |
| **AC-21** | 2 | Crash between resolution and outbox drain recovers to one notice | Crash recovery & event idempotency |
| **AC-22** | 1 | Native recipient reads and acknowledges exact notice | Native delivery & acknowledgment |
| **AC-23** | 2 | Resumed conversation can inspect/cancel; stale execution cannot | Conversation ownership |
| **AC-24** | 2 | Restore fences waiting/unacknowledged cases; no automatic continuation | Restore fencing of waits |
| **AC-25** | 3 | Persistent admission fence blocks published unclaimed notice after restore | Persistent admission fence |
| **AC-26** | 2 | Revoked binding or widened profile cannot breach destination ceiling | Destination ceiling & revalidation |
| **AC-27** | 2 | Disabling registration continues maintenance; pure get reports overdue | Maintenance continuity |

### Slice WC-04: Next-User-Turn Notices
| Check | Tier | Focus / Test Seam | Invariant Enforced |
|---|---|---|---|
| **AC-28** | 1 | Queued entries never appear as ready work to NextEvent/watchers/cues | Zero idle wakes |
| **AC-29** | 2 | Supported native user turn admits; channel message/callback cannot | Supported origin gating |
| **AC-30** | 2 | Root admission stable across retries; at most 3 quiet notices per turn | Root stability & 3-burst cap |
| **AC-31** | 2 | Source reads respect budget; rendering is not acknowledgment | Context budget & non-ack |
| **AC-32** | 2 | Claim/flush/expiry races create at most 1 delivery; overflow rejected | Race resolution & queue cap |
| **AC-33** | 2 | Crash after admission preserves hold; does not requeue or wake | Hold preservation on crash |
| **AC-34** | 2 | Legitimate new execution admits queued work; stale attempts cannot | Execution binding & restore |
| **AC-35** | 2 | Unsupported bindings (Agy) refuse before acceptance; safe disable | Binding validation & disable |

### Slice WC-05: Model-Assisted Coordination
| Check | Tier | Focus / Test Seam | Invariant Enforced |
|---|---|---|---|
| **AC-36** | 1 | Review, apply, and undo loop for progress, blocker, and artifact actions | Full advice review/apply/undo |
| **AC-37** | 2 | Tool requests, arbitrary URLs, unknown IDs rejected with zero mutations | Model output rejection |
| **AC-38** | 2 | Explicit source selection cannot bypass hosted eligibility; local inputs refuse | Hosted eligibility |
| **AC-39** | 2 | Subset apply is atomic; duplicate retry returns original receipt | Atomic subset apply |
| **AC-40** | 2 | Undo blocker creation withdraws; undo resolution reopens prior state | Blocker creation/undo logic |
| **AC-41** | 2 | Quota/auth failure stops immediately with zero provider fallback | Quota/auth fail-stop |
| **AC-42** | 2 | Timeout/overflow terminates process group; restart reconciles cleanly | Process group cleanup |
| **AC-43** | 2 | Limits, job dedupe, and purge cleanup operate with generation disabled | Retention & cleanup continuity |

### Slice WC-06: Combined Release & Retirement Verification
| Check | Tier | Focus / Test Seam | Invariant Enforced |
|---|---|---|---|
| **AC-44** | 4 | End-to-end multi-feature user journey across two sessions on Plane card | E2E multi-agent journey |
| **AC-45** | 4 | Injected crashes, restore fences, reverse retirement, purge reapplication | Fault injection & reverse retirement |

---

## 5. External CLI Scripted Failure Fixtures

To verify failure modes deterministically without depending on external network services,
the test infrastructure includes scripted mocks for external tools:

### Plane CLI Fixture (`tests/fixtures/mock_plane.sh`)
- Controlled via `MOCK_PLANE_MODE`:
  - `normal`: Returns valid JSON issue list.
  - `too_large`: Returns >10,000 issues (triggers `PROJECT_TOO_LARGE`).
  - `output_overflow`: Outputs >16 MiB payload.
  - `timeout`: Delays execution beyond 30s timeout.
  - `auth_failure`: Exits with code 1 and auth error message.
  - `mutating_attempt`: Rejects mutating commands (`create`, `delete`, `update`).
  - `config_mismatch`: Returns mismatched workspace/project identifiers.

### Claude CLI Fixture (`tests/fixtures/mock_claude.sh`)
- Controlled via `MOCK_CLAUDE_MODE`:
  - `normal`: Returns valid structured proposal with 3 action types.
  - `empty`: Returns valid proposal with 0 actions.
  - `tool_request`: Returns JSON requesting tool/MCP execution (triggers `INVALID_PROPOSAL`).
  - `arbitrary_url`: Returns proposal with unverified external URL (triggers `INVALID_PROPOSAL`).
  - `unknown_action`: Returns unknown action type (triggers `INVALID_PROPOSAL`).
  - `quota_exhausted`: Exits with 429 quota exhaustion (`QUOTA_EXHAUSTED`).
  - `auth_failure`: Exits with 401 subscription required (`SUBSCRIPTION_REQUIRED`).
  - `timeout`: Delays execution beyond 90s deadline.
  - `output_overflow`: Outputs >16 KiB payload.

---

## 6. Test Execution & Documentation Validation

### Running the E2E Test Suites
```sh
# Run all Go packages and fixtures against disposable PostgreSQL (Full Integration Standard):
# MANDATORY: Must run WITHOUT CAIRN_TEST_ONLY to ensure all package suites and fixtures execute.
make test-integration

# Run specifically the Work Coordination E2E suite:
go test -v ./tests -run TestWorkCoordination
```


### Documentation Verification Suite
Standing documentation integrity checks are retained:
```sh
python3 scripts/verify_docs.py
```

It uses the Python standard library and invokes `bash -n` for shell examples.
The default strict mode exits nonzero when a selected tier reports a failure.
Use `--json` for machine-readable results, `--tier 1,3` to select checks, or
`--no-strict` to inspect failures without a failing exit status.

| Tier | Checks | Limits |
| --- | --- | --- |
| 1 | Relative Markdown links, target files, heading anchors, and explicit HTML anchors | Uses regular expressions and its own slug generator; does not fetch external links or fully parse Markdown |
| 2 | Documented Cairn command names, known flags, and selected command contracts | Discovers flags from source but does not validate every command-specific flag combination or execute examples |
| 3 | JSON parsing and shell grammar in recognized fenced blocks | Syntax validity does not establish valid API fields, available tools, or safe execution |
| 4 | Required guide files, minimum content, and headings | Presence and length do not establish accuracy or usefulness |

The source of these checks and the required-file list is
[scripts/verify_docs.py](scripts/verify_docs.py). Discovery skips hidden
directories and configured exclusions, including the pinned `sources` tree.
CLI checks cover the README and `docs/` pages; other tiers use their own scopes.

### Review Beyond the Checker

For changed examples, inspect the command's parser or `--help`, confirm field
names against request types, and run a disposable fixture where execution adds
useful evidence. Check error handling and required setup as well as the happy
path. Do not run a documented mutation against an operational database just to
validate its syntax.

Keep historical verification reports labelled as historical. Their commands,
counts, and deployment observations describe the recorded run. Current guides
should explain how a reader can reproduce a check in their own environment.

The checker is separate from `make check`; run both when a change affects Go
contracts and their documentation. See [developer testing](docs/developer/testing.md)
for the full validation workflow and [interpreting results](TEST_READY.md) for
what a successful run establishes.

