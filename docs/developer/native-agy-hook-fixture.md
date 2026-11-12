# Native Agy coordination hook fixture

`scripts/check_r04_native_agy.py` exercises Agy's native `PreInvocation` and
fully-idle `Stop` callbacks against receipt-checked private Go coordination.
It runs in the existing hybrid-runtime isolation wrapper with a disposable
home, a scripted Gemini HTTP provider and a synthetic Cairn API. It uses no
model account, real inference or production installation. Agy has no ordinary
lifecycle memory hook.

The real R-02 installer and R-04 renderer produce immutable selections A and B.
The fixture copies their exact bytes to the target project's
`.agents/hooks.json`; it preserves another workspace's A selection under the
same native account. The native process's `openat` records and the retained
hook files bind that project layer to the staged settings hash. The fixture
does not rewrite the staged commands. Native `--settings` selects the separate
session settings for the local provider; it does not select hook definitions.

After all 13 project-layer cases, those clients are joined and both workspace
hook files are removed. Four account-only cases use the same staged bytes at
the disposable `HOME/.gemini/config/hooks.json`: fresh A, B publication during
the conversation, stop/join and exact-conversation resume on B, then reversal
to A. The R-04 `account_home` and declared hook layout name this native global
customization directory. It is distinct from `ANTIGRAVITY_CLI_CONFIG_DIR`, the
Cairn binding account and the native brain/profile directory. The
[official hook reference](https://antigravity.google/docs/hooks/) documents
this global location. Native opens, literal argv, provider context and
identity are checked for this layer too;
the earlier account prompt must survive later requests and resume. Each case
records its hook layer and file pin. The original project-layer receipt
refusals remain; the account-only phase adds positive selection controls.

The cases cover fresh and repeated A, an independent workspace control,
publication of B during the existing conversation, the unchanged control,
fresh B, stop/join followed by native `--conversation` resume, receipt
refusals bracketed by valid B, and reversal to A. In Agy 1.5.6's marker controls,
the next callback reread the changed project file without restarting the
process. The fixture requires that behavior from the selected client, with
the same conversation ID, and checks earlier prompts in subsequent provider
input. It separately verifies orderly restart with that exact ID. `--continue`
does not identify the conversation precisely enough for this check.

The raw trace binds each launcher to its Go program and literal argv in the
same PID. Kernel `CLONE_THREAD` records identify that process's threads;
forked API clients are excluded from its stdin and stderr. Independently
decoded native writes and positive Go `read(0)` bytes must agree on the full
`conversationId`, workspace and event. CLI init/result records and the native
history path identify the same conversation. Initial registration or a
context update must report busy at PreInvocation, followed by an idle update
for a Stop whose raw `fullyIdle` is exactly `true`.
Preflight refusals can exit before Go reads stdin; the native producer's
callback frame is retained for those cases. History is read from the native
`transcriptPath` after all clients close. The report distinguishes that native
home from the fixture's Cairn binding account directory.

Each prompt seeds a fresh synthetic marker in the API's agent display name,
without placing it in user prompts or hook configuration. The fixture decodes
the checked Go `PreInvocation` stdout as `injectSteps[0].ephemeralMessage`,
checks the marker and full agent/execution identity, and requires that complete
message in the native Gemini request's `contents[].parts[].text`, ignoring
surrounding whitespace. The marker must be absent from earlier requests.
Per-case evidence records the marker, hook-output/message hashes and matching
provider-request indices; the archive retains raw hook I/O, provider requests
and synthetic API code. Missing/corrupt receipt cases keep zero API effects,
unchanged state and empty hook stdout, and their fresh marker must not reach
the provider. This qualifies native context application to the local provider;
real model comprehension and inbox delivery remain outside this fixture.

The captured prefix ends on a complete trace line. Per-prompt windows can begin
between a syscall's unfinished and resumed lines.
The decoder therefore pairs records in one full captured prefix before
selecting execs and native writes by their original start byte position. Thread
ancestry also uses the full prefix. A call that began before the window is not
credited to the current prompt. Genuine unpaired resumes still refuse, and
truncated selected hook writes still refuse. The snapshot must contain the
completed producer writes as well as the hook exits. Each analysis window is
retained before later assertions; a pairing assertion also retains its prefix.

Missing and corrupt receipts both emit the same public compatibility-preflight
diagnostic and exit 1. The fixture pins that diagnostic, requires no stdout,
and checks zero API calls and unchanged lifecycle state. It also reconstructs
the selected receipt's descriptor walk. For the missing file, the final
`openat` returns ENOENT; for the corrupt file, complete reads through EOF
reconstruct the retained seeded bytes and their changed hash. Valid B controls
must reconstruct the pinned hash. The seeded fault is the changed input and
the Go image refuses at preflight. The internal open-error or pin-mismatch
reason follows from source order; it is not a distinct native diagnostic.

Every traced PID must have an exit record before namespace teardown. Timeout
cleanup signals the owned executable's native PID after checking `/proc/PID/exe`;
it does not signal a process group or only the tracer. A separate hung Python
child exercises this shared cleanup path and must raise the forced-shutdown
failure. That calibration is separate from normal Agy runs, which must exit
cleanly. Native executable hashes are checked before and after execution.
The separate `--version` invocation is also traced and joined, with its stdout,
connections and executable hashes retained. An already-finished version command
is identified by its successful literal exec and exit records; live cleanup
still requires `/proc/PID/exe`. Credential checks run after this invocation.
The decoder rejects truncated native hook prefixes before JSON decoding, and
the exact pair of complete callback events remains required.

The connection check permits only the fixture provider's loopback port and
loopback DNS port 53. It records the exact unavailable
`/var/run/nscd/socket` probe separately and requires ENOENT and an absent path
inside the namespace. Every other Unix socket connection fails. The client
can try to resolve analytics and updater names; observed DNS writes and counts
are retained as unanswered probes. This is a `connect(2)` check, not a claim
about every address-bearing send. The wrapper's loopback-only network
namespace supplies the egress guarantee.

Select the native Agy executable and its expected SHA-256, and keep evidence
outside Git:

```sh
mise run host:exec -- --timeout 20m --weight 1 -- \
  python3 scripts/test_hybrid_runtime_operations.py \
  --timeout 900 \
  --receipt "$CAIRN_R04_EVIDENCE/isolation.json" \
  --ro-bind "$CAIRN_R04_AGY:/opt/r04-native/agy" \
  -- python3 -B scripts/check_r04_native_agy.py \
  --agy /opt/r04-native/agy \
  --sha256 "$CAIRN_R04_AGY_SHA256" \
  --archive-stdout > "$CAIRN_R04_EVIDENCE/run.log" 2>&1
```

The output's `archive_base64` JSON line contains a gzip tar archive of selected
raw traces, settings, seeded receipts, native protocol/history records and
synthetic requests. Decode it outside Git, including on failure. A successful
`report.json` includes the fixture/client hashes, callback evidence, scope,
resume transition and cleanup result. Raw traces contain I/O bytes; homes,
databases and executable files are not separate archive members. The wrapper's
receipt records the command, checkout and isolation checks independently.

Layered account/project precedence, duplicates and cross-account isolation
remain unobserved, as do
non-fully-idle Stop,
Stop continuation, and child/subagent conversations. Namespace-local process
evidence does not qualify the host presence watcher, host PID association or
Herdr wake. This is not a code-image upgrade, real store/authentication test,
complete activation-unit barrier, full R-04/R-05/R-06 qualification or ticket
closure.
