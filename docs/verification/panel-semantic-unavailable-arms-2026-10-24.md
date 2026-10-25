# Semantic-unavailable source setup across panel arms

Date: 2026-10-24.

The panel controller now accepts `semantic_unavailable` in baseline, envelope,
batch, and combined arms. Each invocation uses its own database on a fresh
owner-only PostgreSQL cluster and materializes only the frozen `lexical_anchor`
source. The authenticated sender's Store identity and exact source history are
checked; no event is created.

The frozen `before_search/disable_semantic` transition is carried in the
controller's source-preparation receipt as `unrun`. The receipt also marks
`semantic_fallback` and `whole_source_pull` as `unrun`, with
`actor_acceptance: unassessed`, `provider_called: false`, and
`native_turn_join: unimplemented`. This four-arm extension is source setup,
not four executions of semantic fallback.

The separate [`check_panel_semantic_unavailable.py`](../../scripts/check_panel_semantic_unavailable.py)
probe remains the evidence for the local failing-worker path: authenticated
semantic search reports `DEGRADED_NO_EMBEDDINGS` and
`discovery.state: unavailable`, the lexical result is whole-pulled with exact
source identity/body, and plain lexical search finds the same source without
reinvoking the worker. That probe does not establish mandatory-context
preservation or an accepted actor answer.

Focused validation used a fresh disposable PostgreSQL cluster and separate
databases `cairn_panel_<arm>_semantic_unavailable`. All four controller checks
passed. Each receipt reported zero events and `provider_called: false`. On the
same fresh cluster, the standalone probe passed in its own
`cairn_panel_semantic_unavailable` database and reported fixture SHA-256
`05b726e7959156d20afbacfba7e87ce0e38f273823ecdee3482f38ca5ef6a1d7`,
`discovery: unavailable`, and `fallback: labelled_lexical_whole_pull`. The
source body, temporary tokens, random identities, and provider output were not
retained. `make check` and 20 focused controller/preflight tests passed.

Reproduce the full repository integration sequence with:

```sh
make test-integration
```

The integration runner invokes the four controller workflows and then runs the
standalone semantic-unavailable probe against its own database. These fixtures
verify setup and fallback mechanics only; no actor turn, task outcome, usage,
or provider cost is measured.
