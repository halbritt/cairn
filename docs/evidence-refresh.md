# Evidence refresh and inspection

Explicit evidence capture accepts up to 1 MiB of decoded source bytes.
`cairn agent evidence` reads an `EvidenceRequest` JSON object from stdin with
`request_id`, `repo`, `body`, `source` and optional `sensitivity` (default `local`).
Use an existing provisioned profile and choose the source deliberately. The
`source` field is a label or locator; capture does not fetch it.

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
