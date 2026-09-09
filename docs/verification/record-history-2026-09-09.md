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

## Installed use

Clean `9fb278b025e1a0897e0efcc8282831e5f738d68a` is installed as the CLI and
running API, SHA-256
`c912496c4fd12b75f9996367bfdd7fb8e7ca8701877c6a6711d137e62ec9b66b`.
Both services are active; the store process, migration 030, host configuration,
native adapter and semantic worker files remain unchanged.

The ordinary hosted profile listed metadata and read the exact Codex procedure
v6/v7 and OpenCode procedure v8/v9 bodies from the preceding maintenance review.
All four body hashes match that review's retained evidence. This inspection used
no operator SQL or database credentials and did not mutate those records. It
completes the previously operator-only comparison through the ordinary interface.
No claim about improved model decisions follows from that access check.

A selected ordinary procedure explaining history was saved as record
`cd6217ad-2f69-45f1-bcfb-a01066909855` v1 and retrieved intact from a fresh task
scope. Its source and checksum are retained in the manifest. This adds practical
review guidance without expanding the existing harness procedures or conferring
authority. Deployment and review responses remain under
`/tmp/cairn-record-history-deployment/`; raw note bodies are outside the checkout.

[CI for the exact installed source](https://github.com/halbritt/cairn/actions/runs/34392942583)
passed PostgreSQL race tests, Python tests, vet and build.
