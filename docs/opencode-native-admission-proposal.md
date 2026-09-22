# OpenCode native admission: proposed atomic change and tests (design only)

Status: proposal for root review; native admission NOT implemented here, per task
boundary. Target source: github.com/anomalyco/opencode tag v1.18.31 (commit 014614d).

## Verified contract today (source)

- `prompt_async` handler (`server/routes/instance/httpapi/handlers/session.ts:311`)
  forks `promptSvc.prompt(...)`, swallows all errors into a session error event, and
  always returns HTTP 204.
- `prompt()` (`session/prompt.ts:1052`) persists the user message via
  `createUserMessage(input)` BEFORE `loop({sessionID})` at line 1346.
- `loop()` calls `SessionRunState.ensureRunning` (`session/run-state.ts`); the runner
  (`effect/runner.ts` `ensureRunning`) on `Running`/`ShellThenRun` returns
  `awaitDone(st.run.done)` — the submitted work effect is discarded; the caller
  resolves with the in-flight run's result. Only the `Shell` state defers real work.

## Consequence for a wake submitted after an idle precheck (Cairn bridge race)

Three indistinguishable outcomes behind one 204:

1. admitted cleanly when the runner is Idle;
2. merged-by-adoption when the in-flight runLoop is between steps (the loop reads
   `MessageV2.latest` each step, so the persisted wake message becomes the next user
   turn of the OWNER's active run — observed live);
3. silently dropped when the in-flight runLoop breaks before the wake lands: 204,
   wake user message persisted as an orphan, no assistant reply, no error event,
   while the bridge records queued:true/started:true and suppresses retry.

## Proposed atomic change (upstream, minimal)

Make `prompt_async` reserve the runner BEFORE persisting anything, and surface
refusal over HTTP:

1. Add `SessionRunState.tryBegin(sessionID, work)` (or a `reserve` flag on
   `ensureRunning`): under the same `SynchronizedRef.modifyEffect`,
   - `Idle`: transition to `Running` synchronously (create the run handle, set
     status busy via the existing `onBusy`), then execute `work`;
   - `Running`/`ShellThenRun`: fail with the existing `Session.BusyError` without
     touching state (no `awaitDone`).
2. Reorder the `prompt_async` handler path so `prompt(..., deferMessages=true)` does
   NOT call `createUserMessage` until after a successful reservation; simplest shape:
   a new prompt-mode where `createUserMessage` moves inside the reserved work.
3. Map the refusal with the existing `SessionError.mapBusy` to HTTP 409
   `SessionBusyError` for `prompt_async` (instead of swallow-to-event + 204). No
   session error event is needed for the refusal; the HTTP status is the contract.
4. Cancellation/`cancel()` releases the reservation exactly as runner cancellation
   does today; `Shell`/`ShellThenRun` behavior is unchanged.

Alternative smaller variant: keep 204 semantics but make `ensureRunning` on
`Running` enqueue the work as a pending run (executed after the current run) and
publish a `session.queued` event. This changes merge semantics instead of refusing;
it preserves wake delivery but still hides admission truth from HTTP callers, so the
refusing variant is preferred for the Cairn contract.

## Required tests (native source, after root approval)

1. `prompt_async` while a run is active returns HTTP 409 `SessionBusyError` and the
   session message list is unchanged (no user message persisted).
2. `prompt_async` on an idle session persists the message and runs normally (204).
3. Status reads busy immediately after reservation (no idle window between
   reservation and run start).
4. Cancellation while reserved releases the reservation and returns the session to
   idle without assistant output.
5. `shell` then `prompt_async`: refused (409) rather than queued behind the shell
   (or queued, per root's chosen variant — tests pin whichever is selected).
6. Existing suite: all current session/server tests stay green; the swallowed-error
   event for prompt failures is retained for non-busy failures.

## Harness note

`scripts/opencode-native-harness/` provides the fail-closed fixture (loopback-only
netns, scrubbed credentials, canary-checked mock model) so these tests and any Cairn
side probing run without external provider access.
