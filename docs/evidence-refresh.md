# Evidence refresh and inspection

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

Managed large artifacts, source-span/version predicates, scheduled refresh jobs,
class D payload deletion and complete dependent qualification remain unfinished.
