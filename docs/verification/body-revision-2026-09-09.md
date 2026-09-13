# Body-only note revision, 2026-09-09

Correcting a note's wording previously required copying up to ten draft fields
from a pull, including scope, applicability, relations and attribution. The
native OpenCode integration test and retained operational procedure corrections
both reconstruct that draft explicitly. A simple text correction can now use
`cairn_edit` with `record_id`, `expected_version`, `request_id` and `body`.

The core reads the saved draft inside the revision transaction and changes only
its body. It uses the same attempt/record lock order, version comparison, active-A
restriction, version insertion and writer stamping as full edits. Scope,
sensitivity, kind, applicability, relations and declared delegation attribution
are preserved. The authenticated writer of the new version is recorded normally.

The Unix API exposes `/v1/revise`; raw `cairn agent revise` and operator
`cairn revise` accept its JSON request, which also includes `repo`. Native tools
supply their configured repository. The API returns only record ID and version,
so this convenience operation does not disclose a stored body or its metadata.
MCP and native OpenCode retain the same five tool names and full-draft edit form.
An edit must supply exactly one of `body` and `draft`.

A revision has its own stable intent and cached response under the existing
mutation mechanism. Reading the current draft occurs inside that transaction,
after checking for a committed retry. An identical retry returns the original
revision even after another edit; it does not rebuild its intent from a later
version. Changed intent refuses with `IDEMPOTENCY_CONFLICT`; a fresh stale request
refuses with `VERSION_CONFLICT`. Relation validation can still refuse a revision
when its retained dependencies are no longer eligible.

## Choice and preservation

The observed burden is repeated metadata reconstruction, not evidence that agents
have already corrupted metadata. Leaving the full-draft interface unchanged
retains that burden. A client-side read-then-edit helper would require extra care
to preserve original request identity after an ambiguous response. The existing
store transaction can own both operations without adding a cache or another
storage layer. Full edits remain the path for intentional kind, attribution or
relation changes, subject to their existing restrictions.

No SQL migration, ranking change, automatic capture or authority promotion is
included. Existing full edits use an extracted transaction helper with unchanged
statements and validation. Revision responses contain no body, so they do not
add a body-bearing cached response to the deletion inventory.

## Verification

Core tests exercise a cross-writer revision of a pinned, related, attributed
procedure; compare all stored draft metadata; retry after a later full edit;
reject changed intent, stale versions and wrong repositories; and race a full
metadata edit against a body-only revision. Exactly one racing edit succeeds,
with no mixed body/metadata result. Invalid intent is rejected before store
access. MCP tests also refuse B/C revisions and ambiguous body-plus-draft input.

The first integration run failed a new test expectation after ordinary deletion:
the record is removed, so a fresh revision returns `NOT_FOUND`, not
`AUTHORITY_DENIED`. The expectation was corrected to match the existing deletion
contract; the product did not change in response to that failure.

Native OpenCode and authenticated CLI integration exercise body-only revisions,
exact retries, changed-intent and stale-version refusals, followed by fresh
searches and exact pulls. Native argument validation remains in the adapter;
no standalone TypeScript typecheck is claimed.

This makes selected knowledge correction easier to perform. It does not establish
independent model judgment, reduced task error rates or a controlled memory
advantage. Existing usefulness requirements remain open.

## Installed use

`make check`, `make test` and the full native/API integration passed. After a
backup, the candidate CLI/MCP executable and native adapter were installed and
the API restarted. No profile, harness permission or database schema changed.

A normal OpenCode session using scripted loopback completions revised the saved
setup procedure from version 5 to 6 through the new body-only form. The selected
correction replaces obsolete full-draft-only guidance and adds the verified
browse continuation procedure. Its exact retry returned version 6 again. A fresh
search/pull returned the same body with metadata preserved. A separate native
Codex conversation retrieved that exact version and text through its real MCP
client. No model inference occurred; this session selected the correction.

The [manifest](body-revision-2026-09-09.json) records source and installed hashes,
terminal checks and private artifact pointers. Operational bodies and raw harness
output remain outside Git. The binary was built from pending source, rather than
a clean release commit.
