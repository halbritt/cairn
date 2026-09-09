# Authenticated record history — 2026-09-09

Readers can now inspect retained version metadata and select an exact earlier
body through the ordinary authenticated API/CLI. The preceding
[procedure-maintenance review](procedure-maintenance-2026-09-09.md) needed operator
SQL to verify earlier note bodies; current `get` and owned-receipt replay did not
provide a record-history interface.

The [contract](../record-history.md) specifies the bounded read. The
[manifest](record-history-2026-09-09.json) retains source and test-log hashes.

## Verification

- The initial Unix API fixture created and revised shareable guidance, then
  failed history inspection because the route did not exist.
- The same caller now receives newest-first metadata without bodies and reads
  the exact earlier text through `version`. Hash, byte size, scope, writer,
  witness and timestamp match the original record.
- An exclusive version cursor continues through older revisions when a newer
  revision is appended between pages. Current and original version classes remain
  distinct; empty terminal pages and absent exact versions are explicit.
- Private records and foreign repositories refuse both history modes. Request
  JSON cannot supply its own destination. Forgotten records refuse; excluded
  older payloads expose neither text nor checksum/size, while a later available
  version remains readable.
- History honors restore admission. Inspection adds no retained-use reference
  that would prevent otherwise valid ordinary deletion.
- Actual CLI history reads passed through a disposable Unix API with no HOME or
  usable client database address. The five native tools are unchanged.

`make test-integration` passed with disposable PostgreSQL and race detection.
The subsequently extended CLI and exclusion/restore checks passed separately;
Go tests, 30 Python tests, vet, formatting and build also passed. No operational
fixtures or model calls were used for these software checks. No schema migration
or persistent historical-response copy was added.

## Interpretation and review

This exposes existing retained information for comparison. Historical text may be
obsolete, and its recorded class is not present authority. Each call has bounded
size; the host still manages combined context and decides what is worth reading.
Missing/pruned versions are not reconstructed. The history response is not a
compiler package, delivery receipt or evidence of downstream task success.

Doctrine packet `pkt-d8fd36a096563901` supported placing the read in the existing
inspection owner and preserving access/history boundaries. Its decision receipt
is `/tmp/cairn-record-history-decision.json`; schema validation and citation
consumption closure passed. Twelve residual route obligations are classified as
nonmaterial in the manifest: separate architecture/interface redesign is not
introduced, the observed missing read justifies this bounded change without a
recurring-change study, and no-change alternatives were considered in the plan.
