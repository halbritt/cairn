# CAIRN-227 remote code-mode host isolation probe

Date: 2026-10-25. Result: **refused** as an auth-isolated actor route.

The provider-free probe in `scripts/trial_codex_remote_code_mode_host.py`
starts installed Codex CLI 0.164 app-server with a loopback-only Responses
fixture. It places a synthetic auth marker in the temporary app-server
`CODEX_HOME` and starts the packaged `codex-code-mode-host` in external bwrap.
The bwrap mounts the actor workspace and system runtime; it does not mount the
temporary app-server home. The app-server configuration requires no provider
authentication and disables in-process code-mode-host fallback.

Observed sequence:

- A direct `/bin/cat` in the same bwrap filesystem view could not read the
  marker.
- A model-facing `functions.exec` call ran `printf` successfully.
- A second `functions.exec` call ran `cat` against the marker path and returned
  the synthetic marker to the local Responses fixture. The marker was synthetic;
  no owner auth file, subscription route, external provider, API key, or
  production service was used.
- A second turn stalled at the local fixture, then `turn/interrupt` completed
  with native status `interrupted` at 2.001 seconds. The app-server and remote
  host process groups were stopped and reaped.

This establishes that the external bwrap namespace works for a process placed
inside it, while the remote code-mode host path does not put the
model-facing `functions.exec` shell in that namespace. The probe does not
establish an alternate supported shell executor or a safe live route. The
40,000-input and 512-output ceilings remain post-turn checks; this probe tested
only the active interruption mechanism with a two-second fixture deadline.

Run the focused proof with:

```sh
python3 -B -m unittest scripts.test_trial_codex_remote_code_mode_host -v
```

The test intentionally passes only when it reproduces the refusal conditions:
successful benign command, direct bwrap marker denial, marker exposure through
`functions.exec`, native interruption, and owned process-group cleanup. A
future supported isolated executor should replace the expected leak assertion
with a denial assertion before any live canary admission.

## Hardlink alias follow-up

A path-only workspace bind does not close the synthetic app-home leak. A
provider-free bwrap fixture placed `SYNTHETIC_HARDLINK_MARKER_ONLY` in a
synthetic sibling app home, hardlinked it under the workspace, and confirmed
both names had the same inode (`nlink=2`). The readonly workspace bind still
returned the marker. A separate bwrap check with an absolute symlink to the
sibling path returned `ENOENT`, which shows that this path was absent from that
mount view; it does not address hardlinks.

A timing check also replaced an ordinary workspace file with a hardlink to the
synthetic marker **after** bwrap had established its readonly bind and while the
sandboxed command was sleeping. The command then read
`SYNTHETIC_RACE_MARKER` through the already mounted workspace path. The final
inode comparison was equal and `nlink=2`. Thus preflight scanning for `st_nlink`
or checking current directory entries cannot close this boundary when another
same-UID process can mutate the source tree during execution. A readonly bind
prevents the sandboxed process from making that mutation, but it does not freeze
the host's underlying directory entries.

The retained fixture also reports that `/proc/<host-pid>/fd` was not visible.
A separate descriptor probe established that a descriptor explicitly passed to
a child remains usable; seccomp denied creating another connection but did not
revoke the inherited stream. This is a separate authority channel: a future
launcher must close all unneeded descriptors and must never pass a credential-
bearing or broadly capable descriptor to model-facing execution. Neither
finding establishes that the current upstream nested executor can inherit a
particular host descriptor.

No safe workspace-preserving mitigation was established. Bind mounts preserve
inode identity, so readonly/path allowlists cannot distinguish a workspace name
from another hardlink to the same inode. Reflink/copy snapshots would break
that identity but require a complete, race-safe copy and a defined writeback
contract; they would also change live workspace visibility and filesystem
semantics. No such executor was implemented or tested here. Rejecting hardlinks
or scanning before launch is insufficient against the demonstrated race.

Disposition remains **refused**. The concrete remaining boundary is any
workspace bind whose source tree can acquire a hardlink to an out-of-view inode
while execution is active. The nested-exec prototype has no accepted isolation
claim, and the upstream build blocker (pinned `rusty_v8` v152.2.0 HTTP 404) means
no new app-server runtime proof was obtained. Do not use this route for a live
subscribed actor. A future candidate must provide a detached workspace view
with explicit read/write reconciliation, or an equivalent kernel-enforced
inode/provenance boundary, and test concurrent source mutation, symlinks,
hardlinks, descriptor inheritance, ordinary workspace reads/writes, and cleanup
before reconsidering admission.
