# Bounded note excerpts — 2026-09-09

An accepted 60,042-byte procedure could be indexed but not pulled, even with
1,000,000 bytes of declared input room: the expansion session caps additional
content at 24,000 bytes. The existing evidence-span interface could not read the
note's own body. The disposable-store reproduction retained that refusal before
the implementation changed.

An explicit span on the existing body-pull operation now retrieves the requested
tail within the ordinary budget. It retains the full source identity and metadata,
returns the selected bytes and their digest separately, and leaves the record body
empty so the partial result is distinguishable from a complete record.

## Contract and implementation

`ExpandRequest.span` is optional. Its omission preserves existing whole-body
request identity, response shape and accounting. A/B excerpts accept byte offsets
0–65,535 and lengths 1–65,536, with the start inside the source and clipping only
at EOF. Class C instructions require whole delivery. Each range uses one of the
same four expansion credits and encoded-response bytes. No migration or extra
operation was added.

`span.source_sha256` identifies the complete indexed body; `sha256` identifies the
selected range. `offset`, exclusive `end` and `total_bytes` describe its position.
UTF-8 text appears in `body`; a split multibyte character uses `body_base64`.
The existing complete-source integrity, caller, destination, currentness, mandatory
bootstrap and expiry checks run before cached-response lookup. A different range
requires a different request UUID. Retraction and forgetting retain their guards.
Usage is labelled `authorized-body-span-pull/1`, distinct from full-body inspection.

Note and evidence excerpts share only byte slicing and encoding. The earlier
`EvidenceSpanRequest` and `EvidenceSpan` Go names remain aliases; evidence JSON and
range-error contracts remain unchanged. The Go request/result structs gained an
optional field; repository positional literals were updated. CLI `agent pull`
accepts `--offset`/`--length`, MCP accepts the nested `span`, and the native OpenCode
adapter validates and forwards it. Partial text must not be used as though it were
the complete body when editing a note.

## Verification

- The initial actual-PostgreSQL reproduction failed at the requested tail with
  `BUDGET_REFUSED`. The completed test reads the exact tail, checks both hashes,
  empty full-body field, byte positions and three remaining credits. Oversized
  whole and span requests spend no credit. Retry equality is compared as JSON;
  an intermediate test incorrectly compared Go time-location objects with
  `reflect.DeepEqual`, and was corrected to check the public response contract.
- Range, UTF-8 split/base64, EOF clipping and exhausted-credit checks passed.
  Optional instructions refuse excerpts and still support their original whole
  pull. Changes outside the selected prefix, including corrupt source bytes with
  no version advance, prevent cached disclosure. Caller and destination mismatches
  remain refused. Mixed whole/excerpt concurrent requests share exactly four credits.
- Whole/excerpt forgetting fixtures exercise refusal before and after purge, with
  the same guarded preview, conflict and authority boundaries. The cached response
  is checked for removal after purge.
- The complete disposable integration suite passed with Go's race detector. Its
  CLI/API fixture stores a long note, refuses its whole body and reads its exact
  tail. Independent MCP stdio and native OpenCode tools read excerpts, preserve
  retries, refuse changed intent and reject stale versions. OpenCode also rejects
  malformed span arguments before effects. No model answering calls were made.
- The installed previous binary created whole-body and whole-evidence cached
  responses in the disposable database. The new binary retried both with identical
  response values and budgets. All Go tests, 30 Python tests, vet, formatting and
  build passed. Later focused checks cover the additional complete-source and
  cached-purge assertions.

This makes retained long-note content accessible through the ordinary bounded
interface. It does not establish improved model task outcomes, persisted as-cited
span relations, or complete task-level context accounting. Longer sources still
need selective reading; starting another retrieval does not make context free.
The prior saved evidence-span lesson supplied a useful pointer to the existing
interaction and guard requirements; this session also inspected current source,
so no unique causal contribution is claimed for that retrieval.

The [manifest](note-spans-2026-09-09.json) retains source/check hashes and decision
provenance. Runtime installation is recorded separately when completed.

## Local deployment

Clean source `559307c` is installed as the CLI and running API; both executables
have SHA-256 `b79d63c574560add0d7eb10fced886f3d8a355c5460d1084c360256831b5df2b`.
The bundled OpenCode adapter was updated with the existing connection settings.
Its SHA-256 is `4d07a80fd26585b5a9a71744da2f0598b2d751cccabbe8ed8b96355bb0c444d0`.
The API restarted successfully and both user services are active. Previous files
are retained privately. Codex configuration, OpenCode connection settings and
semantic worker/drop-in hashes were preserved; no schema migration was added.

A fresh native Codex CLI 0.153.4 conversation loaded the five ordinary tools from
a generated configuration, read the existing version-6 procedure and requested
bytes 17–100. The exact 83 bytes, complete/partial digests, empty full-body field,
shared credits and identical retry were verified against the full read. This was
an ordinary hosted-profile read, with no new fixture capture or model turn.
The disposable native OpenCode check covered the matching adapter before install.

[CI run 34384075765](https://github.com/halbritt/cairn/actions/runs/34384075765)
passed for exact implementation commit `559307cbffe4f95fa4c85f54804f5d14f3e65197`:
PostgreSQL tests with race detection, Python tests, vet and build. Private deployment
artifacts and hashes are included in the manifest. These checks establish deployed
behavior, without changing the task-value limits described above.
