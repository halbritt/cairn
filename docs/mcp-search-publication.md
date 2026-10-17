# Attempt-only search preparation publication

`NewAttemptReadOnlyServer` requires an `AttemptRetrievalBackend` and registers
only the existing search and pull tool contracts. Ordinary `NewServer` and
`NewReadOnlyServer` keep their behavior. No command-line switch, launcher, local
API fallback, native owner, account or runtime integration is added.

Each backend search returns its actual `core.IndexResult` together with one
`SearchPublication`. The trusted parent must retain that object and bind it to
the original operation. A request-ID map is insufficient: overlapping calls can
have equal explicit request IDs, and must return distinct publication custody.
Nil and typed-nil publications refuse. The facade does not authenticate a parent,
prove object uniqueness, or implement receipt-membership authority.

After existing request validation, the facade runs unchanged `presentSearch`,
the existing pull-reserve/inspection-room calculation, and final tool-result
encoding. The attempt path includes the server-info metadata and `resultType:
"complete"` added by the installed MCP server. It seeds those fields through the
public `CallToolResult` JSON interface before encoding. Boundary tests compare
the callback's bytes with the real server's returned encoding; an SDK change to
that representation requires revalidation. No SDK distribution inquiry is part
of this implementation.

Only a fully encoded result within the remaining budget reaches
`SearchPublication.Prepared(ctx, encodedResult)`. The argument is an immutable
Go string containing the complete `CallToolResult` JSON, not only the inner
search document. It includes the generated pull request IDs. The parent must
validate exact handle/version/seal/request correspondence against its held raw
operation. Mutating that raw operation after this point cannot alter the
already-encoded result. This is bounded result size, not an allocation or
physical-memory-fit guarantee for raw results, projection and encoding aliases.

The facade checks cancellation before backend entry, after backend return,
before publication and after publication. It makes one local `Cancel` attempt
on every failed path with a returned publication, including projection,
serialization, budget and backend errors. `SearchPreparationFailed` means only
that `Prepared` was not entered; it says nothing about backend effects. A backend
failure or panic before returning an object remains the backend owner's cleanup
and uncertainty obligation: the facade cannot recover custody it never received.

Once `Prepared` is entered, an error, panic or observed cancellation suppresses
the result and passes `SearchPublicationUnknown` to `Cancel`. Successful cleanup
does not restore eligibility or turn that unknown publication into known
nonpublication. Cancellation error/panic also produces a fixed unknown refusal.
The parent must retain its own uncertainty; no retry or rollback is inferred.
`Cancel` is local cleanup even when the original context is cancelled, not a new
external-effect deadline. A trusted publisher must honor the original context;
this synchronous interface cannot forcibly interrupt a blocking implementation.

Backend, projection, encoding, publisher and cancellation diagnostics never
appear in attempt-search public failures. They become fixed
`SEARCH_PREPARATION_FAILED`, `SEARCH_CANCELLED` or `SEARCH_PUBLICATION_UNKNOWN`
messages. Pull retains its existing typed dispatch, budgets and error semantics.

The final successful check establishes local preparation only. Cancellation
after that check, transport writing, native consumption, usable receipt membership,
the native MCP-child owner/endpoint handoff and hosted dispatch/reply still need
their separate owner contracts. Nothing here admits a native runtime or completes
the weekly evaluation.
