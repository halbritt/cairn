# Governed policy verification

Migration 023 and `policy-revise` introduce immutable typed repository policy
revisions with atomic C audit events. The effective policy is the latest explicit
revision; rollback copies an earlier revision's rules into a new authorized
revision. The initial `local-loop/2` engine narrows optional-memory budgets within
the original ceiling. No operational repository receives a policy through migration.

Disposable PostgreSQL tests establish:

- First adoption pins the policy revision and current authority facts in a new
  semantic package. Zero optional budget removes optional memory. Rollback
  restores retrieval with a new ID and version while old package seals replay.
- Stale expected heads, missing/conflicting rules, invalid budgets, cross-repo
  or missing rollback targets, missing reasons and another actor's grant refuse
  without replacing effective policy. Scoped inspection and reports cannot
  cross repository boundaries.
- Revoked effective authority blocks fresh compilation and launch, including
  cached expansion/binding retries. Explicit reauthorization under live authority
  restores fresh compilation; old receipts remain stale. Historical replay and
  delayed outcomes remain available.
- A policy revision invalidates cached managed-context registration only in the
  changed repository. Receipts in another repository remain usable.
- Concurrent first revisions have one winner. A repeatable-read snapshot taken
  before a committed first revision fails serialization at the policy-generation
  lock, so it cannot bypass that policy. Policy events enter the C checkpoint set.
- Mandatory instructions remain selected with zero optional budget, in body and
  index modes. Unsupported mandatory runtime enforcement still refuses.
- Policy-filtered run reports preserve pagination and include zero-memory runs,
  distinguish the legacy baseline, exclude pure retrieval and retain earlier
  policy membership after later revisions and outcomes.
- The ordinary API exposes no policy revision mutation route.

The lifecycle drill creates revisions and rollback, takes a real dump, restores
it, verifies effective rules and transport retry identity, replays historical body
and index seals, recompiles under restored policy and verifies audit expectations.
Its CLI run query also excludes the pure-retrieval fixture population.

The initial feature and report tests failed on missing public APIs before their
implementations. Focused policy tests, UTC integration/race, formatting/vet and the
disposable lifecycle drill pass. These checks establish the implemented policy
versioning and delivery boundaries; they do not establish model obedience, causal
memory benefit, instruction-category limits, waivers or complete restore admission.

An isolated restore of the actual pre-upgrade backup also advanced from schema
022 to 023. The old installed binary and the new candidate replayed all six
local-operator packages to identical data and seals. The record-version digest
remained unchanged, migration retry passed, and no policy revision was created
by migration. The disposable restored store was stopped and removed afterward.

## Local installation

Commit `b694d7f42a4db565e5ee0dd9f79bc85a62f51246` passed
[CI](https://github.com/halbritt/cairn/actions/runs/34213846617) and was built from
a clean clone with Go 1.25.0. The installed executable and running API both have
SHA-256 `4c19a6bef5856fd2bbfa77ea159bafd58e48cac696ce7ef2aeee9c1ce1a03784`.
The operational store now uses schema 023. Both services are active, an
authenticated existing-record read passed, and the record-version digest remained
unchanged. Inspection confirmed the operational Cairn repository still uses the
original built-in policy. No operational policy was issued for verification.
