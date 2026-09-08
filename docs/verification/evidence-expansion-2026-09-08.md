# Supporting-evidence expansion verification, 2026-09-08

Agents can inspect one exact supporting object through an existing index handle,
with the expected SHA256 from its metadata. Body/evidence pulls share credits
and bytes, current record/bootstrap checks and destination restrictions. See the
[contract](../index-and-pull.md). This verifies bounded evidence retrieval;
it does not establish H1 model routing or improved task results.

Seven focused core tests pass: exact bytes/budget/retry and original exposure;
copy exclusion/purge with independent evidence retained; changed body/digest
retry refusal; caller/destination/unattached/sensitivity checks; oversized intact
refusal without credit spend; mixed concurrent body/evidence credit accounting;
and withdrawal refusal before cached response. The copy test also removes its
exclusion marker in owned state and verifies recovery inspection detects it.

Genuine red probes preceded missing-feature implementation, the new operation's
addition to deletion exclusion, and expected-digest pinning of retries. Mechanical
extraction errors were corrected separately. Concurrent callers retain the
existing bounded serialization-retry contract: VERSION_CONFLICT can request a
retry, while exactly four successful spends share the session budget.

`make test-integration check` passes with the real hosted CLI/API probe in
`scripts/check-local-api.py`: index, body pull, exact evidence pull and retry
through a private Unix socket with an unusable client DB address. Returned
witness remains testimony; service-observed expansion does not upgrade the
captured evidence. The seven-test final focused run adds recovery-marker damage
coverage after that integration run. `make test-lifecycle` and all 12 Python
script tests pass. All fixtures and fault mutations use owned disposable stores.

The shared body-pull guard and evidence decoder preserve existing behavior. No
new schema, session type, credit pool, external-file read or authority path is
introduced. Forgetting purges cached disclosure copies tied to the source record;
independently captured evidence remains an explicit separate residual.

Pincite packet pkt-94b7860d91ce968f, content SHA256 94b7860d91ce968f077843f814b3a083127b656129d396bd61009f4897ee1b0b, used two evidence passes.
The private schema-valid decision receipt retains 23 nonmaterial generic
interface/UI/ranking/configuration/named-procedure and pre-release-parity
obligations. Repository contracts, exact keys, authenticated action, scoped input
and authoritative gate signals support the decision. Source and tests verify
implementation; operational parity is separate. This feature adds no new result
to the [usefulness evidence](usefulness-status-2026-09-08.md). Real accepted-task,
retrieval quality and transfer evidence take priority; further experimental
recovery work is deferred unless an observed problem requires it.

## Release state

Operational installation is pending; the existing service runs c370481 with
schema028. No operational memory fixture or model experiment occurred in this
implementation.
