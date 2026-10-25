# Codex native turn propagation: verified boundary and remaining gap

The installed Codex 0.164.0 hook payload contains a nonempty `turn_id` on
ordinary `UserPromptSubmit` and `Stop` events. The host hook normalizer already
retains this as `observation.native_turn_id`; ordinary inbox admission used to
drop it when constructing the claim intent.

`integrations/lifecycle/coordination.py` now carries a validated nonempty ID
into ordinary Codex `UserPromptSubmit` claim intents. Explicit wake bindings and
OpenCode replay bindings retain precedence. Other harnesses and
`turn_exclusive` are unchanged. The focused test verifies that the exact ID is
persisted on claim, a later mismatching turn cannot take over the claim, and
empty IDs remain omitted.

The end-to-end disposable source-forgotten fixture did not produce an exact
claim-to-app-server-turn receipt. It stopped during hook trust setup: the app
server exited with status 2 before answering `initialize`, with empty stderr.
The same Codex 0.164.0 `app-server --stdio` initialize handshake, using the same
staged `CODEX_HOME` and isolated network, succeeded from both the feature
worktree and clean `main` (exit 0, empty stderr). Therefore the failure is
specific to the fuller fixture setup, but its cause is unknown. No source
forgetting, native claim, or settlement assertion is claimed as passed.

Network isolation was enforced with a network namespace and outbound route
preflight. No provider request was sent. The loopback/provider-response join is
not established by this verification. Full CAIRN-226 acceptance remains open.
