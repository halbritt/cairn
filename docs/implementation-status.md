# Implementation status — 2026-09-07

Cairn is a usable local alpha. It can retain project notes, retrieve them on a
later run, supply a bounded context package to a process, and show the retained
package and observed outcome afterward. This advances the initial record-only
slice; it does not complete every Stage 1–2 design contract.

## Working paths

- A records have retained versions, stamped writers/witnesses, retry identity,
  scoped reads, and compare-and-swap edits. An explicit task/run wildcard lets
  project notes survive a run boundary.
- A one-time operator root supports bounded grants, revocation, independent B
  promotion, C issuance, B correction, and retraction. Privileged transactions
  retry serialization/deadlock failures from fresh state. Deferred constraints
  require matching event subjects/versions and B evidence references.
- Inline evidence is deliberately captured with SHA-256. Retrieval verifies
  its bytes and applies the accepted one-resolvable-reference minimum.
- Delegation reconciliation matches the exact dispatcher, delegate, task, run,
  attempt, and result reference. Failed attempts retain one service observation
  and contradicted claims reach the correction docket.
- The compiler uses a repeatable-read snapshot, direct mandatory selection,
  deterministic lexical/scope/recency ordering, atomic omission of disputed
  optional material, destination filtering, and conservative budgets.
- Canonical CBOR with nanosecond RFC3339 timestamp encoding and BLAKE3 seals
  semantic state. Request IDs, delivery nonces, and receipt timestamps are
  outside the seal. Receipts and exact record exposure commit before return.
- The wrapper supplies stdin or argv before process execution, observes exit
  and duration, hashes output streams, limits process lifetime, and cleans up
  its process group. A receipt can claim only one launch. Pending outcome
  requests survive local DB failure and have an explicit recovery command.
- Local inspection provides list/get, historical replay, reverse exposure,
  explicit usage observations, an outcome report, and a correction/recovery
  docket. None treats exit zero or citation as causal proof of benefit.

## Evidence collected

`make test-integration` passed with PostgreSQL 17 and Go's race detector. There
are 25 top-level tests, with additional scenario subtests. Tests include the
original schema upgrade, concurrent edits and retry collisions, parent-grant
revocation races, self-promotion, evidence degradation, mandatory overflow,
conflicts, destination canaries, semantic seals, launch failure, timeout, and
duplicate-launch refusal, and durable outcome recovery after store failure. `make check` and `make build` passed.

`make test-lifecycle` passed startup, backup, restore, exact semantic-package
replay, stop, and restart against its own disposable store. The check found and
fixed an inherited lock descriptor that otherwise kept backup/stop waiting on
the running PostgreSQL process. No shared database was used for those tests.

The installed OpenCode 1.18.21 was invoked through Cairn against a local
synthetic OpenAI-compatible endpoint. The compiled canary appeared in the first
model request and the process exited zero. See the
[probe report](verification/opencode-1.18.21.json). The probe used temporary
home/XDG directories, a synthetic provider key, and no real model or user
session. It used the documented [custom provider configuration](https://opencode.ai/docs/providers/#custom-provider)
and [configuration overrides](https://opencode.ai/docs/config/), then verified
the installed binary's behavior rather than assuming the docs proved contact.

A persistent local store was started at `~/.local/share/cairn/socket`, with no
TCP listener. The `cairn` executable was installed in `~/.local/bin`. Three
ordinary repository notes were deliberately captured from Cairn's own docs and
code. A live search selected the relevant notes. A local `cat` task received a
compiled package, and its report recorded one available delivery, one zero
process exit, and one unknown task outcome. A local database backup was created.
This is a working smoke path, not measured model usefulness.

## Limits and next work

Operating choices are settled in [decision 0002](decisions/0002-local-memory-loop.md).
The following are implementation gaps, not decisions waiting on the operator:

1. Add a narrow authenticated agent transport. The current CLI is trusted
   local operator administration; embedding hosts establish channels separately.
2. Extend scope with revision/workspace pins, validity and domain-specific
   applicability. Current repository/task/run scope does not establish code
   currentness after revision changes. Capture revision-sensitive notes with
   explicit short-lived task/run constraints meanwhile.
3. Add audit-backed redaction, deletion effects, backup residual tracking,
   evidence lifecycle jobs, retention, and restore handling for later deletions.
   The current backup drill precedes those features. Do not retain secrets.
4. Add full conflict packages, explicit C supersession notices, and authorized
   expansions. Optional disputed material is currently omitted whole; there is
   no in-run expansion tool or compaction hook.
5. Wire Striatum's actual authenticated spawn/terminal callbacks and durable
   task identity. Standalone process outcomes do not replace upstream task
   acceptance or binding/capability assessment.
6. Build a cutoff-pinned real-history recurrence trial and baseline comparison.
   The current replay reproduces selected bytes; it is not a counterfactual
   usefulness evaluation. Grooming and learned ranking remain disabled.

The wrapper retains permitted context files in its private run directory and
does not isolate all user native-memory facilities. Generic command destination
selection is trusted operator configuration, not network confinement. Production
receipts report availability; only the controlled fixture probe observed model
request contact. There is no H3 claim.

No automatic backup/pruning/grooming timers were installed. PostgreSQL can be
stopped with the local-store script and restarted without data loss. Reboot
autostart is not configured.
