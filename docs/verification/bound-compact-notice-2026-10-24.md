# CAIRN-216 bound compact notice probe, 2026-10-24

`scripts/check_bound_compact_notice.py` joins the pinned source coordination,
memory and inbox-recall handlers to a fresh disposable PostgreSQL database
and local Cairn API. A synthetic owner process receives a native
`SessionStart` callback. The test then uses the Store's authenticated
`session-inbox-claim` to pin one notice to a synthetic native turn and saves
that exact attempt into the private coordinator state. A synthetic reserved
`UserPromptSubmit` callback runs the real bound hook, which generates the
compact context and short handle. No native queue submission or model turn
occurred.

The Store issues a mandatory C instruction. Its whole text and record ID
remain in the bound hook's 3,000-byte output. The notice source is 3,675
bytes: under the compact inline cap, but too large beside the required
context. The hook omits its body and supplies a scoped `cairn-inbox read`
handle. The real helper returns the exact whole source and leaves the
delivery leased. Explicit `handled` settlement commits one acknowledgement;
an identical helper retry returns the same result. The publisher sees the
original delivery handled with one attempt and no invented result record.

This is a source, hook and Store transition probe. The synthetic admission
does not prove native transport, actor comprehension, accepted task outcome,
provider usage, latency, or an installed Codex/Claude canary.
