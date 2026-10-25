# CAIRN-220 native wake join probe, 2026-10-25

This probe did not establish the watcher-to-Store join. It must not be used as
CAIRN-220 acceptance evidence.

## Attempt record

The first run stopped during outer sandbox command construction, before bwrap
started. The exact error was `NameError: name 'profile' is not defined`.
The 0600 private trace is
`~/.cache/cairn-220-native-wake-proof/run-full-join-initial-setup-failure.json`.

One targeted fix bound `profile` to the fixture's private Codex home. The single
retry started its disposable PostgreSQL cluster and local Cairn API, then
returned exit code 2 without creating the inside-runner boundary report. No
queue receipt, `item/started`, `UserPromptSubmit`, Store attempt, or local
Responses request was observed. The 0600 private trace is
`~/.cache/cairn-220-native-wake-proof/run-full-join-3564263e54004114bb8ab9b14129c22f.json`.
It preserves the outer exit code and stderr SHA-256, but not stderr text. The
exact bwrap diagnostic is therefore unavailable.

## Next-probe prerequisite

The exit-2 source remains unconfirmed because stderr was not retained. Static
review found a likely bootstrap path mismatch: bwrap mounts the worktree at
`/repo`, but the command invokes Python with the original host worktree path
for `check_codex_app_server_wake_join.py`, which is not mounted inside. A
separate provider-free inert bwrap preflight using `runtime_sandbox()`, the
same `/opt/codex-bin` read-only bind, and `/usr/bin/true` exited 0, so the
missing `/opt` directory is not established as a cause. Before another bounded
run, invoke the mounted path `/repo/scripts/check_codex_app_server_wake_join.py`
and capture bounded, path-redacted outer stderr in the owner-only 0600 trace
before any assertions or disposable cleanup. Verify the outer bootstrap
reaches the runner before relying on application-level diagnostics.

The intended trace boundary contains only fixed failure codes, process and
delivery identifiers, native queue receipt identifiers, turn identifiers,
and SHA-256 digests of exact wake text and provider request bodies. It must not
retain tokens, auth JSON, full prompts, Responses bodies, raw sessions, or
unredacted stderr. Keep the trace under
`~/.cache/cairn-220-native-wake-proof/` with mode 0600; do not copy it into the
repository.

No real provider, provider authentication, OpenRouter, production database,
held-work replay, or acceptance claim was used or established. The follow-up
below records the later bounded retry; CAIRN-220 remains unaccepted.

## Bounded follow-up

An inert bwrap preflight read the checker at the exact mounted path
`/repo/scripts/check_codex_app_server_wake_join.py`. It returned exit 0 with
empty stderr. The 0600 traces are
`~/.cache/cairn-220-native-wake-proof/preflight-path-retry-4ed24111fb6e44c3b753b44bef8a665a.json`
and, after the targeted import-path edit,
`~/.cache/cairn-220-native-wake-proof/preflight-path-retry-662fc1003a1f472581a49337cc6bbfed.json`.

The first full run reached the isolated Codex app-server, then stopped before
the watcher. Its 0600 trace
`~/.cache/cairn-220-native-wake-proof/run-full-join-retry-78ecc6b9f98a4a078057e8a5ac5f9b1e.json`
records `ModuleNotFoundError`, no queue/hook/Store observations, and empty
outer stderr. Static review localized this to the inner runner importing
`codex_queue` without `/repo/integrations/lifecycle` on `sys.path`. One
targeted fix added that path; missing-module diagnostics were constrained to a
fixed allow-list.

The single full retry reached the actual production watcher with the pinned
Codex 0.164.0 binary, fake Codex auth, `--unshare-net`, a local Responses stub,
and the disposable API/PostgreSQL cluster. The watcher exited 0 and retained a
pre-claim `codex-queue` wake marker with `status=uncertain` and no
`queued_submission_id`. The queue observer recorded no receipt or item; no
`UserPromptSubmit`, Store attempt, or local Responses request was observed. A
`SessionEnd` hook row appeared only during app-server cleanup, after the
boundary report had been written. The 0600 trace is
`~/.cache/cairn-220-native-wake-proof/run-full-join-retry-bef463c04fbe41e69dc022021e4c34c2.json`.

