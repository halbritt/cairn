# J-01 inbox journal characterization

CAIRN-332 and CAIRN-276, `coordination/v6` at
`c9a1908bacac65806f233d040bd0656c8b43f97e` (contract blob
`e9e81fc2f1aa47b77998bb43b215b72e83a28a84`), 2026-11-06. This is a
characterization of the existing Python runtime for the CAIRN-301 migration.
It adds synthetic tests and fixtures; it does not repair the runtime, accept a
Go replacement, or establish installed behavior. Independent J-R1 and J-R2
reviews and the campaign integration gate remain required.

The Python oracle snapshot is `integrations/lifecycle/coordination.py` at
`a6ff2a9125ef56d37e2c2d15e5dd5237f7523ad4`. Its exact last-changing source commit
is `256eb6df5a267e9e016fb62cc0b1dc45ff080ca2`, and its SHA-256 is
`92716c54be54a9f2e0fbe4dbd5edc77aecb02d6f7da3422708828376e63b0116`.
The original capture used `db45c4474fa7911b5569f2f55ebf2bd19c9a81bb`; the oracle
bytes are identical. All 15 original golden traces remain unchanged and were
rerun after rebasing onto the landed `coordination/v5` contract at
`a6ff2a9125ef56d37e2c2d15e5dd5237f7523ad4`,
`docs/plans/hybrid-runtime-contracts/coordination.md`.
Round 2 rebases onto `085b119b3f849f3a83914ab617d929bf2f619663` and binds the
contract to v6 while retaining that exact Python oracle snapshot. No `/2` cell
pinned here changed between v5 and v6. Of the 32 traces reviewed at `4337d755`,
31 retain identical JSON value byte spans; only `v5_recall_wake_stdout` changes
its normalization, and three new traces close the required coverage gaps.

## Preservation surface

`scripts/test_inbox_characterization.py` runs through existing command entry
points and the disposable CLI fixture from `test_inbox_recovery.py`. Its checked-in
golden file is `scripts/fixtures/inbox_characterization_v1.json`. Normal test
execution only reads this file; there is no update-goldens mode.

| Operation or state | Observed behavior pinned by the fixtures |
| --- | --- |
| Explicit `complete`, `ack`, `respond` | Execute issued context commands. A premature reply or a different result version refuses before API contact. |
| Committed or refused ordinary command retry | Contact the authenticated CLI/API boundary again with identical argv, request ID and input; retain the original status/code and record `replay_code`. Changed input refuses locally. |
| Watcher recovery | Replay pending slots; skip committed/refused slots. A committed completion missing its exact result has one recovery send, with its original identity, before reply materialization. |
| Refusal classification | All 12 `INTENT_FINAL_REFUSALS` values become `refused`. Transport failure, unknown error codes, malformed output and timeout remain `pending`. |
| Reply reference mismatch | `SCHEMA_MISMATCH` remains pending until a successful response confirms the exact result and a valid event ID. |
| Explicit vs watcher deadlines | Process-boundary send timeouts are 30s and 10s. Real `flock` contention with a virtual clock shows a 30s explicit wait (`SESSION_BUSY`, exit 7) and a 0s watcher wait (`None` busy sentinel). |
| `finish-reply` | Both immutable intents exist in a 0600 journal before completion sends. Completion precedes publication and the reply cites its exact result. The `cairn.finish-reply/1` envelope is pinned. |
| `inbox drain` | Claim one owned lease; persist a 0600 `cairn.session-inbox/1` context with `route: drain` before returning `cairn.inbox-drain/1` and its issued commands. |
| `inbox compact read/inspect/settle/advance` | Resolve a `cairn.compact-inbox-handle/1` entry, preserve exact source and handle identity, settle through the journal, and advance the notice batch through the existing native-session owner. |
| `watch --config-dir` | Discover sorted `*.json` bindings, ignore non-JSON filenames, and contend on `<config-dir>/.watch.lock` (0600). A second watcher refuses; it succeeds after release. |
| Inspection | Pending, unknown, malformed and non-object journals are unsettled; absent, committed and refused entries are settled. Inspection leaves journal bytes unchanged and sends no commands. |

