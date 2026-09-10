# Evidence refresh and inspection

Explicit evidence capture accepts up to 1 MiB of decoded source bytes.
`cairn agent evidence` reads an `EvidenceRequest` JSON object from stdin with
`request_id`, `repo`, `source`, optional `sensitivity` (default `local`), and
one nonempty source representation: UTF-8 text in `body` or arbitrary bytes in
`body_base64`.
Use an existing provisioned profile and choose the source deliberately. The
`source` field is a label or locator; capture does not fetch it.

For binary files, encode the selected bytes as canonical padded standard base64
(for example, Python's `base64.b64encode(data).decode("ascii")`). Do not decode
arbitrary binary as UTF-8 or put replacement characters into `body`. An example
request capturing the three bytes `00 ff fe` is:

```json
{"request_id":"d5f3a8c5-ea2c-45aa-a392-1585fdd9fd99","repo":"/path/to/repo","body_base64":"AP/+","source":"explicitly selected binary source","sensitivity":"local"}
```

Choose an actual repository, source label and fresh request UUID; submit through
`cairn agent evidence` or the operator CLI's `capture-evidence`. Reuse the same
UUID and representation for an exact retry. Changing bytes or switching between
text and base64 is different request intent, even if decoded bytes match.
Base64 input rejects invalid characters, noncanonical padding bits, missing
required padding, line breaks and simultaneous nonempty `body`. Empty sources
and decoded data above 1 MiB refuse before reserving the request identity.
The limit applies to decoded bytes; base64 does not raise it. Existing text-only
requests retain their earlier retry identity. Older servers reject the new field;
upgrade the API before using it. No schema migration is needed.

Direct Go callers must also use `BodyBase64` for non-UTF-8 bytes. Such bytes in
`Body` now refuse, including retries of that legacy input form: JSON request
hashing cannot distinguish all invalid UTF-8 strings. Already captured evidence
is not rewritten or removed. The operator `capture-evidence` command retains
its 128 KiB encoded request limit; use `agent evidence` for sources needing the
full 1 MiB decoded allowance.

Capture returns an evidence identity and checksum, not an automatic note or a
qualified claim. Existing explicit citation, destination and pull checks still
apply. UTF-8 evidence is returned as `body`; non-UTF-8 evidence is returned as
`body_base64`, regardless of its input representation.
[Binary capture verification](verification/binary-evidence-capture-2026-09-09.md).

The authenticated CLI/client/API allow an 8 MiB encoded envelope for this operation,
so a full-size source can fit even when each source byte requires six bytes of
JSON escaping. The decoded 1 MiB limit remains enforced. Oversized sources or
envelopes return `INVALID_REQUEST`; neither truncates data or reserves a capture
request ID. Repeat a successful capture with the same ID and content to retain
its original identity. Ordinary note `create`, `edit` and `revise` use 512 KiB
envelopes; remaining API operations keep 128 KiB.
The [transport verification](verification/evidence-limit-2026-09-09.md) covers
exact bytes, retries, limits and the actual agent CLI.

`cairn check-evidence` accepts JSON `request_id` and `evidence_id`. It checks the
retained inline bytes against their captured SHA-256 and persists a generation,
method, timestamp, expected/actual digests, requester and affected-record count.
The caller cannot supply the resulting state. A local agent profile can invoke
`check-evidence` through the Unix API; hosted profiles cannot inspect these checks.

The method `inline-sha256/1` establishes stored-byte correspondence. It does not
verify the truth or relevance of the source, fetch its locator, or establish that
an upstream document is still current. A matching retained object becomes
`resolvable`; differing bytes become `divergent`. Transport retries return the
original check without incrementing its generation; use a new UUID for a new
observation.

Status, check history and affected claim generations commit together. Refresh
invalidates impact previews for directly evidence-linked records, which also
changes fingerprints of known transitive relation previews. A check refuses
rather than partially invalidating more than 1,000 directly citing records.
Ordinary retrieval still verifies bytes; it need not wait for a background job
to detect changed data. Historical recompilation uses its retained earlier gate
facts and does not rewrite the past from the latest check result.

[Evidence impact inspection](evidence-impact.md) lists the exact directly citing
versions, known transitive relation links and recorded exposures behind an
affected-record count. It preserves historical citations after correction and
does not infer authority or causal influence.

`cairn evidence-checks EVIDENCE_UUID` inspects up to 1,000 check generations.
`cairn evidence EVIDENCE_UUID` returns the current captured object and status.
UTF-8 payloads use `body`. Explicit binary payloads use `body_base64` and leave
`body` empty, so JSON encoding cannot silently alter the captured bytes. Expected
and actual digests remain available for comparison.

Converting a failure/recovery proposal now verifies its selected source evidence
again, in addition to checking assessment versions. Unavailable evidence refuses
conversion. Deferral, dismissal and inspection remain available; a checksum does
not approve a lesson or confer promotion authority.

[Precise supporting citations](evidence-citations.md) retain full-source identity
and optional byte passages on new qualified claim versions. Reference availability
also compares the current object with that citation-time digest; an object-level
check alone cannot revalidate a changed citation.

Managed large artifacts, broader source relations, scheduled refresh jobs, class D
payload deletion and complete dependent qualification remain unfinished.