The retained session state supports only the fixed classification
`queue_outcome_uncertain_no_receipt`: an uncertain wake marker exists, but no
queue receipt or attempt was observed. Watcher stderr was retained only as a
SHA-256 digest, so the native RPC/`QueueError` cause is unknown. No replay,
settlement, or additional full run followed. The unverified harness was
discarded; this note records the verified preflight and failure boundaries,
not CAIRN-220 acceptance.

## Queue-stage diagnostic preparation

At commit `1140772`, the previous full-join harness is absent from the checkout
and no runnable copy was present in the private trace directory. The prior
uncertain delivery was not replayed. A new opt-in Python startup observer is
available at `scripts/fixtures/cairn220_queue_trace/sitecustomize.py`. With
that directory first on `PYTHONPATH`, `integrations/lifecycle` also on
`PYTHONPATH`, and `CAIRN220_QUEUE_TRACE` set to a host-mounted private path, it
records fixed stage transitions around production `codex_queue._open`,
`_admit`, `thread/queue/add`, and `thread/queue/start`. Each append is fsynced
to a mode-0600 trace before assertions. It keeps only allow-listed exception
class names and bounded integer native refusal codes; it omits exception text,
wake content, and tokens. The queue helper return values and decisions are
unchanged.

`python3 scripts/test_codex_queue_stage_trace.py` verifies the observer's
pre-call/refusal trace, 0600 file mode, and omission of synthetic refusal text.
That focused test and `make check` passed. This verifies the trace boundary
only. No new disposable PostgreSQL/API/Codex watcher run or delivery-to-Store
join was performed, so CAIRN-220 remains unaccepted and the queue failure's
native cause remains unknown.

## Reconstructed fixture attempt

A provider-free runner was added at
`scripts/check_codex_app_server_wake_join.py`. It has a separate preflight that
executes the exact mounted runner path under `bwrap --unshare-net`, creates the
observer trace at the exact mounted path with exclusive create, verifies owner
and mode 0600, checks the single fixed startup row, and removes that preflight
trace before the full attempt. Its fixture uses a fresh PostgreSQL cluster and
local API, synthetic agent identities and fake Codex auth, the pinned Codex
0.164 binary, and a loopback-only Responses stub. The observer is first on the
watcher's `PYTHONPATH`; the full trace is outside the disposable fixture root.

The first reconstructed full attempt exited during setup with a generic
`CalledProcessError` and no watcher boundary report. A setup-only repetition of
the PostgreSQL/API bootstrap passed. The one trace-driven full retry produced
the private 0600 bootstrap trace
`~/.cache/cairn-220-native-wake-proof/bootstrap-f69eab74388a4fbfbdafcd63f0a6e1ff.jsonl`.
It records `pg_start` with exit code 1 after `pg_initdb`; the bwrap runner,
Codex, watcher, queue, hook and Store were not reached. No raw PostgreSQL or
watcher stderr was retained. The failed disposable root was removed, and a
process check found no matching test server. No queue retry or replay occurred.

The exact current diagnostic boundary is therefore PostgreSQL startup, before
the watcher. This reconstructed full fixture has not established queue receipt,
native item start, `UserPromptSubmit`, or a Store attempt. The prior uncertain
delivery remains untouched, and CAIRN-220 remains unaccepted.

After the retry, the strict path tests, mounted-path/trace preflight, focused
observer test, and `make check` passed. The PostgreSQL startup failure-path
cleanup also checks for a partial `postmaster.pid` before removing its root;
that partial-start branch was not observed in this run.
