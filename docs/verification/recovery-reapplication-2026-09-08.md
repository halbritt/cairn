# Recovery reapplication verification — 2026-09-08

The preceding release inspected an older restore against later external
withdrawals but could not reapply them. The new [operator command](../recovery-reapplication.md)
records new authorized restrictions and preserves original missing-history
expectations. It also retains imported context custody for the ordinary deletion
worker. This advances L9; freshness, broader projection rebuilding and restore
admission remain open.

## Verified behavior

`core/recovery_apply_test.go` uses separate databases inside the owned disposable
PostgreSQL cluster. It verifies revoked grant authority, retracted instruction and
forgotten-source/dependent exclusion from compilation; new event identities;
original missing audit gaps after reapplication and a subsequent export; stable
original audit digests; exact request retry and changed-intent refusal; current
root checks on cached results; absence without replacement; rollback after late
scope or unsafe-custody refusal; conflicting external expectations; and rollback
when the new audit would exceed the export budget.

The source-event expectations in those focused tests are explicitly synthetic.
The actual capture/restore evidence is `scripts/check-recovery.py`, run by
`make test-lifecycle`: it creates a grant, C instruction and note, takes a real
custom-format dump, then revokes/retracts/forgets and captures the external
recovery record. A post-backup wrapped process creates a real owned context file.
Inspection of the restored dump detects revived restrictions and missing custody.
It remains read-only. Reapplication then repairs all three restrictions, retains
only the original `AUDIT_MISSING` gaps, preserves those same gaps across a fresh
export, and queues the absent receipt's context through imported custody. A
fresh worker process purges the file without inserting a receipt. Application and
purge retries preserve their results. The existing lifecycle suite separately
covers abrupt worker death, restored pending deletion effects and file-unlink
recovery.

Final local checks passed:

- `make test-integration check`: all five Go packages with race detection,
  capture/proposal/API/host CLI checks, vet and formatting.
- `make test-lifecycle`: actual backup/restore, generation fencing, interrupted
  deletion/file effects and the extended withdrawal reapplication drill.
- Python script suite: 12 tests.

Private logs are `/tmp/cairn-reapplication-integration.log`,
`/tmp/cairn-reapplication-lifecycle-final.log`, and
`/tmp/cairn-reapplication-python.log`. Early focused runs caught an instruction
fixture missing its required kind/policy key and a UUID/text cast error in the
new inspection mapping. These were corrected before final verification; they
are not historical Cairn defects.

## Preservation and limits

Ordinary revoke, retract, forget and preview operations share private transaction
helpers with recovery. Their public validation, refusal retention, request
idempotency and isolation remain. Migration027 adds immutable application and
imported-custody records plus a new audit event kind. Existing audit member JSON
is unchanged; new application events commit the source digest and action mapping.
No source actor, timestamp or transaction ID is reconstructed from a hash.

The command uses current root authority and trusts an explicitly selected
external source. Its checksum does not establish authenticity or freshness.
An absent subject remains absent; conflicting scopes, unsafe custody or an
inconsistent existing tombstone refuse. Imported custody is not a host
observation. Existing deletion residuals still apply. The command does not open
service admission or claim general memory usefulness from test success.

Doctrine decision: `pkt-5728b4e5ef820576`, SHA256
`5728b4e5ef820576b2fd941d8c7bfa500ab596d8022ffa417abcd71edcee89b4`;
corpus `corpus-2026-07-12-a11702cc9217`, doctrine
`doctrine-f6bbb5196a3f8bf9`, validated release
`d3e0c0d4ccd1920b2e045c156f1cf0db4fc5f04f`. Two evidence passes closed with a
schema-valid private decision receipt and five valid concept citations. The
receipt retains 23 nonmaterial generic UI/ranking/interface/configuration,
pre-release serving-parity and named-procedure obligations explicitly. Release
verification is separate from this implementation receipt.
