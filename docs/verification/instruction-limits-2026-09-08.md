# Instruction limits verification

Migration 024 adds issued instruction categories and the governed `local-loop/3`
rule format. The [contract](../instruction-limits.md) defines the categories,
count/body-byte accounting and preservation boundaries. Migration assigns existing
authority rows to workflow without adopting a policy.

Public Store tests with disposable PostgreSQL cover:

- Mandatory category count overflow and a multibyte body exceeding its token
  bound refuse in body and index modes; the exact byte boundary fits.
- Optional category count/token overflow is omitted without excluding unrelated
  categories or advisory records. Index admission reserves full bodies, and the
  three admitted C handles expand successfully.
- Mandatory and optional instructions share one quota. A newer optional
  instruction cannot displace required policy in either mode.
- Category facts survive scope authorization and retraction. Metadata inspection
  enforces repository access; changed-category request retries conflict.
- Missing category objects, out-of-range limits and unknown issue categories
  refuse. Omitted categories default to workflow. Explicit adoption can refuse an
  existing instruction without changing that instruction's old package seal.
- Old engine-3 packages recompile after switching to engine 2. Rollback to their
  earlier rules creates a new engine-3 revision and enforces the restored quota.
- Private optional candidates neither consume visible category quota nor appear
  in hosted packages, census or explanation candidates.

The initial optional test failed because its unrelated fixture bodies exhausted
the total optional budget first. Reducing those bodies isolated the category
boundary while preserving both 1,000-byte security bodies and the 1,500-byte
security cap. The expected category omission was retained; production packing
precedence was unchanged.

The lifecycle drill creates an issued category and engine-3 policy before backup,
restores the dump, checks metadata/rules and issue/policy idempotency, verifies
body/index history and fresh compilation, then checks audit expectations and
existing restore fences. UTC integration/race, vet/formatting and lifecycle pass.

An isolated restore of the actual schema-023 backup was additionally populated
using the old installed binary. Migration to 024 preserved all six existing
local-operator packages and four synthetic body/index packages across engines 1
and 2. Four old issuance requests and one old policy request retried identically;
both old indexed C handles expanded after migration. Synthetic packages replayed
and recompiled with their original seals. Record versions were unchanged across
migration and its retry. The disposable cluster was stopped and removed.

These results establish the tested quota, history and migration behavior. They
do not establish model obedience, beneficial numeric limits, C waivers or the
complete enforcement/restore-admission design. Operational installation is tracked
separately from these disposable checks.

The engineering review used validated Pincite release
`d3e0c0d4ccd1920b2e045c156f1cf0db4fc5f04f`, corpus
`corpus-2026-07-12-a11702cc9217`, packet `pkt-d5f47f1863ae4674`.
Repository precedence, preservation of existing behavior and bounded authority
informed the change. The local decision receipt records typed source/test evidence,
two evidence passes and the remaining nonmaterial obligations. No UI, database
index optimization, ranking change or model-evaluation claim is part of this slice.


## Local installation

Commit `35543dd6136d10766d097f78677d62e31902ef45` passed
[CI](https://github.com/halbritt/cairn/actions/runs/34217040022) and was built from
a clean local clone with Go 1.25.0. The clean release binary also passed the
isolated old-backup upgrade and compatibility checks above before installation.
The installed binary and running API both have SHA-256
`4f50727182a9e091bae12e30fcebe18622aef98baab72d16a16de02dfb518c14`.

The operational store is now schema 024. A private backup preceded migration;
an authenticated existing-record read passed after restart. The record-version
digest stayed unchanged. The Cairn repository still uses builtin `local-loop/1`:
verification created no operational category policy or instruction.
