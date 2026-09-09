# Preview source positions — 2026-09-09

Matching previews and bounded note pulls were disconnected: a search could show
a command buried in a long note without revealing where to start the excerpt.
The previous excerpt test supplied a tail offset known to its author. A new
public-store baseline returned a matching preview from a 61,636-byte note but no
position. An operational `VERSION_CONFLICT` search likewise returned a buried
OpenCode passage without a source range.

New A/B index entries include `summary_span` with the exact UTF-8 byte offset and
length of the preview's source text. Synthetic omission markers are excluded;
literal source punctuation remains. Copying that range into the existing pull
request locates the passage. Wider context remains an explicit caller choice.
Instructions omit the field because their pulls require whole delivery.

The existing preview selector computes text and position together. Locations are
sealed and packed with their entries, including their additional byte cost. New
lexical, browse and semantic indexes use schema 8; earlier formats preserve their
original encoding and packing. Body compilation and ranking are unchanged. Pull
authorization, full-source integrity, expiry, currentness and shared credit/byte
limits remain in the existing expansion path. No migration or new tool is needed.

## Verification

- The disposable long-note baseline now refuses an oversized whole pull and uses
  the returned location to read the exact matching passage with three credits
  remaining. Editing the source invalidates the cached pull; historical
  recompilation retains the original seal and position.
- Existing Unicode, identifier, tie and source-preview tests pass. A ten-second
  fuzz run completed 472,772 executions checking text, range, UTF-8 and omission
  marker correspondence. The instruction fixture verifies absent span metadata
  and continued refusal of partial instruction delivery.
- Real CLI/API, MCP stdio and native OpenCode checks pass locations from search
  directly into pulls. The CLI case uses a long source; MCP and OpenCode use
  buried passages. Exact bytes, retries and shared credit accounting are checked.
- The installed previous binary produced body schema 3 and index schemas 5, 6
  and 7. The candidate recompiles them exactly, including schema-7 ready scores
  from a deterministic disposable worker. New packages also recompile exactly.
  Old index compile retries require fresh request IDs; body compilation and an
  old cached pull retain their prior results. The ready-score fixture initially
  used the wrong receipt owner and correctly received `AUTHORITY_DENIED`; it was
  corrected to use its original authenticated channel.
- Full disposable PostgreSQL/race integration, Go tests, 30 Python tests and
  vet/format checks passed. No model task or operational fixture capture was run.

Private baseline, compatibility and test artifacts use the prefix
`/tmp/cairn-preview-location-`. The [manifest](preview-locations-2026-09-09.json)
records their hashes. This establishes a usable source-location path, not lower
total context use or improved task outcomes. Locations consume index space and
may reduce entries per page. Semantic similarity does not establish relevance;
a lexically unmatched semantic result still has its ordinary prefix preview.
