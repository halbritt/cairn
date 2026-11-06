# J-01 inbox journal characterization

CAIRN-332 and CAIRN-276, `coordination/v1`, 2026-11-06. This is a
characterization of the existing Python runtime for the CAIRN-301 migration.
It adds synthetic tests and fixtures; it does not repair the runtime, accept a
Go replacement, or establish installed behavior. Independent J-R1 and J-R2
reviews and the campaign integration gate remain required.

The oracle is `integrations/lifecycle/coordination.py` at baseline
`db45c4474fa7911b5569f2f55ebf2bd19c9a81bb`. The fixture records its SHA-256.
The assigned contract was read from the Coordination lead's commit
`36b106f52c9ed88938def61e90fb9d6ad7bf6020`,
`docs/plans/hybrid-runtime-contracts/coordination.md`.

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

Only temporary root paths, the oracle/interpreter paths, and the three named
timestamp fields are normalized. Randomness is fixed at the generation boundary
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
observed production incident or lost work. Treating this obligation as unsettled
in Go requires the separately labelled J-03 repair and independent review.

Proposed domain wording distinguishes **observed slot state** (`pending`,
`committed`, `refused`) from a **derived response obligation** (a `finish_reply`
intent whose response is not yet materialized). A single settled boolean cannot
express both facts. No glossary or production schema changes are included.

## Contract mismatch: single-binding watcher

The assigned `coordination/v1` document describes `watch --config <path>` as
supported alongside `--config-dir`. At this baseline, the parser accepts
`--config`, but the watcher branch requires `--config-dir`; the single-binding
invocation exits 2 with `watch requires --config-dir`. The directory watcher
works. The golden fixture preserves both observations. The lead was notified
before dependent edits; this slice does not add single-binding support.

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
```

The local run passed 15 characterization, 53 recovery, 8 drain, 10 NativeSession,
15 wake-refusal and 7 installed-bundle tests: 108 tests, with none skipped.
`make check` passed. The interpreter was Python 3.14.4. After extending the earlier
hold-release trace, the 15 characterization tests passed again.

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
supplied packet `pkt-b71f92561d98d539`, SHA-256
`b71f92561d98d53902af559e75d301a15b3b883e69cacba9d57085e406f27719`
(corpus `corpus-2026-07-12-84d6cdfc0e9f`, doctrine
`doctrine-2694f0450e78ff1e`, retriever `retriever-8b654e47fcb1be28`). Its
`implementation-risk-driven-tests`, `universal-preserve-behavior-by-default`, and
`universal-separate-semantic-structural-change` concepts support keeping the
observable baseline separate from the proposed repair. Authority extends to
tests and selected documentation; production edits and landing before both
reviews remain excluded.

Eight broader evidence obligations remain nonmaterial to this bounded result:
desired accepted behavior and incident history are not asserted by an oracle
capture; an inventory of every mutation path and every project contract is not
claimed; measured operational cost/risk, intervention cost, a general latent-risk
assessment, and the leave-code-alone decision procedure are outside a slice that
does not change production code. They remain open for the corresponding repair
or replacement decision. The alternatives here are to preserve and report the
observed behavior or to repair it in a separately authorized slice; the latter
is deferred to J-03 and its reviewers.
