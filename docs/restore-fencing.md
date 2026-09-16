# Fence restored delivery capabilities

An older database can contain an unlaunched receipt that once authorized a run.
Expiring index handles does not prevent that receipt from claiming a launch.
`fence-restore` invalidates delivery capabilities from the restored database while
preserving receipts and their historical semantic seals.

Migration 045 also fences pending [one-shot schedules](event-scheduling.md):
they become `skipped` with code `restore_fenced`. A pending row in a backup may
have fired after that backup. Its retained occurrence ID and publication metadata
remain available for review; restarting the scheduler does not replay it.

Use [restore sessions](restore-admission.md) for the complete implemented
reconciliation workflow. `begin-restore` includes a fresh fence and blocks
ordinary transactions until explicit verified resume. The standalone command
below only invalidates delivery capabilities; it does not pause fresh work.
Keep the restored store isolated from runners when using it:

```sh
python3 - <<'PY' | cairn fence-restore
import json, uuid
print(json.dumps(dict(request_id=str(uuid.uuid4()),
    reason='Invalidate restored delivery capabilities before fresh compilation')))
PY
```

As with other commands, `CAIRN_DATABASE_URL` selects the database. The command
requires an unscoped operator channel. It advances a store-owned delivery
generation, expires existing index sessions and retains an immutable fence record
with the operator identity and reason, in one transaction. Its response contains
`fence_id`, `generation`, `receipts` and `sessions`. Receipt count covers the
previous generation, including already claimed receipts; it is not a count of
cancelled processes. Retrying the same request UUID and reason returns the same
result, without invalidating work compiled afterward. Use a new UUID for each
subsequent restore.

Old receipts now return `STALE_PACKAGE` on launch claims, bindings, managed
context registration, index/pull and repeated compile requests. Guards run before
cached mutation responses are returned. Callers must compile with a new request
UUID. `replay`, historical recompilation, explanations and delayed outcome
observations remain available, subject to the existing payload and access rules.
The semantic seal excludes this delivery-generation metadata; fencing does not
rewrite what an earlier run saw or fabricate a launch observation.

Compiles, launch claims, bindings and pulls hold a shared generation-row lock
through their transaction. A fence takes the exclusive update lock. Compilation
whose source snapshot predates a committed fence must retry; it cannot stamp old
source state with the new generation. Conversely, a fence waits for an already
committing receipt and invalidates that receipt afterward.

The authenticated observer API now exposes `POST /v1/claim-run` with
`{"receipt_id":"UUID"}`; `cairn agent claim-run` uses that endpoint. An agent
profile cannot claim a host launch, and the receipt must belong to the caller's
principal and repository. Success reserves one launch. A repeated claim returns
`RUN_ALREADY_STARTED`; after an ambiguous response, inspect the run instead of
executing it again. This endpoint reserves admission; it does not execute a
process or assert task acceptance.

This fence does not stop running processes, recall bytes already delivered, or
make an isolated restore safe to resume by itself. Already claimed work may have
external effects, so the operator must stop or isolate runners before restore.
Freshness and reapplication of newer withdrawals, projection verification and
other restore checks remain required. The API does not infer that a database was
restored or run this command automatically. `invalidate-handles` remains the
narrower index-only operation for compatibility.

Migration 018 assigns existing receipts generation zero and preserves their
history. The upgrade alone does not fence the operational store. The lifecycle
drill restores a real dump, starts a fixture observer API, fences the restored
receipts, checks refusal of old claims/bindings, and claims a newly compiled
receipt exactly once. Database tests also cover cached expansions, delayed
outcomes, scope, retry identity and both sides of the compile/fence boundary.
