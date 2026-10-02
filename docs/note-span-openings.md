# Source openings beside note spans

New context-index receipts bind a 96-byte source-opening policy. A subsequent
partial `cairn_pull` includes an exact prefix of the same current note, without
requiring the agent to request context separately. This does not change ranking
or enlarge the receipt's byte allowance. The internal policy is sealed into the
receipt. Receipts that predate this policy retain their previous response and
retry behavior; upgrading does not add openings to them.

Optional `opening_bytes` on a note pull overrides the prefix length with 1–768
bytes and requires an explicit `span`. Zero or omission uses the receipt's policy
(which is no opening on older receipts). CLI positional pulls use
`--opening-bytes N` with `--offset` / `--length`. JSON, MCP and OpenCode expose
`opening_bytes`.

The response's `source_opening` starts at zero and clips before the requested
span so the pieces never overlap. If the passage starts at zero, the opening is
omitted. The requested passage is unchanged and still clips only at EOF. Both
pieces belong to `selection.record` and the full-source hash in
`span.source_sha256`. Each has its own byte boundaries and SHA256; a split UTF-8
character uses `body_base64`. `record.body` remains empty.

The pair consumes one expansion credit and charges both serialized pieces to
the existing receipt budget. If it cannot fit, the whole request refuses without
spending bytes or credit. It does not silently discard context, shorten the
passage, enlarge the allowance or make a second read. A caller can choose a
smaller opening or another range with a new request UUID. An identical retry
returns the original response after current access and source checks. Whole
pulls remain unchanged. Instructions and competing positions still require
whole delivery.

Unknown receipt policies fail integrity checks. Old readers that do not know
the sealed field cannot reproduce a new receipt's seal. Historical recompile
preserves the policy. A repeated lexical search across a policy upgrade may
return `STALE_PACKAGE`, as with existing presentation-policy upgrades; issue a
new search UUID. Semantic retries retain their earlier sealed policy.

The prefix is exact source text, not an inferred project label, applicability
judgment or authority. Qualifications can lie elsewhere in the note. Read the
complete current note before editing it or relying on broader claims. Hooks
validate an API-supplied pair and do not fetch a duplicate opening; their older
whole-read excerpt path remains separate.

An optional-field prototype showed no native uptake in its development trial.
This receipt-bound default still requires evaluation before promotion: exact
byte and budget tests do not establish better source application, task outcomes
or fleet adoption. Both API and adapter versions matter; older APIs can reject
an explicit `opening_bytes` field and running MCP sessions can retain old tool
catalogs until a new session initializes.

## Readers and relays

An API client receiving a paired expansion must declare
`Cairn-Accept-Source-Opening: 1`. The current Go client sends it for note pulls
when decoding into `core.Expansion` or `json.RawMessage`, including the current
MCP facade and CLI. Other response types do not implicitly declare support.
Raw HTTP consumers must preserve both pieces before sending the declaration.
This header conveys response support only; it grants no access or authority and
does not change request identity.

The API refuses a paired read with `426 PROTOCOL_UNSUPPORTED` when the declaration
is absent. This check follows current access and integrity validation, precedes
cached delivery and charging, and leaves the original request UUID reusable after
upgrading the reader. Malformed or duplicate declarations return
`400 INVALID_REQUEST`. Whole reads, spans starting at zero, and old-receipt spans
without an explicit opening remain available without the declaration.

Updated relays forward the declaration but never supply it themselves. Older
relays may strip it, causing a refusal even for a capable client. Upgrading the
relay permits the same request to succeed. This does not require a wire protocol
version increase; existing clients and servers retain unrelated operations.
Installing a new binary does not upgrade running MCP facades. Such facades must
start with the new binary to receive paired expansions; an API-only upgrade will
refuse their paired reads rather than charge for discarded context.
