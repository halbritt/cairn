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
held-work replay, or acceptance claim was used or established. The probe remains
incomplete after the one targeted fix; no further run was made.
