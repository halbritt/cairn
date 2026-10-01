# Allocate search room for a whole source

Use `inspection_policy: "first-fitting-whole/1"` with an explicit
`memory_budget_bytes` when the next step is to inspect a complete source.
Cairn calculates the charged source cost; the caller does not need to guess
`min_pull_bytes`. The two options cannot be combined.

```json
{"query":"deployment rollback","memory_budget_bytes":4500,"inspection_policy":"first-fitting-whole/1"}
```

CLI equivalent:

```sh
cairn agent --token-file "$HOME/.local/share/cairn/hosted-agent.token" search \
  --repo "$HOME/git/cairn" --task TASK --run RUN \
  --memory-budget-bytes 4500 --inspection-policy first-fitting-whole/1 \
  'deployment rollback'
```

## What the result means

Core keeps required context whole and uses the existing eligibility, ranking,
instruction and optional-budget policies. It considers ranked, eligible source
units in order. Qualified competing positions form one indivisible unit. It
reserves the calculated charge for the first unit whose preview and complete
pull fit with required context and response metadata, then uses spare room for
later previews. It does not pull any body automatically.

The sealed `memory_budget` extension uses `cairn.memory-budget/3`:

- `inspection_policy` identifies the allocation rule.
- `inspection_status: "ready"` identifies the first indexed unit as the
  reserved target. Its existing sealed ID, version and body hash identify the
  source; use its complete `pull_arguments` to inspect it.
- `min_pull_bytes` is the calculated charged expansion allowance at issuance.
  It includes metadata and escaping, not just body text.
- `skipped_units` counts earlier eligible units omitted because their whole
  inspection could not fit. Affordable does not mean most relevant.
- `no_whole_fits` means eligible candidates existed but no whole unit fit.
  `no_candidates` is distinct. Required context is still returned when it fits;
  an impossible required-context/envelope budget is refused.

The reservation belongs to the receipt's shared allowance. Reading another
source can spend it. A repeated search reports the current remaining balance
without replenishing it; `ready` and the derived minimum describe the original
allocation, not a new grant. Pulls still revalidate current eligibility,
source versions, mandatory context and access. Changed facts can invalidate a
planned read. A fitting source is not necessarily applicable or correct.

## Budget and compatibility

The mode keeps the existing total budget and 24,000-byte expansion ceiling.
Native adapters check their final serialized response alongside the receipt's
remaining allowance. Note preparation includes its guidance overhead and stays
lexical; ordinary search can use semantic discovery with its existing labelled
fallback. Each page has its own budget, and offsets follow original source-unit
positions. Count all calls, errors and repeated exposures against the separate
aggregate task allowance.

The field is explicit: omitting it preserves the legacy preview allocation and
numeric-reserve behavior. Changing it requires a new request UUID; old retries
and receipts keep their original semantics. An older API or facade may reject
the option. Never silently drop the policy or cap on retry.

The API version response advertises the separate optional
`inspection_capabilities` declaration. Existing `retrieval_capabilities/1`
remains unchanged. `cairn_client_info` reports facade support separately from
API support; an absent or unrecognized declaration means unknown. Check the
registered tool schema as well as the running API before selecting this mode.

Ordinary preview search remains available when breadth matters more than an
immediately readable whole source. Declining an irrelevant candidate is valid.
Any further search or permitted partial read needs remaining aggregate room;
there is no automatic retry, alternate query or extra grant.

Availability and a successful whole pull establish mechanics only. Useful
recall requires evidence that verified guidance informed an actual decision or
action, with its costs and alternative explanations retained.
