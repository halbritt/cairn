# 0002 — Local memory loop and operating defaults

Status: selected for implementation, 2026-09-07. The operator delegated the
remaining decisions and authorized continued work while away. This supersedes
the initial review's decision deferrals; it does not assert unimplemented gates
already work.

| Design decision | Selected default |
|---|---|
| O1 | Continue the existing Go repository and implement usable vertical slices. |
| O2 | Manual promotion; maximum grant ancestry depth two below the root. No automatic promotion service. |
| O3 | `local` content only to local destinations. `shareable` is an explicit creation choice. H0 supplies advisory data and instructions requiring delivery only; runtime-enforced mandatory policy refuses H0. No native-memory files overwritten. |
| O4 | One-time root installation by the trusted local operator channel, stamped with the OS UID in the CLI. Embedded hosts establish agent/service identity outside request JSON. Service spawn and terminal events remain distinct from model testimony. |
| O5 | Dedicated owner-only PostgreSQL cluster and Unix socket under the user's Cairn data directory. Evidence bytes initially in PostgreSQL, bounded to 1 MiB each, so a database backup includes evidence. Manual backup before upgrades; daily backup recommended, seven local copies. No automatic B/use/audit pruning yet; no RPO/RTO guarantee until a restore drill is measured. |
| O6 | Emergency authorized payload redaction may remove bytes while preserving minimal conflict membership and audit facts. Ordinary retraction must not erase a conflict. Implement only with reverse-impact and purge accounting; no bypass flag. |
| O7 | Preserve the accepted existential availability gate. Required multi-premise evidence policies remain a separate, explicitly configured extension. |
| O8 | No learned ranking, interleaving, or automatic authority changes. Deterministic lexical retrieval, outcome joins, and replay first. |

Scope uses an exact repository and explicit task/run constraints. `*` is an
intentional wildcard in task/run only; empty remains unknown and is rejected.
This makes project lessons reusable without interpreting a missing key as
global scope. Ordinary edits cannot widen or change scope. Creation through a
constrained host must fit that host's repository boundary.

The first compiler has no embeddings. It reads current records, grants,
evidence, and conflicts in one repeatable-read transaction, selects mandatory
instructions directly, ranks optional material deterministically, and commits
the receipt before returning. The optional ceiling remains the smaller of 10%
of available input room and 6,000 tokens. Unknown tokenizers use UTF-8 byte
count as a conservative upper bound; this sacrifices capacity for safety.

Canonical CBOR and BLAKE3 seal the semantic body. Nonce, request identity,
wall-clock receipt time, and transport details remain outside the seal.
Delivery is availability/contact, never proof of obedience. Exit zero remains
an observed process result, never verified task acceptance.

No model call or background grooming job is necessary for this loop. The next
decision is driven by measured use on actual recurring work, not a commitment
to implement every proposed subsystem before the owner can try it.
