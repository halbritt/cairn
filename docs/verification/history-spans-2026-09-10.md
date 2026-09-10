# Historical byte excerpts — 2026-09-10

Agents can inspect a selected passage from an earlier note through the existing
history interface. A valid 65,536-byte body previously exceeded the native fixture's
64,000-token output room; metadata was readable but its text was not. The same
whole-body call still refuses. An explicit small excerpt now fits that room.

[The contract](../record-history.md#read-a-passage-from-an-earlier-version) requires
an exact version and a byte offset/length. Results omit the full body, retain its
version, full hash and byte count, and return selected bytes with their own hash.
UTF-8 fragments use base64. Current repository, sensitivity, forgetting and output
budget checks remain. This adds no tool, stored copy, migration or current-use
permission. Current instruction and competing-position delivery remain whole.

## Verification

- The new authenticated API regression first returned `INVALID_REQUEST`; the MCP
  regression first rejected `span` as an additional property. Both then passed.
- Disposable PostgreSQL tests read earlier rather than current bytes, check exact
  positions and hashes, EOF clipping, integer bounds and split UTF-8, and verify
  that inspection adds no references preventing ordinary deletion. Existing
  privacy, foreign-repository and forgotten/excluded-payload tests now include spans.
- `make test-integration` passed, including the Go race suite and real CLI/API/MCP
  workflows. `make check` and all 40 Python tests passed.
- Public stdio MCP and native OpenCode 1.18.21 both refused the 64 KiB whole body,
  then returned its last 36 bytes with exact source and excerpt hashes. Earlier
  Unicode fragments, malformed ranges and private-note refusals also passed.
  The OpenCode fixture uses scripted calls with no model inference. Large input
  is seeded through the disposable operator CLI because native debug capture has
  a separately recorded stdout limitation.

Baseline source was `c8a9f48`. Logs, typed doctrine evidence and the validated
receipt are retained under `/tmp/cairn-history-spans-20260910`; the
[verification metadata](history-spans-2026-09-10.json) records their hashes.
Pincite packet `pkt-dbd929f07903391a` supported retaining existing access ownership
and reusing exact byte selection. Four Go-interface obligations are nonmaterial
because this change introduces no interface or substitution hierarchy; metadata
retains their exact requirements. Both citation-consumption loops are closed.

## Value and limits

This resolves an observed inspection limit and makes long earlier notes available
in passages. It does not search historical text for an unknown offset, establish
that a fragment is sufficient context, or prove an independent task improvement.
No operational note was changed for the implementation or fixtures. Broader task
value, interpretation quality and net cost remain unmeasured. Installation and
guidance maintenance are recorded below.

## Installed build and guidance maintenance

Installed clean `4999caa` in the CLI, API and bundled native OpenCode adapter after
[both CI jobs passed](https://github.com/halbritt/cairn/actions/runs/34533931794).
The API restarted; PostgreSQL stayed running at migration 034. A backup preceded
the update, and all 78 retained versions, profile/configuration hashes, optional
semantic settings and the recent-file plugin were unchanged by deployment.

The installed ordinary hosted profile then read the earlier procedure's exact
oversized-result passage with `span`. A selected replacement updated that guidance
from v3 to v4 with the excerpt option and current source pointers. All unrelated
text stayed byte-identical; a second historical excerpt returned the same v3
passage after the correction. This one ordinary maintenance revision brings the
retained-version count to 79; it is separate from the unchanged deployment snapshot.
It records verified operating guidance, not an independent downstream task result.

A fresh installed MCP session listed the span argument and returned the same v3
passage. Its initial probe omitted the required socket flag; correcting that
probe from the CLI contract resolved the startup refusal. Fresh ordinary search
and full pull returned the corrected v4 procedure with the expected body hash.