The existing recovery, drain and NativeSession suites retain the additional
coverage for lost completion/publication replies, late replies after
reconciliation, legacy missing `kind`, other-conversation isolation, stale
execution, compact source forgetting/restore fencing, lapsed leases, retirement
gates, and turn-end reminders. This slice adds preservation observations to those
tests rather than replacing them.

Temporary root paths, the oracle/interpreter paths, and the three named
timestamp fields are normalized. The recall stdout trace additionally checks
its raw byte-budget formula before replacing its derived remaining count, as
specified below. Randomness is fixed at the generation boundary
for request IDs and compact handles. Request, agent, execution, attempt, event,
delivery, lease, result and handle identities remain exact, as do argument order,
stdin, refusals, status transitions and reply references. Golden data contains no
credential contents, live session data or provider output.

## Verified inspection discrepancy: an earlier reply obligation

`test_earlier_crashed_reply_is_misclassified_during_rejected_wake_reconciliation`
pins a reachable source-level inspection defect without repairing it:

1. An earlier request executes its issued `complete` command. Recovery returns
   `{'reply_possible'}`; the real `flush_inbox_intents` and NativeSession release
   path reconcile `delivery_completed` and clear its hold while retaining its
   journal index entry. Its old issued context then executes `finish-reply` with
   the same selected result. A fault injected after the real journal directory
   `fsync` stops this late command with completion `committed`, `finish_reply`
   present and `response` absent. Completion retains its valid exact result.
2. That earlier attempt remains in `inbox_journals`. The current rejected wake
   is a different delivery; it has no `inbox_intent`, `inbox_attempt`, result
   policy, close intent or active prompt. Its session/native identity and live
   process match the receipt, and the session is neither ending nor retired.
3. The real `record_activation_refusal` constructs and hashes an owner-only
   receipt for the exact Claude channel envelope. The real
   `reconcile_rejected_wake` reads the earlier journal. Its
   `settled_journal_index` check returns **true**, without modifying that journal.
4. The synthetic authenticated CLI boundary confirms the new wake's event,
   collection, consumer and delivery, with `state: pending` and integer
   `attempts: 0`; readiness confirms the same event/delivery. The pinned
   disposable memory binding has no matching grant, so reconciliation clears
   `idle_wake` and records `wake_reconciled`. The earlier journal and index entry
   remain intact. No completion, acknowledgement or claim is sent for this new
   delivery.
5. Running `flush_journal` on the earlier attempt materializes and sends its
   still-owed reply. A scripted lost publication response leaves
   `response.status: pending` and returns `{'response'}`.

The current delivery's zero-attempt precondition therefore does not exclude the
defect: the committed completion belongs to an **earlier** indexed attempt.
The trace calls the production inspection, receipt, binding, ledger and
reconciliation code; it does not mock their decisions. Only the API executable,
disposable files and the crash boundary are controlled.

The discriminating hypotheses were that zero-attempt wake preconditions exclude
every outstanding reply, or that an earlier indexed attempt can still owe one.
Normal release followed by a late `finish-reply` establishes the second history;
the preserved journal, successful wake reset and subsequent publication attempt
distinguish it from the first.

The missing-result fixture also pins the precise unmaterialized case at the end
of `flush_journal`: if the committed completion cannot recover its missing exact
result, `response` remains absent, recovery returns `{'response'}`, and
`settled_journal_index` still returns true.

The consequence established here is an inconsistent inspection of an outstanding
reply obligation, allowing the new wake marker to clear. The earlier reply is
still durably recoverable. This is synthetic source evidence, not a claim of an
observed production incident or lost work. Any changed J-03 policy requires a
separately labelled repair and independent review. In particular, v6's caution
at contract lines 397-400 applies: treating every unmaterialized `finish_reply`
as unsettled would wedge reconciliation when its exact result stays permanently
missing; the repeated missing-ref trace preserves that compatibility limitation.

Proposed domain wording distinguishes **observed slot state** (`pending`,
`committed`, `refused`) from a **derived response obligation** (a `finish_reply`
intent whose response is not yet materialized). A single settled boolean cannot
express both facts. No glossary or production schema changes are included.

## Watcher compatibility limit

The earlier `coordination/v1` document described `watch --config <path>` as
supported alongside `--config-dir`; landed v5 corrects that description.
At this baseline, the parser accepts
`--config`, but the watcher branch requires `--config-dir`; the single-binding
invocation exits 2 with `watch requires --config-dir`. The directory watcher
works. The golden fixture preserves both observations. The lead was notified
before dependent edits; this slice does not add single-binding support.

