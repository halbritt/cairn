# JSON Unicode integrity, 2026-09-09

A real API reproduction submitted a JSON evidence body containing invalid UTF-8
and received HTTP200/OK. Go's decoder replaces malformed Unicode while producing
a valid Go string, so the existing store-level UTF-8 check ran after source bytes
had already changed. Lone Unicode surrogate escapes have the same problem.
Operator decoding and agent forwarding reproduced the gap separately.

The request paths now share a Unicode check before decoding or sending serialized
JSON. The API authenticates first, reads within its existing body limit, checks
Unicode, then uses the standard JSON decoder with the existing schema, unknown-
field and one-request rules. Operator bounded JSON decoding checks the same text;
the API client checks its serialized request before HTTP. This is a Unicode guard,
not a replacement JSON parser or a change to captured source semantics.

## Observations and verification

The initial API test failed with success instead of refusal. Initial CLI tests
showed operator decoding accepted all three malformed cases and the agent sent
them to its API. The corrected tests reject raw invalid UTF-8, lone high/low
surrogates and broken pairs. The API refuses without reserving the request UUID;
a valid capture using that UUID then retains the exact source digest.

Valid UTF-8, paired escaped emoji, escaped literal backslashes, NUL escapes and
an explicitly supplied U+FFFD are preserved. A bounded three-second fuzz run
completed 12,199 executions without a failure; that count includes invalid UTF-8
inputs discarded by the valid-string property. Fixed cases
cover surrogate ordering and escaping so a literal `\\ud800` is not mistaken
for an unpaired escape. Fuzzing is supplementary evidence, not exhaustive proof.

Actual agent and operator CLIs reject malformed serialized requests, then reuse
the refused IDs for valid captures. Both preserve valid escaped pairs, literal
backslashes and replacement characters through retained evidence inspection.
The agent runs without client database access. Existing exact-size text/binary
capture, selected files, JSON envelopes and stdio MCP remain in integration.

## Scope and compatibility

All operations using the bounded operator decoder or API request handler gain
this check. Existing valid JSON request intent and captured bytes are unchanged.
No schema migration, note rewrite, new operation, native tool, authority or sharing
policy is introduced. Errors do not echo request bodies. Authentication and the
existing 128 KiB/512 KiB/8 MiB request bounds remain in place.

The check sees serialized bytes at these boundaries. It cannot identify earlier
replacement performed by an upstream serializer or native SDK. Go callers must
use explicit base64 for arbitrary bytes, and a caller intentionally supplying
U+FFFD is not rejected. Specialized import/configuration decoders outside these
request paths are not claimed as covered. Historical altered captures are not
automatically identified or repaired. Malformed legacy requests now refuse on
retry too; the guard runs before the operation and its idempotency lookup.

The saved evidence guide was pulled before implementation and retained the prior
binary-input constraint; current source and previous conversation also informed
this review. This fixes a verified capture defect, without establishing a net
memory benefit or crediting the guide with independent discovery. L4's broader
lifecycle work and durable task value remain open.

[Metadata](json-unicode-integrity-2026-09-09.json) retains failures, completed
checks and decision provenance. Installation is recorded in the next section and
in the metadata's `installation` block.

## Local installation

Installed CLI/API `463ccefb1f4b7a851527e1edff3febb4fb577252`, SHA-256
`3d377effdafd2bcb50ec1e293f45b6b91ef033d50efab2315faccc4b7b5b0455`. Both version responses agree; running
API PID 897597 matches the installed binary. An operational catalog-backed backup
preceded replacement. PostgreSQL, migrations, semantic worker, native adapter and
connection settings are unchanged. Synthetic captures remained in disposable tests.

The existing evidence guide was revised v6→v7, preserving its earlier body and
metadata. Exact mutation retry and fresh ordinary pull verify the selected update.
CI 34435235516 was `in_progress` when this receipt was written. That is an
as-of observation, not current project validation status. Companion metadata
retains build, backup and guide identities; current validation is local.
