# Disposable R06 status consumer fixture

`scripts/check_r06_status_consumer.py` exercises the public
`CLI.CaptureWatchDrain` and `ExchangeWatchBindings` consumer against a real
Cairn API and a PostgreSQL cluster owned by `disposable_host.OwnedCluster`.
It builds the API and a private Go test process from the current source. The
test process uses the concrete file host and records real transport inputs,
outputs, errors and exits. It supplies no modeled API replies.

The initial library dependency is
`f2080c8bec82a715c6ca84089c40cd374ce64883`, with the accepted authenticated
read/client implementation from `cde4a4aca91f96a154a9dc9a34ac8170eafd728f`.
The library's review and integration remain separate from this fixture.

Run from a clean committed checkout, using a new evidence directory:

```sh
mise run host:exec -- --timeout 10m --weight 2 -- \
  python3 -B scripts/check_r06_status_consumer.py \
  --evidence "$HOME/.cache/r06-consumer-run"
mise run test:unit -- ./internal/lifecycle/coordination -run '^TestSharedWatch' -count=1
mise run check
```

Use repeated `--case NAME` arguments for focused diagnosis. Existing evidence
directories are refused; preserve failures. The normal host `HOME`, tools and
credentials stay available. Only the application's `CAIRN_HOME`, principals,
sessions, tokens and database are fixture-owned. Fixture tokens are excluded
from the copied evidence. No provider/model is invoked.

The terminal and prior-execution cases create and settle a synthetic request,
retain its exact completion, result and response correspondence in a journal,
then inspect it through real D2/D4 reads and exchange the whole selection.
The current reader authenticates the prior-execution lookup; the returned
attempt's session remains historical and its lease fields remain redacted.
The cleanup case captures and joins an owned tool process, reports its actual
terminal state through the API, and retains those exact requests for inspection.
Active and unfinished attempts, missing and foreign history, stale readers,
foreign-owner and invalid-token authentication, and unavailable transport must
refuse receipt creation and publication. Missing and foreign cases explicitly
require a real null control response followed by a refused historical lookup.

The serialization case uses a separate fixture PostgreSQL actor to hold the
delivery row, observes the API backend blocked by that actor, and commits a
same-value update. This changes the MVCC version without changing the logical
protected state. A real serialization refusal must propagate to the consumer;
an unreproduced bounded interleaving is recorded as UNKNOWN and fails the
fixture, with no simulated replacement.

The invalidation case first captures a valid receipt, then a separate fixture
actor makes a real native claim before the public exchange. Snapshots before
and after each inspection phase distinguish actor changes from inspector
mutation. Publication must refuse, preserving the complete old selection.
The unpublished prepared selection may remain; its identity, complete content
and shared lock inode are retained separately from successful publication.
The inspector transport admits only four read operations; it cannot claim,
renew, settle, reconcile or cancel work.

Evidence includes source/tree/dependency and executable hashes, build and test
argv, selected actor requests, actual replies, pagination fields, protected
table and journal snapshots, publication results, and owned process cleanup.
The helper uses explicit empty callback census for this stopped fixture only.
This qualifies a finite consumer seam. It does not establish complete production
unit census, effective hook refresh, grant disposition, atomic cross-view
snapshots, freedom from activity after the final read, full R06 acceptance or
production adoption.

## Existing owner grant disposition

`scripts/check_r06_owner_grant_drain.py` joins the same real API/control seam
to installed Python Source and checked Go memory entries. Run its finite cases
from a clean committed checkout with a new evidence directory:

```sh
mise run host:exec -- --timeout 10m --weight 1 -- \
  python3 -B scripts/check_r06_owner_grant_drain.py \
  --evidence "$HOME/.cache/r06-owner-grant-run"
mise run test:unit -- ./internal/lifecycle/ledger ./internal/lifecycle/memoryhook \
  -run '^TestR06GrantDisposition' -count=1
```

The fixture creates positive activation decisions through the actual checked
owning-session entries for Codex and Claude, stages and selects disabled B,
and invokes the refreshed owner. It preserves the original ledgers and
initialized markers. Go and Source use separate directories; comparison never
resets or deletes a positive ledger. Existing normalization checks runtime
measurements and excludes only those measurements from state comparison.
The retained evidence includes raw bytes and hashes as well as normalized
JSON, owner session/turn identities, actual commands, outputs and exits.

The declared Codex `PreCompact`/`Stop` and Claude `PreCompact`/`SessionEnd`
entries are exercised with empty dialogue. These
existing capture entries do not constitute grant settlement. The fixture
requires the retained positive activation to remain visible and the real
inspector to refuse before and after them. A separate positive unit must remain
byte-identical. Separate negative fixtures exercise unknown and malformed
activation decisions, a foreign authenticated reader, and an unresolved real
owning-session obligation. Inspector snapshots must preserve database, native
state, journals, memory files and the selected bindings.

The existing activation operation remembers the first decision for a turn;
refreshing a disabled selection does not replace a retained `true` with
`false`. Inbox compaction preserves recent decisions and explicitly retained
owner turns; it is reached when a new admitted inbox grant is persisted.
The finite fixture does not manufacture new work to age out a grant or supply
an artificial set of live turns. Its successful assertions establish executable
parity and refusal evidence. Positive grant disposition remains **BLOCKED**:
an owner policy/interface decision is needed for retirement before a unit
retaining a positive activation can satisfy D1. This is not full R06, whole-unit
refresh, HC05, production adoption, or R07 evidence.
