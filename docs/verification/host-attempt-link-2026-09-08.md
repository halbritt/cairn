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
host result correctness or task acceptance.

## Installed and actual host verification

Code `b976ae7ae75d156bfb127decfba882e8babab3f7` passed
[CI](https://github.com/halbritt/cairn/actions/runs/34223190908) and was built from
a clean clone with Go1.25.0. The installed executable and running API share
SHA-256 `8dab57688eaf612c803478d338c1a32f73387dcba7bca26018d27e3cef6467c4`.
A cataloged backup preceded migration to025. Both services are active, and the
existing observer receipt remains inspectable with an unusable client DSN.

An actual Cairn `make build` then ran through the deployed observer wrapper. The
host started a real wrapper process behind a startup pipe, recorded its spawn,
and only then released it to prepare and launch the build. The wrapper retained
attempt `10f74049-45b1-4b40-9d9f-1f9c792f518a`, receipt
`8446d37b-f15e-4f16-960b-62da91e958ce`, and outcome
`bbbbee94-2722-405f-a7b1-4e2355d2870d`. Its process exited0.

The host checked the produced Go executable's clean source revision and recorded
its digest as the corresponding terminal result. The API run report retained the
exact attempt link and unknown task assessment. No model was invoked or native
Striatum contract changed. Operational record-version digest remained unchanged;
real host, receipt, context and outcome metadata were added. No synthetic
operational memory fixture was created.

Private local evidence: `/tmp/cairn-attempt-link-install-verification.json` and
`/tmp/cairn-attempt-link-operational-verification.json`. The completed host script
refuses a repeat when its intent exists; retained exact observation JSON supports
recovery without starting another build.

The design review used validated Pincite release
`d3e0c0d4ccd1920b2e045c156f1cf0db4fc5f04f`, corpus
`corpus-2026-07-12-a11702cc9217`, final packet `pkt-8f278f9a19da479c`.
Two evidence passes closed material identity, gate, preservation and consumer
obligations; the typed local receipt retains nonmaterial omissions separately.
