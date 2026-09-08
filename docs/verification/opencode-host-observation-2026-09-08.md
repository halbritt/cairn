# OpenCode host observation — 2026-09-08

The recurrence trial now runs through real host-observed attempts under per-arm
observer identities. This connects the existing host-attempt API to a concrete
OpenCode controller. U1 remains partial: this verification uses a synthetic model
endpoint and does not establish new model usefulness or native Striatum delivery.

## Implemented path

`scripts/trial_host.py` owns one private attempt directory, actual wrapper startup,
pending spawn/terminal observations and process-end metadata. It releases the
wrapper's startup pipe only after the API confirms that exact attempt. The
controller checks its own precompiled package and executes with the same request
ID, scope and pins, then checks the returned receipt/seal. Operator replay cannot
inspect observer-owned receipts and is no longer used for that check.

`scripts/trial-opencode-recurrence.py` provisions one hosted observer per arm and
uses that observer for execution, host observations, evaluator evidence,
assessment and citations. Operator setup/promotion and aggregate reporting stay
separate. The child sandbox and memory budgets are unchanged. The API client has
an unusable database address; the real model filesystem cannot access the
observer token.

A returned patch is a corresponding result, independent of correctness. Exit zero
without a patch becomes `no_result`. Model-authored citations remain testimony
about citation, not proof of causal influence. See the
[trial protocol](../experiments/opencode-recurrence.md) for recovery semantics.

## Verification

- A real child cannot execute before spawn confirmation. Exclusive intent and
  attempt directories refuse a repeated launch. JSON scope preserves non-ASCII
  text and process streams remain bytes.
- Injected lost spawn/terminal responses retain exact pending requests. Lost
  spawn response never releases the child. Timeout cleanup observes the owned
  process end. Postprocess failures retain a terminal without sending or replacing
  an existing request. Zero exit alone cannot manufacture a corresponding result.
- The disposable PostgreSQL/Unix API check exercises a real hosted-profile wrapper,
  matching precompiled receipt/seal, owner-only outcome inspection, idempotent
  terminal retry and a separate unknown task assessment.
- The full controller used the installed OpenCode executable with a synthetic
  loopback endpoint. It selected one scoped lesson, launched the real wrapper,
  matched the attempt and process outcome, and observed exit zero with no patch.
  The host recorded `no_result`; the independently run necessary repair condition
  failed and the bounded task assessment was rejected. The repaired/reference
  preflight still passed while the broken input failed.

[Exact metadata](opencode-host-observation-2026-09-08.json) includes the exercised
source/binary hashes and fixture receipt/attempt/outcome identities. The full
fixture ran before the final postprocess-recovery addition, which has separate
fault-test coverage, and before progress-event wording changed. No model was
called. The two loopback requests prove harness contact only.

Python tests and `make test-integration check` pass. A new precompile integration
assertion initially used an empty query while the CLI defaults to the prompt;
it correctly received `IDEMPOTENCY_CONFLICT`. The fixture was corrected to use
identical intent. This was a test setup error, not a production compiler defect.
No operational database, installed executable, service or sibling tree changed.
The private trial store was stopped after dumping its evidence.

## Remaining limits

No daemon reconciles a host killed before it can observe termination. Inspect
real state before terminal recovery; a journal or PID is not proof of life or
death. A pending observation retries only its exact API payload and never
releases another process. A later model trial must report the new observer
attribution and protocol alongside prior results; this fixture does not add
another successful repair to the usefulness evidence.

A subsequent [real-model trial](observed-model-failure-2026-09-08.md) now verifies
timeout finalization and independent review of a failed candidate through this
host. Its model and repair results remain separate from this synthetic-provider
fixture.
