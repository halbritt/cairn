# Keep a named task available across native OpenCode sessions

Native OpenCode searches can now use an explicitly configured task ID across
sessions. A note restricted to that task remains discoverable and pullable when
the session changes. Without configuration, task `opencode/<sessionID>` and run
`<sessionID>` remain the defaults.

This addresses a mismatch with compact startup: the launcher accepts a declared
task/run, but subsequent native searches previously had no way to keep those
labels. The existing guide documented the switch to native session scope. The
change adds an explicit host choice; it does not infer a task from a session or
propagate startup flags automatically.

## Interface and boundaries

`opencode-install --task TASK_ID` writes optional `task_id` in the existing
connection file. Native run IDs still come from each session. Optional `--run`
fixes the run too and requires a task. The native adapter validates these settings
and uses them in the ordinary authenticated search. Agents cannot override them
through tool arguments. [Usage](../opencode-tools.md#continue-a-task-across-sessions)
explains reconfiguration and alignment with startup or another harness.

Labels retain exact UTF-8 bytes, subject to the 256-byte bound, nonblank text,
no NUL and refusal of the wildcard `*`. Invalid installer arguments refuse before
filesystem effects. A fixed task applies to every session using that connection
file until the host changes it. These labels are declarations, not independently
observed execution identities. Existing native session validation still applies.

There is no API or database schema change. Scope matching, profile/repository
checks and destination filtering stay in the existing core. Captures do not
inherit search scope. Retained pulls keep their original receipt and current
eligibility checks. Changing effective search scope under a reused request UUID
continues to refuse rather than silently change the request.

## Executed checks

The new installer test first failed because `--task` was unknown, then passed.
An older invalid-arguments test also expected `--task` to be unknown; it now checks
an actually unknown flag, while the new tests establish the intended optional
scope behavior. Go tests verify exact Unicode/punctuation, default omission,
task-only configuration, run-only refusal and invalid labels without side effects.

A disposable PostgreSQL/API check used actual OpenCode 1.18.21 custom tools:

- Default native scope omitted the seeded named-task note.
- Two distinct fresh native session IDs searched and pulled its exact body after
  the task was configured; each retained its own native run ID.
- Fixing the run additionally retrieved its run-restricted note. Unrelated-task
  and local-only notes remained excluded.
- Caller attempts to override scope through tool arguments refused. Changing
  configured scope with an existing request UUID refused; a fresh request used
  the new task. Removing the settings restored default exclusion.
- Invalid host settings refused, including oversized UTF-8, NUL, lone surrogate,
  blank/wildcard labels and a run without a task.

The full disposable PostgreSQL/race suite and native OpenCode suite passed,
including existing exact body/evidence/history, currentness, permission, Unicode,
context-room and normal scripted-session checks. Python checks and `make check`
passed. The native tests made no answering-model calls and used no production
store. Distinct native sessions do not establish independent model-task benefit.

[Metadata](opencode-task-scope-2026-09-10.json) retains the actual generated scopes,
source/check hashes and evidence pointers under
`/tmp/cairn-opencode-task-scope-20260910/`. Doctrine packet
`pkt-2f98d7bcf21730ce` informed identity distinctions and preservation boundaries;
its receipt and two citation closures validate. Twenty-one residual obligations
concern architecture or domain-model redesign outside this change.

The retained startup procedure and owner evaluation guidance helped keep this
work focused on an access gap. Full task-context accounting, automatic propagation,
native observed execution and sustained task value remain open.
