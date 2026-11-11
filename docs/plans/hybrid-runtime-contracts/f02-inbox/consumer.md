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

The consumer implements Codex and Claude claim/privacy guards, memory session
locking, initialized ledger reads, required search with its retained retry ID
and deadline, checked whole task-source history, optional search and the landed
eager whole/group/span helper. Its byte measure includes the actual hook event,
control text and source envelope. It preserves prior Codex charges, repeat and
retired-turn refusals, exact-source fallback, lease renewal before publication,
durable grants and ledger compaction. It does not rewrite captured dialogue.

`test_f02_inbox_identity.py` and `test_f02_inbox_delivery.py` compare real
checked Go processes against the literal retained `/2` Source and real CLI,
using a disposable authenticated Unix API. Comparisons include private streams,
typed ordered grant state and actual request payloads. Separate candidate
cases exercise paid whole/span pulls, deferred reports, changed pins and report
failure. The reference uses its real `/2` descriptors; it does not assert that
Python accepts the `/3` image protocol. These fixtures do not prove production
delivery, a live store result, or full F-02 acceptance.

`memoryclient.Options.ObserveRPC` supplies owned snapshots before search policy
and post-pull validation. The optional observer cannot mutate live data or
accounting; loss remains incomplete. Known-zero pulls do not initiate a call.

The separate private `lifecycle inbox report` operation accepts exactly
`trace_report`, `meter_report` and `started_monotonic`, after Coordination
releases its lock. It checks the same code/configuration generation without an
event, admission decision or session-state mutation. Recall returns materialized
JSON only; report uses the Go SDK and actual executing-image diagnostics under
the original deadline. Failed, expired or unavailable reports remain unknown
and cannot change output, refusal or grants. Traces contain selected bounded
metadata, never query/source bodies, credentials or callable objects.

The optional candidate observer records actual routes, checked returns and
tentative/final renders through the same eager helpers. Its callbacks receive
snapshots and cannot alter live policy or accounting; callback loss sets the
existing `complete=false` flag. It does not invent missing observations. Whole
and span process fixtures compare the materialized trace against Source, apart
from elapsed timestamps.

The pending Coordination join owns actual post-lock scheduling and native
settlement. Foundation owns that joined disposition and final integration.
Public lifecycle dispatch remains disabled, and existing runtime, provider and
failed-work holds remain in force.
