# Claimed-inbox Go consumer

This unit ports `inbox_recall.output` / `deliver`, under the coordination lock.
The private entry is `cairn-lifecycle lifecycle inbox recall`. The public
lifecycle dispatcher stays disabled. Foundation owns the interface and final
integration; Coordination owns the caller, claims, journal, settlement and
post-lock scheduling.

The producer supplies exactly `event`, `observation`, `state`, `control` and
`started_monotonic`, with the original bridge deadline and independently checked
configuration, binding, receipt and executing-image pins in argv. No activation
flag in that frame creates a decision. The child reads the previously resolved
activation and never calls the decision writer.

Current construction checkpoint: the private process, checked inputs, identity
and privacy guards, and inbox ledger/compaction primitives are implemented.
`test_f02_inbox_identity.py` compares four real Codex/Claude owner/session
refusals with the retained pinned `/2` bridge, exact streams and unchanged grant
bytes. The reference uses its real `/2` descriptors; it does not pretend that
Python accepts the new `/3` image protocol. No API call is expected in those
identity-refusal cases.

Search, required/task-source/optional rendering, lease renewal, grant delivery
and materialized post-lock reporting are still under construction. A valid
recall reaching that unfinished body refuses with `compatibility_profile`;
this checkpoint makes no claim about those branches or full F-02 acceptance.
Both Codex and Claude remain in the full target scope. The existing eager
candidate helper on `c9bfb9f3` will be reused with the same authenticated client,
receipt accounting and the whole control/source-envelope cost function.

The working report proposal is a separate private `lifecycle inbox report`
operation after the coordination lock releases, under the same original
deadline. Its exact caller/result schema is being settled with Coordination.
No Python rendering/store fallback, API-CLI substitute or invented meter is
permitted. Reports must not change output, refusal or grant state.
