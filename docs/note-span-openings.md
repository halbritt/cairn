# Source openings beside note spans

An optional `opening_bytes` on `cairn_pull` requests an exact prefix of the same
current note beside an explicit `span`. It accepts 0–768 bytes; zero or omission
keeps the existing response. Nonzero values require `span`. CLI positional pulls
use `--opening-bytes N` together with `--offset` / `--length`; JSON and MCP use
`opening_bytes`. The OpenCode tool exposes the same optional argument.

The response's `source_opening` is a byte span starting at zero, clipped before
the requested span so the pieces never overlap. If the requested span starts at
zero, the opening is omitted because that source position is already included.
The requested passage is unchanged and still clips only at EOF. Both pieces
belong to `selection.record` and the whole-source hash in `span.source_sha256`.
Each has its own byte boundaries and SHA256; a split UTF-8 character uses
`body_base64`, following the existing span contract. `record.body` remains empty.

The pair uses one expansion credit and charges both serialized pieces to the
existing receipt budget. If it cannot fit, the whole request refuses without
spending bytes or credit. It does not silently discard the requested opening,
shorten the passage, enlarge the budget, or make a second read. A caller can
choose a smaller opening or another range with a new request UUID. An identical
retry returns the original response after current access and source checks;
changing the opening size is a different request intent.

Instructions and competing positions still require whole delivery. Receipt
ownership, destination, expiry, source integrity, current eligibility, deletion
and required-context checks remain in the existing guarded expansion path.
The prefix is exact source text, not an inferred project label, applicability
judgment or authority. A relevant qualification can lie elsewhere in the note.
Read the complete current note before editing it or relying on broader claims.

This opt-in interface is separate from search preview packing and hook-eager
opening excerpts. Existing consumers receive no automatic new source context.
A passing API or budget test does not establish better native source selection
or task outcomes; those need separate evaluation and deployment evidence.

Both the serving API and the calling tool must support this argument. Older
versions can reject it as an unknown field; a newer tool description alone does
not prove API support. Existing running MCP sessions may retain their original
tool catalog until a new session initializes.
