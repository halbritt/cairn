# Native wake-session association

Status: implementation validation; production rollout pending.

Migration 040 links a wake attempt to the actual native conversation and
execution UUID. The native profile keeps the conversation; the slot profile
keeps the delivery. This adds no credentials and makes no process-attestation
claim. Supported hooks update the owner-only wake context after registration.

## Tested behavior

The disposable PostgreSQL tests exercise cross-profile slot/native association,
exact retries, concurrent slots competing for one conversation, stale execution
refusal, local-session visibility, second-consumer exclusion even after metadata
changes, and resume exclusion while a wake remains active. Actual restore fencing
retains that exclusion. Confirmed finish ends only the linked execution; replay
of an old finish after resume does not stop the newer execution.

The real CLI/API probe links a process-observed native conversation, retains
completion through the slot, prevents a child conversation inheriting the wake,
and resumes the original native conversation with the same UUID. Installed Claude
and Codex hooks link their actual native IDs using isolated configuration homes
and loopback-only providers. The real-model Agy probe links its native ID/model
through its existing account, with owner-wide coordination disabled outside the
fixture wrapper. All coordination writes use disposable databases.

Installed OpenCode and Hermes run under the real systemd supervisor against
loopback synthetic providers. Both link a native conversation, execute their
native completion tool and retain an explicit result before process cleanup
ends presence. The provider fixture accepts both string and content-part message
formats. OpenCode `--pure` was found to disable the required plugin; the tested
launcher omits it. Separate fixture databases prevent restore tests from fencing
another concurrently running probe.

A real worker process test detected the missing `CAIRN_LIFECYCLE_CHILD` flag.
The launcher now sets it explicitly after the runner's environment filtering,
preventing duplicate lifecycle memory capture. Native coordination still accepts
the explicit wake context. The combined runtime check covers both behaviors.

The integration suite, static checks, 107 Python tests and real backup/restore
lifecycle passed. The retained wake rows include their session associations.
These checks establish conversation linkage and explicit handling; they do not
establish pool dispatch, account availability or task acceptance.

## Independent review

The owner supplied a running Agy agent for review. Request
`eb97772c-eff8-4b7b-a471-4543408796f3` was published using a fresh resolution of
that exact UUID. Its immutable review patch is separate from subsequent fixes.
The agent was idle, so an explicitly targeted Herdr prompt triggered its next
native boundary; Cairn then leased the queued request. This is a host-assisted
wake, not evidence of an automatic out-of-band wake implementation. Review
findings were returned in result `2c86803e-7d8c-43dd-9e1f-bad0b82c1e36/1`.
The review request was explicitly handled, its response
`2e51e214-dfc3-4bed-abcb-41e81300d053` was read and acknowledged, and the local
review artifact was inspected. This completes one useful review exchange;
it does not establish the full v1 acceptance criteria.

The review's visibility concern conflated metadata disclosure with model
execution destination. The association transfers no payload permission, and a
shareable attempt intentionally cannot expose a local-only session UUID. A
related source check found that the documented hosted-only runner contract was
not enforced on wake admission/launch. Those transitions now reject local
profiles while preserving inspection and reconciliation. A failing regression
test established the missing check before the fix.

The existing hook entry point already catches I/O, JSON and missing-key errors;
silent fallback was not adopted. A JSON array did escape as `AttributeError`,
which was reproduced and fixed with an object/type check and a bounded file
read. The suggested fixture PATH/process-name changes were unnecessary for the
tested absolute binaries and plugins supplying an observed ancestor PID;
installed OpenCode/Hermes completed the combined test successfully.

The real response used the shared profile as publisher. Native inbox context
now supplies an exact response argv with the conversation UUID, stable request
ID and causation. The real API probe executes that command and verifies its
publisher and causal event. This improves the provided command contract; it
does not assert that every model will follow it.
