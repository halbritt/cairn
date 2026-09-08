# Host attempt linkage verification — 2026-09-08

Optional host attempt linkage connects a memory receipt to an already observed
attempt under the same authenticated observer and exact scope. It does not
manufacture a spawn or infer delegate completion from process exit. See the
[contract](../host-attempt-link.md).

## Source and boundary checks

- Same-owner/exact-scope bindings retain the host attempt UUID through the
  library, authenticated CLI result and run report. Missing/malformed attempts,
  another observer, and mismatched repo/task/run including wildcard host scopes
  are refused. Matching task/run strings alone never synthesize a link.
- Multiple prepared receipts may refer to an attempt, but concurrent claims
  reserve exactly one wrapped execution. Binding does not consume the reservation.
  Native terminal and claim races either record a preceding claim or refuse it;
  after terminal commit, later claims return `ATTEMPT_TERMINAL`.
- Process outcome observation leaves the native attempt open. If the host closes
  its task first, the existing open-delegate finding remains until the host
  records terminal. No delegate completion or task acceptance is inferred.
- The authenticated CLI passes the attempt ID with an unusable client database
  address. Existing role/destination, unknown outcome and no-duplicate checks
  remain in the same integration path.
- A real disposable backup/restore preserves the exact link and process outcome.
  Restore fencing still blocks old bindings and launches; late host terminal
  observation remains possible.

Tests are in `core/run_attempt_test.go`, `localapi/runner_test.go`,
`scripts/check-local-api.py`, and `scripts/check-restore-fence.py`.
`TZ=UTC make test-integration check` and `TZ=UTC make test-lifecycle` passed.
Local logs: `/tmp/cairn-attempt-link-reservation-integration.log` and
`/tmp/cairn-attempt-link-reservation-lifecycle.log`.

## Compatibility

Migration025 adds a nullable attempt reference and lookup index. It neither
backfills old attempts nor changes historical packages. Empty attempt fields are
omitted from canonical binding requests, preserving old request hashes.

An additional disposable upgrade used the clean installed b1eeb42444a2908f0530536f64b0d3dfa11999f6
binary to create a real schema024 receipt, binding and claim through its API,
then migrated to025 with this implementation. Original binding retries returned
the same observation, repeated compilation preserved the seal and semantics,
and run reporting/status retained the original claim with no invented attempt.
Private proof: `/tmp/cairn-attempt-link-upgrade-verification.json`.

The first feature test failed against the missing attempt field. A subsequent
wildcard test setup failed because compilation already forbids wildcard task/run
pins; it was corrected to test wildcard host observations against an exact
receipt. This was a test construction correction, not a claimed production bug.

## Limits

These checks cover the provided host linkage and lifecycle contracts. They do
not establish a native Striatum sealed-input route, actual model usefulness,
host result correctness or task acceptance. Installation and a real linked host
invocation remain pending separately.
