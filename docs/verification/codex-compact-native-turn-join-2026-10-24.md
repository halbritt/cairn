# Codex compact native turn join

This provider-free check extends the existing disposable Codex compact notice
and response fixture. It verifies that a claim selected from the native
session inbox stores the exact `turn/start` ID observed by app-server, and that
the compact context for the same attempt carries that ID. A metadata-only hook
wrapper records event names and turn IDs, then signals after the registration
turn's `Stop` hook has returned; this prevents publishing between the apparent
idle transition and completion of that hook.

The first registration turn completes before the fixture publishes the
delivery. The second turn receives the scripted Responses sequence, reads the
exact source through the compact handle, settles the delivery as handled, and
reaches its own observed native `Stop` boundary.
The fixture asserts that the Store claim retry returns the same native turn ID,
and preserves existing event, delivery, source, causation and one-attempt
settlement checks.

## Disposable run receipt

The run used Codex CLI 0.164.0, the staged Codex binding copied to a private
home, a disposable Cairn API and PostgreSQL cluster, and a loopback Responses
server. `bwrap --unshare-net` isolated the fixture; the runner also preflighted
external routes. No provider request was sent.

| Case | Event | Delivery | Attempt | Registration turn | Compact turn | Causal request |
|---|---|---|---|---|---|---|
| Notice | `b2007709-1392-4d62-a001-ec5d5491cf1b` | `505ee0a0-d7d5-44ad-a2ae-86a0dc71d81a` | `684387d2-5a61-4fa5-8d4a-22665aee705e` | `01a170d4-7a9e-7eb3-8776-cd99a1da2e35` | `01a170d4-7d36-7091-a9c0-b4be479523f5` | — |
| Causal response | `9067d573-f666-430e-a459-010d1585144a` | `cb989be3-2f28-43ab-b1a2-b9a70d949eef` | `3f3e409b-317c-4b0e-b6b6-610d9605de61` | `01a170d4-903c-7041-bc8a-46cdd5c2b68b` | `01a170d4-9322-7152-9c98-6483457c1013` | `b5d45e6e-72b0-4553-8d38-ffc23f626dd2` |

Both cases passed with exact claim/context/app-server turn equality, 6,795-byte
source reads, explicit handled settlement, and one attempt. The causal response
references Bob's committed fixture result and the original request as its
causation. The fixture reported four scripted Responses IDs (`resp_1` through
`resp_4`) per case. These are local fixture identifiers, not provider response
IDs. The choice to read and settle was prescribed by the fixture response
sequence; this does not establish a model decision, actor comprehension,
provider usage, latency, cost saving, or accepted task outcome.

An ordinary turn-only claim now stores `native_turn_id` even when the caller
does not preselect a delivery ID. Migration 069 relaxes only that poll binding;
exact-delivery wake claims still require both IDs, and exclusive turns still
require an exact delivery. A focused Store test covers this path and its retry.
CAIRN-216 remains open for paired task outcomes, real subscription usage and
latency, measured savings, and canary/rollback evidence.

Migration 069 is a widening constraint change with no data rewrite. Retain it
after any turn-only poll rows exist; rolling the check back would make those
rows violate the schema. An older API can start with the widened schema, but
cannot create or idempotently retry a turn-only claim. No production database
or installed binding was changed for this verification. The installed API
revision `19f39f9` does not contain this change, and no production activation
was attempted. Live model decisions and provider usage remain untested.
