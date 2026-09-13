# Scope authorization verification

Migration 022 and `authorize-scope` implement the accepted C broadening gate.
An explicit target scope/applicability, live `issue` grant, source CAS, current
impact preview and reason authorize a new version. Its content/class and support
remain unchanged. The latest scope grant is independent of the record's B/C
qualification and remains required across ordinary edits and corrections.

Disposable PostgreSQL tests establish:

- A narrow, independently qualified B becomes available in another repository
  task only after explicit authorization. The returned package retains both
  prerequisite grant chains, deduplicating shared ancestors.
- Revoking the scope grant excludes fresh use after correction. Reauthorizing
  the same range can replace scope authority; revoking independent B
  qualification still excludes the claim.
- A remains advisory A after broadening, and ordinary edits retain its scope
  requirement. A widened mandatory C instruction with revoked scope authority
  no longer blocks a hosted destination that cannot receive local content.
- Widening C into a conflicting instruction's scope retains the new conflict and
  refuses compilation. No conflict is silently resolved by scope expansion.
- Missing explicit pins, stale versions/previews, wrong actors, task/repository
  moves, narrowed constraints, no-op initial authorization, degraded evidence,
  narrow source relations and open conflicts refuse without changing the record.
  A B with one surviving evidence reference still passes the existing read gate
  while its degraded support prevents broadening.
- Concurrent scope decisions have one winner and matching version/scope state.
  Identical retries return the original authorization.
- Historical packages before/after broadening and after scope exclusion
  reproduce their seals after revocation. The new refusal-reason test first
  reproduced `REPLAY_INCOMPLETE`; adding that reason to historical gate handling
  repaired it without changing old omission censuses.
- Scope authorization on B enters the C checkpoint subset. A real disposable
  backup/restore retains an A scope grant, wider applicability and idempotent retry.

UTC integration tests with the race detector, formatting/vet and the lifecycle
drill pass. The first lifecycle fixture used the replay command's response shape
for a compile response; correcting that fixture to the actual compile contract
made the restore check pass. No production response format changed.

These checks establish the implemented scope transition and its declared
boundaries, not broader task usefulness or complete restore admission. They do
not establish that a human's generalization is substantively correct. No
operational claim was broadened during verification.

## Local installation

Commit `a8623d6b48793463d0bb6bd7dd01d2db23917ad9` passed
[CI](https://github.com/halbritt/cairn/actions/runs/34209678488) and was built
from a clean clone with Go 1.25.0. An isolated restore of the pre-upgrade backup
advanced from schema 021 to 022, retained the record-version digest and passed a
repeated migration before the operational upgrade.

The local store now uses schema 022. The installed executable and running API
both have SHA-256
`3d1def84be487f549cfd2ced42e7a2410ae15a254f710faba6f49d46c03194a8`.
An authenticated read of an existing record passed after restart. The
record-version digest remained unchanged. No operational claim was modified to
exercise the new transition. This verifies installation, separately from the
behavioral evidence above.
