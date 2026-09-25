# Owner-only run status verification — 2026-09-08

The new `run-status` operation closes a hosted observer recovery gap: a caller
can inspect its own claim/outcome after an ambiguous response without reading
protected memory reports. See [the contract](../run-status.md).

## Verified behavior

- The hosted observer's status changes from unclaimed to claimed to a recorded
  process outcome. A claim alone never implies a started process or acceptance.
- Exact caller and repository ownership apply. Another caller in the same repo
  and the same caller in another repo are denied. Missing/malformed IDs return
  the existing typed errors. Ordinary agents can inspect their own compile
  receipts without acquiring observer authority.
- Raw HTTP response keys are restricted to receipt/time/claim/binding flags and
  bounded process outcome fields. The privacy check uses untyped JSON so added
  server fields cannot disappear during typed decoding. Hosted run-report
  access stays denied.
- Real Unix handlers commit a claim or outcome and then drop the response.
  Status reveals the committed metadata; a lost claim does not launch a child,
  and exact pending-outcome recovery returns the observation ID seen by status.
- Policy changes, restore fencing and body purge preserve status observation.
  Old launch requests and unavailable payload replay remain refused.
- The CLI works through the API with an unusable client database address.
  Another agent cannot read the observer's receipt. Direct CLI and API return
  equivalent retained metadata after restoring a disposable backup and fencing
  its receipts; a false claim still does not permit launch.

Source tests: `localapi/run_status_test.go`, `localapi/runner_test.go`,
`core/run_status_test.go`, `scripts/check-local-api.py`, and
`scripts/check-restore-fence.py`.

`TZ=UTC make test-integration check` and `TZ=UTC make test-lifecycle` passed using
disposable PostgreSQL. Logs are retained locally at
`/tmp/cairn-run-status-final-integration.log` and
`/tmp/cairn-run-status-lifecycle.log`. No schema migration is needed.

## Scope

These checks establish owner-scoped recovery inspection and preserved launch
boundaries. They do not prove model usefulness, task acceptance, native Striatum
integration, hostile same-UID isolation, or recovery of observations absent from
a restored backup.

## Installed verification

Commit `b1eeb42444a2908f0530536f64b0d3dfa11999f6` passed
[CI](https://github.com/halbritt/cairn/actions/runs/34221236150) and was built from
a clean clone with Go 1.25.0. Installed and running API binary SHA-256 is
`ef89bdb43ab28bd0ac02003af8f206b37c0e4353fd32d336c223913257a5cb82`.
Both local services are active; schema stays 024 and the record-version digest
is unchanged. A cataloged backup was taken with the prior installed executable.

The installed client inspected the existing authenticated repository-validation
receipt through its observer profile with an unusable client database address.
It returned the recorded binding and launch claim, original outcome ID, process
state `exited` with exit code 0,
and the fixed metadata-only shape. No new operational run or test fixture was
created. Task acceptance remains unknown. Private local proof is retained at
`/tmp/cairn-run-status-install-verification.json`.

The engineering review used validated Pincite release
`d3e0c0d4ccd1920b2e045c156f1cf0db4fc5f04f`, corpus
`corpus-2026-07-12-a11702cc9217`, final packet `pkt-0582d4659633e23d`.
Two evidence passes closed material ownership, identity, gate and consumer
obligations. The typed local decision receipt retains the evidence and
nonmaterial omissions; doctrine does not establish model or host acceptance.