## coordination/v5 J-F1/J-F2 extension

The v5 traces retain the existing oracle's distinct policies:

| Trace | Observed boundary |
| --- | --- |
| `v5_explicit_respond` | `--version 01` refuses with `INVALID_REQUEST`/2; a missing result `version` or truthy non-dict result returns `INVALID_HOST`/7 before sending. The explicit response slot has no `expected_ref` and accepts an otherwise successful publication with a mismatched ref/event ID. |
| `v5_finish_reply_stale_replay` | A replay can return `STALE_SESSION`/4 while both original slots remain committed; their status/code remain separate from `replay_code`. |
| `v5_materialized_identity_conflict` | A conflict raised inside `journal_finish_reply` exits 7, distinct from the inline immutable-slot conflict's exit 4. |
| `v5_compact_launcher` | The real installer stages a bare `cairn-inbox` launcher in a disposable home; its `CAIRN_INBOX_ROOT` selects the staged complete runtime and binding. |
| `v5_compact_stale_settle`, `v5_compact_confirmation`, `v5_compact_inner_exits` | Missing/corrupt/non-object settle journals re-raise `STALE_LEASE`/7. An unconfirmed inner success returns `CONFIRMATION_MISMATCH`/7; inner failures retain exits 1, 2, 3, 4 and 7. |
| `v5_passthrough_streams`, `v5_input_limits` | Single-slot stdout/stderr/exit pass through even for non-JSON exit 0 or `ok: true` with nonzero exit. Inbox input permits 1 MiB decoded characters; raw hook input is bounded in bytes. |
| `v5_agent_only_seeding` | Seeding adopts matching agent IDs across execution IDs, including final notice/response journals and the ownerless argv fallback. Flush subsequently prunes final entries without sending. |
| `v5_reader_policies` | The five direct readers distinguish missing files, malformed JSON, directory-read errors, non-object JSON, falsy/truthy non-dict completion, malformed session/slot, absent attempt ID, and extension keys (`ClaimOnly == false`). The compact and legacy unindexed readers' additional error/extension cells are pinned by `v6_reader_remaining_cells`, described below. |
| `v5_hook_watch_corruption`, `v5_activation_refusal` | Missing `attempt_id` causes ordinary hook exit 1 and a per-session watcher diagnostic; inside `record_activation_refusal`, its `KeyError` is caught and preserves `NATIVE_PROMPT_REFUSED`/2. Non-dict session/ownerless slot raises escaping `AttributeError`; an indexed non-object journal terminates the shared watcher and ordinary hook with exit 1. |
| `v5_flush_scopes`, `v5_settlement_taxonomy` | Both flush scopes replay historical pending journals; only `all` gates on their uncertainty. Classified failures, an unsettled non-exception return and escaping `AttributeError` retain their distinct strict/non-strict settlement and persistence outcomes. |
| `v5_prepare_idle_persistent_ref` | Three actual `watch_inbox` cycles re-send a committed completion whose exact result remains absent. `prepare_idle_wake` reaches reconciliation through its retained receipt, inspection reports settled, and the wake marker clears while the old response remains unmaterialized. |
| `v5_recall_wake_stdout` | A real admitted recall hook preserves compact UTF-8 stdout, key order, raw U+2028/U+2029, `<>&`, and source text containing `1.25`; no synthetic numeric field is added to the hook result. |

There is one direct `settled_journal_index` call inside
`reconcile_rejected_wake`. The two entry paths tested here are
`record_activation_refusal` and `prepare_idle_wake`, the latter after its normal
watcher flush. Persistent missing-ref recovery leaves completion committed and
response absent through every observed cycle; the test establishes three
repetitions, and the source loop supplies the unbounded-retry inference.

The activation fixture verifies the receipt's actual SHA-256 and live positive
process identity before recording selected facts. Escaping tracebacks are
represented by their presence and final exception line; diagnostic lines and
stdout are retained exactly, with only the declared temporary paths normalized.
For non-dict session/ownerless-slot failures, the owner-only refusal receipt and
state `wake_rejection` are written before legacy seeding raises `AttributeError`.
They therefore coexist with exit 1, rather than a native rejection's exit 2.
The direct rejected-wake fixtures require a live test-runner parent PID >= 2;
that precondition is asserted, and no process identity is invented for PID-1 or
reparented runners.

