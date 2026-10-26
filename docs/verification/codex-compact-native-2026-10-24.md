# Native Codex compact with a pending notice (2026-10-24)

The bounded fixture in [`check_codex_recovery_fixture_disposable.py`](../../scripts/check_codex_recovery_fixture_disposable.py)
uses Codex 0.164.0, a private `CODEX_HOME`, disposable PostgreSQL, and a
loopback Responses stub. Both staged coordination and memory hook command
families are trusted and wrapped. The lifecycle selector is a fixture-only
local executable. The run contacted no external provider and observed no
provider usage.

The sequence registered a native session and waited for its matching
`turn/completed`. It then published one notice, confirmed that the delivery
was pending with zero attempts, and called `thread/compact/start`. The method
returned an empty acceptance object. The fixture drained app-server
notifications until the compact turn reached its matching `turn/completed`.
The compact `PreCompact` hook exited 0. The same notice remained pending with
the same delivery ID and zero attempts through that terminal boundary.

On the continuation, `SessionStart(source=compact)` and
`UserPromptSubmit` both exited 0. The compact SessionStart has no turn ID. The
coordination path therefore admitted the notice at session scope, with no
`native_turn_id`; the Store claim contract requires the request ID and session,
while a native turn binding is optional. The fixture preserves that null value
and does not rewrite the attempt as turn-bound. The continuation then read the
exact whole source and settled the same delivery as handled with one attempt.
Five scripted Responses requests reached the local stub.

The compact notifier initially appeared absent because the fixture's observer
moved notifications into a side queue only while an RPC call was waiting. The
reader now routes notifications continuously. This was a fixture observer bug,
not a Codex or Cairn source defect.

This result establishes native compact sequencing, lifecycle callbacks, and
pending notice identity through compact completion in this fixture. It does
not establish accepted model work, provider use or cost, broader memory value,
or a canary and rollback. CAIRN-224 remains open for those acceptance claims.

## Claimed source at the manual compact boundary (2026-10-25)

The disposable runner now adds a second native sequence for the gap in
[`check_codex_recovery_fixture_disposable.py`](../../scripts/check_codex_recovery_fixture_disposable.py).
It registers a Codex app-server thread, admits a notice on a real
`UserPromptSubmit` turn, reads the exact Store source through the compact read
command, and leaves the attempt unsettled. The fixture waits for the matching
native `turn/completed` and successful `Stop` hook before calling
`thread/compact/start` on that same still-live app-server thread. The compact
turn reaches its own `turn/completed` with `PreCompact`; the continuation has a
different native turn ID and no turn ID on `SessionStart(source=compact)`.

The Store observation after the source-reading turn is already `failed`, with
one attempt and code `processing_failed`. The verified hook ordering is
source-reading `Stop` exit before the compact turn's `PreCompact` entry. The
same event and delivery remain failed after compact completion; the
continuation does not claim or replay them. Thus the installed native
manual-compact API exposes a between-turn boundary: an unsettled
turn-bound attempt is reconciled before manual compaction starts. The same
delivery/attempt cannot remain leased across this path, and the continuation
cannot settle the original attempt. This does not establish whether automatic
context compaction can run inside a still-active Codex turn.

