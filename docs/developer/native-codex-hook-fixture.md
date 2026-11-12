# Native Codex hook selection fixture

`scripts/check_r04_native_codex.py` checks staged lifecycle hooks in an explicitly
selected native Codex app-server. It uses the existing hybrid-runtime isolation
wrapper, disposable homes, a local scripted Responses provider and a synthetic
Cairn API. No model account or production installation is used.

The real R-02 installer and R-04 renderer produce immutable selections A and B.
The fixture copies the selected hook settings into its disposable
`CODEX_HOME/hooks.json`, reads them with native `hooks/list`, and trusts each
native key and `currentHash` through `config/value/write`. Listing warnings or
errors fail the fixture. Trust and hook-file readback describe the profile;
the executed launcher establishes a thread's selection.

The cases cover fresh and existing A, a separate A control, cached A after B is
published and trusted, fresh B, refreshed B, the unchanged A control, missing
and corrupt receipts, cached B before reversal, reversed A, and a B control
that remains on B. `strace` checks each selected launcher's program and literal
argv in the same PID, including its exit status. Startup, Stop and SessionEnd
launches are checked as well. Some coordination events share one argv, so the
trace verifies their shared entrypoint and launch count rather than inferring
distinct event payloads from that argv.
The positive cases also correlate Cairn's busy/idle context updates with the
native thread ID. SessionStart is checked in the first turn's trace because the
selected native client defers that callback until its first turn.

For unbound selections the current renderer uses checked Go `memory` for recall
with H13 and `memory-capture` for PreCompact and asynchronous Stop with H150.
The retained twelve-case native observations used Python memory and do not
qualify these new routes. Coordination reaches the receipt-checked private
Go image. Receipt refusals must leave Cairn API calls
and lifecycle state unchanged. A native turn can complete after a hook refuses,
so model completion and child-process refusal are checked separately.

Refresh uses native `thread/archive`, waits for the target to disappear from
`thread/loaded/list`, then calls `thread/unarchive` and `thread/resume` with the
same thread UUID. Both transitions check the unchanged rollout path and earlier
prompt history in native readback and subsequent provider input. The other
threads stay loaded, and later turns verify their original selections. Closing
and resuming runs SessionEnd and SessionStart hooks; it is not a side-effect-free
configuration read. The fixture finally archives every thread and joins the
app-server without process-group signalling.

This qualifies the selected client's disposable hook trust and thread refresh
path. It does not establish live trust, MCP connection identity, a filesystem
watcher, tool/interrupt/compaction coverage or a full activation-unit barrier.
The same Go image is used for both selections, with different receipt,
configuration and launcher identities. Full R-04/R-06 and production
qualification remain separate.

Select the actual native executable, its expected SHA-256 and an evidence
directory outside Git. The Node launcher alone is not the native executable.
For example, with those paths in task-specific variables:

```sh
python3 scripts/test_hybrid_runtime_operations.py \
  --timeout 480 \
  --receipt "$CAIRN_R04_EVIDENCE/isolation.json" \
  --ro-bind "$CAIRN_R04_CODEX:/opt/r04-native/codex" \
  -- python3 -B scripts/check_r04_native_codex.py \
  --codex /opt/r04-native/codex \
  --sha256 "$CAIRN_R04_CODEX_SHA256" \
  --archive-stdout > "$CAIRN_R04_EVIDENCE/run.log" 2>&1
```

The native thread uses `danger-full-access` within the outer isolation namespace;
the wrapper still isolates production homes, sockets, network and host processes.
Native setup diagnostics are retained in `native.stderr`. Temporary account
paths can prevent PATH-alias creation, and the inner Linux sandbox may be
unavailable inside the outer namespace. These diagnostics do not qualify the
native client's own sandbox or shell-tool setup.

With `--archive-stdout`, the output contains a JSON line with `archive_base64`,
a gzip tar archive of selected traces, settings, native trust readbacks,
synthetic requests and observations. Decode it outside Git, including on
failure. A successful `report.json` includes fixture and client hashes,
executed program hashes, selections, refusals, refresh transitions and
`cleanup_passed`. The wrapper's separate receipt binds the command and source
checkout to its isolation checks. Homes, credentials, databases and executable
binaries are excluded from the archive.