## Round 2 preservation fixes

`v5_recall_wake_stdout` previously retained a path-dependent
`remaining_memory_bytes` value after replacing the embedded paths. This broke
the golden outside the author's checkout. The test now validates the counter
using the actual unnormalized output:

1. Decode the raw stdout envelope and the final `{"selected":...}` recall object
   inside `hookSpecificOutput.additionalContext`. Require the original remaining
   count to have type `int`.
2. Set that recall object's `remaining_memory_bytes` to the configured budget
   `B` (9500 in this fixture), rebuild its embedded JSON and then the outer
   envelope with `ensure_ascii=False, separators=(',', ':')`, and append `\n`.
   Call this provisional raw stdout `P`.
3. Assert `original_remaining == B - len(P.encode('utf-8'))`, matching
   `memory.render_agent_candidates`' provisional budget measurement and
   `inbox_recall.deliver`'s complete wire-envelope cost.
4. Only after that assertion, replace this one field with
   `$CHECKED_REMAINING_MEMORY_BYTES`, rebuild the envelope identically, and apply
   the existing declared path normalization. No other byte counter is normalized.

This preserves the actual arithmetic check across checkout, temporary-root and
interpreter path lengths. The bare `/2` launcher is also invoked with a decoy
caller `CAIRN_INBOX_ROOT`; its existing golden is unchanged. The refusal matrix
now asserts exact equality with `INTENT_FINAL_REFUSALS`.

| New trace | Required observations |
| --- | --- |
| `v6_busy_flush_scopes` | A real BSD `flock` holds the active attempt's intent lock. Both `flush_inbox_intents('attempt')` and `('all')` return false, retain the journal index, and send zero API commands. |
| `v6_finish_reply_priority` | Completion `STALE_LEASE` gives exit 4 with no response slot and no pending reply; lost completion gives `API_CONNECTION_FAILED`/7 and a pending reply; publication `DESTINATION_PROHIBITED` gives exit 4 and a refused response; changed input after committed `finish-reply` gives local `IDEMPOTENCY_CONFLICT`/4 with no API contact. Exact envelopes and slot states are retained. |
| `v6_reader_remaining_cells` | The real hook's legacy unindexed reader retains wake/receipt with no API call for malformed JSON (`JSONDecodeError`/2), directory read (`OtherError`/2), top-level list (`AttributeError`/1 with traceback), and an agent-matched extension-key journal (2). Compact settle re-raises `STALE_LEASE`/7 for a directory, but permits an extension on a matching committed ack and sends `event-renew`, `history`, `ack`. |

No numeric JSON float reaches the current pinned hook envelope: `1.25` remains
source text, so numeric-format parity is not claimed. The `prepare_idle_wake`
entry after a Busy flush and the smaller optional `/2` grammar negatives remain
outside these three added traces.

**J-R1 O-2 remains a required follow-up:** `v5_activation_refusal` and
`v5_recall_wake_stdout` must be rerun unchanged after F-04 F-B3's reviewed
original-event/checked-config-snapshot plumbing lands. This candidate verifies
the pre-F-04 oracle at `a6ff2a9`; it does not claim that later verification.
The updated O-2 SHA-256 is
`0059dfcbe46dde7d3ecf4b9523d848cbb1fc028fc877fa11c40c6d982d83da69`,
also retained in `post_f04_requirement`. This exact recipe, run from the repo
root, hashes the object mapping both trace names to their normalized values:

```python
import hashlib
import json
from pathlib import Path

fixture = json.loads(Path("scripts/fixtures/inbox_characterization_v1.json").read_text())
names = ["v5_activation_refusal", "v5_recall_wake_stdout"]
payload = {name: fixture["traces"][name] for name in names}
wire = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
print(hashlib.sha256(wire.encode("utf-8")).hexdigest())
```

V6's C1/C3 J-F1 cells join O-2 as post-F-04 F-B3/F-04/J-02 obligations, not
claims of this retained `/2` capture: `CompatibilityPreflightError` with zero
effects across hook/inbox/drain/compact (including excluded callbacks), joined
Claude-wake preflight/custody, L/V compact root selection through `--config`'s
grandparent when the environment is unset, wrong-root/config/config-swap
refusals, caller ancestry and native-env `NOT_THIS_SESSION` refusals, inspect
renewal and advance next-cue rendering, and no hook timeout on inbox modes.

