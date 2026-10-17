# Fixed engine command plans

`FixedCommandPolicy(executable, socket, token_file, repo, task, run, context=())`
is an immutable parent-supplied binding. `context` is a tuple of pairs for the
engine's configured revision, workspace_sha256, task_class, task_phase, binding
and capability values. It retains no mutable configuration dictionary.
`plan(operation, args=(), body=None)` returns a frozen `CommandPlan` containing
only operation, rebuilt argv and normalized JSON text (or no stdin for search).

This module performs no command execution, filesystem access, API request,
credential acquisition or gate debit. It has no authorization callback or default
permit. A well-shaped plan does **not** authorize a read. The execution owner must
separately bind the exact plan to the current attempt, trusted configuration and
sources, held receipt/handle/evidence membership, deadlines, reservations and the
sole MemoryDispatchGate debit. It must not treat a manually constructed dataclass
as proof of any of those facts.

## Search dialect

The planner accepts the actual `Memory.search` dialect: explicit matching
`--repo`, `--task`, `--run`, `--tokens`; optional `--semantic` and
`--offset`; repeated `--entity-file` and `--kind`; the exact configured context
flags; and exactly `--` followed by one query argument. It reconstructs these
arguments in the engine's order. Single-valued flags cannot repeat. A query that
starts with an option remains data after the delimiter.

The source bounds are 256–1,000,000 tokens, offset 0–10,000, query at most 4096
UTF-8 bytes, at most 16 distinct canonical relative file entities (512 bytes
each), and at most eight valid retrieval labels. Empty query is supported with
entity hints. Duplicate entity hints remain supported within the distinct-count
bound and are emitted once, matching the core normalization semantics. Context values are compared exactly with the parent binding. Revision is a
40- or 64-character lowercase Git object ID; workspace digest is 64 hex
characters; other configured labels are at most 256 UTF-8 bytes and cannot be
`*`. Digest case is preserved, following the current public validator.

Other CLI dialects are deliberately unavailable here, including browsing,
text-selector pull, semantic-query file/stdin, request-ID search flags and global
routing/session flags. Go's global flag parser stops at the operation, so flags
after it cannot reselect its socket/token. Search flags can still change scope or
read a semantic-query file; the closed engine dialect prevents those routes.
Executable, socket and token path always come from the parent binding. No shell
string is constructed.

## JSON read dialects

Pull, pull-evidence and history require empty args and one strict JSON object.
Duplicate keys, trailing documents, non-finite values, unknown fields and wrongly
typed numbers refuse. The existing gate's 128-KiB canonical request bound and
128-KiB API JSON-body bound remain in force. Bodies are rebuilt from typed fields.

* Pull: nonzero canonical UUID request_id, receipt_id and handle; optional note
  span (offset 0–65,535, length 1–65,536); optional opening_bytes 0–768, with a
  nonzero opening requiring a span. Zero opening preserves receipt-policy
  selection. The planner does not decide whether a particular source requires
  whole-body delivery or supports an opening; the current receipt/store and
  parent's held authorization must establish that.
* Pull-evidence: the same identifiers plus evidence_id and a 64-hex-character
  expected_sha256; optional evidence span (offset 0–1,048,575, length
  1–1,048,576). Supporting-object membership, actual digest and destination
  eligibility remain held evidence/store checks.
* History: record_id and optional version, before_version, limit, span and repo.
  Version/cursor bounds are 0–2,147,483,647; limit is 0–100 (zero means the store
  default). Positive version excludes nonzero paging fields; a note-sized span
  requires positive version. The rebuilt body always carries the fixed repo;
  any supplied repo must match.

Canonical UUIDs are not restricted to version4: the engine's legitimate UUIDv5
retry substitutions and optional span changes remain representable. Preserving
them is not evidence that they derive from an offered receipt. Pull schemas have
no repo/task/run field; extra scope fields refuse instead of being silently
injected or stripped. The parent must establish their fixed-repository membership
from the current-attempt receipt ledger before execution. History similarly
requires a parent-owned read policy; knowing a record UUID is not that policy.

The four operations remain classified by the existing gate. This command planner
does not broaden the native tool whitelist beyond search/pull, or grant history
and evidence reads to an actor merely because a JSON body passes validation.

## Source and validation

The dialect follows `cmd/cairn/agent.go`, `cmd/cairn/agent_search.go`,
`core/types.go`, `core/compiler.go`, `core/entities.go`,
`core/kind_filter.go`, `core/currentness.go`, `core/observations.go`,
`core/index.go`, `core/evidence_expansion.go`,
`core/record_history.go` and `localapi/request_limit.go` at base d1e7d68.
The actual authored engine's Memory.search/call forms were supplied from its
held source and exercised through the existing inert engine test loader.

Pure tests cover actual engine-generated search and pull requests, fixed context
and scope, option/file-routing attacks, JSON ambiguity, UUID and integer types,
span/opening/history boundaries and immutable plan output. Their inert transport
collects plans; it does not invoke a CLI, socket, API, model or provider. These
tests do not establish receipt authorization, remote service integration,
physical memory fit, native admission or full CAIRN-107 acceptance.
