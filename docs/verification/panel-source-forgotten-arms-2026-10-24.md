# Source forgotten before read across panel arms

Date: 2026-10-24.

The frozen `source_forgotten_before_read` event and transition are now accepted
by the authenticated panel controller in baseline, envelope, batch, and
combined arms. Each arm ran in a distinct database on a fresh owner-only
PostgreSQL cluster. Fixture SHA-256: `05b726e7959156d20afbacfba7e87ce0e38f273823ecdee3482f38ca5ef6a1d7`.

The controller uses the exact frozen `request_missing_source` request and
`after_admission_before_read/remove_source` transition. As in the existing
probe, its generic event setup materializes the frozen event text as the
shareable A record and publishes one request referencing that Store record.
It skips the ordinary pre-publication worker history read. An authenticated
local Store claim then pins the event and delivery. Under the disposable
operator root, the controller obtains a deletion preview and forgets that
exact record before the receiver's first exact history read. The read returns
`PAYLOAD_UNAVAILABLE` without disclosing the body; the event reference remains
intact and leased.
The worker acknowledges `failed/source_unavailable`, repeats the identical
acknowledgement with the same request UUID, and reconciles the attempt. Durable
status remains one failed delivery with one attempt and no result.

The synthetic native turn ID and claim are Store setup only. No model actor
read or explained the request, and no requester explanation, provider call,
native turn join, accepted task outcome, or usage was observed. Receipts retain
the source reference and transition IDs but no source body. Each arm emitted
one event and `provider_called: false`. The whole controller is not rerun after
the one-way forget; exact settlement replay is checked inside the bounded
sequence, and a changed worker identity is refused before mutation.

Validation: four focused controller checks passed on separate arm databases.
The standalone [`check_panel_source_forgotten.py`](../../scripts/check_panel_source_forgotten.py)
probe remains unchanged. Twenty-one focused Python controller/preflight tests,
shell syntax validation, and `make check` passed.

Reproduce through the repository disposable integration runner:

```sh
make test-integration
```

This evidence verifies exact source forgetting and failed-settlement replay in
the Store. It does not establish an actor explanation, successful recovery,
paired task usefulness, or a rollout decision; CAIRN-227 remains deferred.