Contract-owner erratum for J-02: v6 line 221 mentions `10s INTENT_LOCK_TIMEOUT`,
but the pinned source has no such identifier. Its actual bounds remain 30s
explicit lock / 30s send and 0s watcher lock / 10s send, as the unchanged lock
and deadline traces establish. This report does not edit the shared contract.

## Validation and limits

Run these local commands from the candidate checkout:

```sh
python3 -B -m unittest discover -s scripts -p test_inbox_characterization.py
python3 -B -m unittest discover -s scripts -p test_inbox_recovery.py
python3 -B -m unittest discover -s scripts -p test_inbox_drain.py
python3 -B -m unittest discover -s integrations/lifecycle -p test_native_session.py
python3 -B -m unittest discover -s scripts -p test_wake_refusal.py
python3 -B -m unittest discover -s scripts -p test_installed_lifecycle_bundle.py
make check
python3 -B scripts/verify_docs.py --tier 1,2,3,4
```

The Round 2 run covers 35 characterization, 53 recovery, 8 drain, 10 NativeSession,
15 wake-refusal and 7 installed-bundle tests: 128 tests in the primary run.
All passed with no skips. `make check` and documentation tiers 1-4 passed.
The interpreter was Python 3.14.4. The requested filename
`test_installed_coordination_bundle.py` does not exist in this base; the existing
`test_installed_lifecycle_bundle.py` supplies the seven installed-bundle tests.

| Required environment group | Characterization result |
| --- | --- |
| (a) Author worktree, `/usr/bin/python3` | 35/35, no skips |
| (b) Independent 24-byte-path `git archive` export with the three candidate files overlaid | 35/35, no skips; the real main checkout was not written |
| (c) Author worktree with a long, non-default `TMPDIR` | 35/35, no skips |
| (d) Explicit interpreter paths | `/usr/bin/python3.14`: 35/35; `/usr/bin/python3`: 35/35, both with no skips |

These are five invocations across the four requested groups, all against the
same frozen trace values. The three added traces pass in each environment.
The other 31 original trace value byte spans are checked against `4337d755`.

The characterization suite is discovered by the existing `make test` Python
discovery pattern. It uses real subprocesses and filesystem locks, a virtual
clock for the contended 30s wait, and the existing synthetic API executable.
It does not contact an operational database, provider, or live harness. No store
or production lifecycle code changes are made; database integration and live
delivery acceptance are outside this characterization's claims.

Rollback is removal of the test, golden fixture and this document. Candidate
test counts and the commit submitted for review are recorded in the Cairn result
and Plane comments; the items stay In Progress until review and integration.

## Review method

Repository instructions and the assigned characterization contract govern the
scope. Pincite's validated release `6deb1af41d41075b28577bcf2126c9db90d0e7c0`
supplied the Round 2 evidence packet `pkt-8eb310b83fb1a488`, SHA-256
`8eb310b83fb1a488dd826768d260c46ae197d7d098330b6b058963d1eba52262`
(corpus `corpus-2026-07-12-84d6cdfc0e9f`, doctrine
`doctrine-2694f0450e78ff1e`, retriever `retriever-8b654e47fcb1be28`). Its
`implementation-risk-driven-tests`, `universal-preserve-behavior-by-default`, and
`universal-separate-semantic-structural-change` concepts support keeping the
observable baseline separate from the proposed repair. Authority extends to
tests and selected documentation; production edits and landing before both
reviews remain excluded.

Thirteen broader evidence obligations remain nonmaterial to this bounded result:
desired accepted behavior and incident history are not asserted by an oracle
capture; an inventory of every project contract is not claimed; the six
automated-transformation tool-trust obligations do not apply to a manually edited
test-only slice with byte-identical production sources; measured operational
cost/risk, intervention cost, a general latent-risk assessment, and the
leave-code-alone decision procedure are outside this slice.
They remain open for the corresponding repair
or replacement decision. The alternatives here are to preserve and report the
observed behavior or to repair it in a separately authorized slice; the latter
is deferred to J-03 and its reviewers.