The safe recovery path is to inspect the failed delivery and any possible prior
effects, reconcile outstanding host work, then use the existing explicit
operator review and `event-reissue` workflow if the task should run again.
Reissue creates a successor event with the exact source version and a fresh
delivery/attempt; it is not an automatic replay. See
[`event-recovery.md`](../event-recovery.md#reissue-a-failed-request). The
fixture verifies transport, exact source identity, native hook order, and Store
state only. It uses a scripted loopback Responses server with disposable
PostgreSQL and no external provider; it does not test task acceptance, provider
usage, or canary/rollback. Automatic compaction is characterized separately
below.

## Automatic in-turn compaction with a claimed source (2026-10-25)

The disposable runner now also has an `auto-in-turn-claimed` scenario in
[`check_codex_compact_native.py`](../../scripts/check_codex_compact_native.py).
It sets a private Codex configuration with a 1,000-token context window and a
50-token automatic-compaction threshold. On the same active native turn that
claims a notice, a scripted loopback Responses response reports 200 input tokens
and returns a tool call that reads the exact Store source. Codex executes the
read, then reports a native `contextCompaction` item start and completion. The
matching `PreCompact` hook carries the active turn ID and exits 0 before Stop.
The exact source was read before that hook; the request did not inline it first.

The same event, delivery, source ref, Store attempt and original native turn
binding remain unchanged. Store status is `leased` with one attempt both at
PreCompact and after automatic compact SessionStart. The memory SessionStart
hook exits 0. The coordination SessionStart hook exits 1 with
`NATIVE_TURN_MISMATCH`: that hook has no `turn_id`, while the live attempt is
bound to the active turn. The fixture records this as an unsupported refresh
boundary; it does not clear, rebind, settle or replay the attempt. The native
turn later reaches Stop, which exits 0 and records the still-unsettled delivery
as `failed` / `processing_failed` with one attempt. No false handled outcome or
automatic replay occurs.

This is a provider-free transport and Store result, not accepted model work.
The threshold and usage are scripted; no external provider or provider usage
was observed. The run used the installed Codex 0.164.0 binary identified
below; it demonstrates that this binary can automatically compact during a
live turn and characterizes the coordination hook refusal when its compact
SessionStart lacks a turn ID. It does not prove that a same-turn owner
binding can be recovered unambiguously from that callback, nor that the task
continues successfully after it. Keep CAIRN-224 open for a safe correlation
contract, paired accepted-task/provider-usage evidence, and effective
canary/rollback.

## Turn-ID prototype attribution correction (2026-10-25)

The missing-`turn_id` observation above came from the installed binary at
`/home/halbritt/.local/bin/codex` (SHA-256
`abab8f847d2e2013560057cbe224d5c09eca0b8e48a3065cfb9a7d9f2e3867b1`). It did
not invoke the separate owner-only prototype wrapper at
`/home/halbritt/.cache/codex-cairn224-artifact/codex` (SHA-256
`7ab3733aab2739d529bed8f4d7b1037f60935df6aeedf832555e086227744429`). An
earlier run of the prototype fixture stopped with `StopIteration` before
`PreCompact`; that run therefore did not observe the prototype's compact
SessionStart payload. The prior missing-ID observation must not be attributed
to the prototype.

The prototype patch targets pinned Codex source `rust-v0.164.0` at commit
`6aaac694803004a63c6f393d153c96d22521a6b9` (patch SHA-256
`68bf00cdb0873d08eeac459e762f3590abd79f5e134150dc08e131d3a47f81cd`). Static
source tracing shows automatic compact SessionStart is queued from the active
turn path, `run_pending_session_start_hooks` receives that existing
`TurnContext`, and the hook dispatcher receives its internal `sub_id`. The
patch serializes that dispatcher-provided value as optional `turn_id` in the
SessionStart hook input; it does not accept a caller-selected identity. On the
pinned, patched source, all 184 `codex-hooks` tests passed, including the
turn-ID-present/absent serializer tests, scoped input test, and generated
schema fixture test. This establishes source and serializer behavior only.
A patched app-server was subsequently rebuilt from this commit and patch.
The binary identified as Codex 0.164.0 and had SHA-256
`b8ad1db40f7f547df6fc89190e3a1feb1355d438eca45678fddcafd746127235`. A
provider-free fixture used that binary under `bwrap --unshare-net`, with
synthetic principals, disposable PostgreSQL, and a loopback Responses stub.
It stopped during native-session registration, before the compact scenario.

The fixture's fixed diagnostic recorded `config_home_match=false` on
coordination `SessionStart`, `UserPromptSubmit`, and `Stop`; coordination and
lifecycle disabled flags and workspace exclusion were false, and the separate
owner-process classification matched the configured name. The hooks exited 0,
but no private hook state file or registered session appeared. The disposable
`agents list` query succeeded and returned no matching native session; the
registration Stop marker was observed. The active hook home value itself was
not retained.

Current `coordination.py` returns an empty success when the active
`CODEX_HOME` differs from the hook configuration's `config_home`, before it
looks up or registers the native session. This matches the recorded branch
class and explains the absence of hook state despite zero exit codes. The
fixture does not identify why the hook process's active home differed from
its configured private home. It did not observe compact `SessionStart`, so
patched turn-ID propagation, continuation, and settlement remain unverified.
Cairn keeps the fail-closed `NATIVE_TURN_MISMATCH` behavior; no task continuity
or settlement is claimed here. No external provider, real auth, production
database, or OpenRouter was used.

## Patched Codex compact callback serializer probe (2026-10-25)

A separate serializer-only runtime probe used the rebuilt pinned app-server
binary identified above (SHA-256
`b8ad1db40f7f547df6fc89190e3a1feb1355d438eca45678fddcafd746127235`). It
used a private `CODEX_HOME`, one trusted minimal `SessionStart` hook that
retained only source and SHA-256 hashes of session/turn IDs, and a loopback
Responses server. Its scripted turn followed the compact fixture's shape: a
1,000-token context window, 50-token compaction threshold, and a custom tool
call response reporting 200 input tokens. The app-server emitted native
`contextCompaction` item start and completion notifications, and the active
turn completed.

The private hook recorded `source=compact`, `turn_id_present=true`, and
turn-ID SHA-256
`30706ca6ac7f239b86643c57f76840acd330aab39c8171d0df72e193d966de9`. This
matches the active `turn/start` response's turn-ID SHA-256. The compact hook
also recorded session-ID SHA-256
`1218bc01aafc6e7ff4e7ffc11d571641e2c96e32dbdc198463d3065f8cbf8e40`. The
single run used four loopback requests, with no external provider call. This
proves that this patched app-server build serialized the active turn ID into
its automatic compact `SessionStart` callback in this fixture.

This is a Codex callback serializer/runtime result only. It did not register a
Cairn session or use the Cairn Store, inbox, or coordination hook, and it does
not prove Cairn continuity, safe attempt recovery, accepted task outcome,
provider usage, or measured usefulness. The section below resolves the full
Cairn fixture registration boundary and verifies both unsettled fail-closed Stop
reconciliation and same-turn post-compaction settlement.

## Full Cairn fixture proof with patched compact SessionStart turn ID (2026-10-26)

Source inspection identified three fixture-level causes of the earlier
registration boundary and continuation check when running the patched
`codex-app-server` binary (SHA-256
`b8ad1db40f7f547df6fc89190e3a1feb1355d438eca45678fddcafd746127235`) in
[`check_codex_compact_native.py`](../../scripts/check_codex_compact_native.py):

1. The previously staged `cairn224-runtime-explicit-home/codex/hooks.json` had
   been copied from `cairn224-inbox-gate-signal`, leaving its hook command paths
   pointing at `cairn224-inbox-gate-signal/codex/...` (whose `config_home` was
   `/home/halbritt/.codex`). Because `check_codex_compact_native.py` replaces
   `str(source_home)` with `str(home)`, those mismatched paths were not rewritten
   and `coordination.py` returned early with `config_home_match=false`. Staging
   a fresh `.codex` candidate via `stage_account(..., "codex", profile="full")`
   aligned `source_home` with `hooks.json`.
2. When bind-mounting the standalone `codex-app-server` binary into a disposable
   directory, its sibling `codex-code-mode-host` binary must be present in the
   same directory so `functions.exec` (`js_repl`) can execute `cairn-inbox` tool
   calls.
3. In [`check_codex_compact_native.py`](../../scripts/check_codex_compact_native.py),
   `Handler.do_POST` previously hardcoded
   `compact_outcomes["coordination"].get("exit_code") == 1` even when
   `expect_compact_turn_id=True`, whereas `verify(rpc)` checked `exit_code == 0`
   when `expect_compact_turn_id=True`. Updating `Handler.do_POST` to honor
   `expect_compact_turn_id` and adding `settle_after_auto_compact` enabled
   provider-free verification of both post-compaction outcomes.

Focused unit tests (`PYTHONPATH=scripts python3 -m unittest scripts/test_codex_compact_context_lookup.py`,
13 tests) and a fresh `bwrap --unshare-net` disposable run with empty fake auth
(`{}`), synthetic principals, disposable PostgreSQL/API, and the patched
`codex-app-server` binary (`b8ad1db40f7f547df6fc89190e3a1feb1355d438eca45678fddcafd746127235`,
receipts recorded in `/home/halbritt/.cache/codex-cairn224-runtime-proof/manifest.json`)
verified both modes without any external provider call:

- **Unsettled Stop fail-closed reconciliation**
  (`expect_compact_turn_id=True, settle_after_auto_compact=False`):
  `result="PASS"`, `native_automatic_compaction=true`, `exact_source_read=true`,
  `source_order=["exact_source_read", "PreCompact", "SessionStart(compact)"]`,
  `compact_sessionstart_observed=true`,
  `compact_sessionstart_turn_id_matches_active=true`,
  `compact_sessionstart_outcomes={"coordination": {"error_code": null, "exit_code": 0}, "memory": {"error_code": null, "exit_code": 0}}`,
  `delivery_state_during_compaction="leased"`,
  `delivery_state_after_compact_sessionstart="leased"`,
  `final_delivery_state="failed"`, `final_delivery_code="processing_failed"`,
  `final_attempts=1`, `false_settlement=false`, `loopback_response_requests=4`,
  `provider_call=false`.
- **Same-turn post-compaction `handled` settlement**
  (`expect_compact_turn_id=True, settle_after_auto_compact=True`):
  `result="PASS"`, `native_automatic_compaction=true`, `exact_source_read=true`,
  `settlement_after_auto_compact=true`,
  `source_order=["exact_source_read", "PreCompact", "SessionStart(compact)"]`,
  `compact_sessionstart_observed=true`,
  `compact_sessionstart_turn_id_matches_active=true`,
  `compact_sessionstart_outcomes={"coordination": {"error_code": null, "exit_code": 0}, "memory": {"error_code": null, "exit_code": 0}}`,
  `delivery_state_during_compaction="leased"`,
  `delivery_state_after_compact_sessionstart="leased"`,
  `final_delivery_state="handled"`, `final_delivery_code=null`,
  `final_attempts=1`, `false_settlement=false`, `loopback_response_requests=6`,
  `provider_call=false`.

This completes the provider-free full Cairn fixture proof for CAIRN-224 across
both stock Codex (`NATIVE_TURN_MISMATCH` fail-closed when compact `SessionStart`
omits `turn_id`) and patched Codex (`turn_id` matching the active turn,
preserving the leased attempt across automatic compaction and supporting both
same-turn `handled` settlement and fail-closed `Stop` reconciliation). Live
paired provider usage and canary activation remain deferred under the
[token-efficiency gate](token-efficiency-gate-2026-10-24.md).
