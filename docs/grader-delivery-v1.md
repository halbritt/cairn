# Grader delivery over Cairn events v1

`scripts/trial_grader_delivery.py` delivers a frozen
[grader delivery plan](grader-delivery-plan-v1.md) to its two declared grader
sessions through the existing [agent event fabric](agent-event-fabric.md). It
retains returned-grade custody, applies the unchanged
[grading join](grading-custody-join-v1.md), and routes disagreements to the
owner. CAIRN-107 lane 1.

The tests use only an in-memory fake fabric and a fake CLI runner. Passing them
shows that the adapter matches its source contract. It is **not** evidence that
any actual grader received a packet, returned a grade, or that agreement was
measured on a real campaign. No grader, provider, model or campaign has been
launched through this path.

## Entry points and fail-closed default

```python
dispatch(plan_dir, run_dir, fabric=F, config=CFG, authority=PIN, now=UTC)
collect(plan_dir, run_dir, fabric=F, config=CFG, now=UTC)
route_disagreements(plan_dir, run_dir, fabric=F, config=CFG, now=UTC)
```

If `fabric`, `config` or (for dispatch) `authority` is missing, each function
refuses `grader_delivery_unavailable` before reading anything.
`trial_grader_delivery_plan.deliver` still refuses that way. It forwards to
`dispatch` only when all three keyword arguments are supplied. Without a
configuration file, the CLI prints that fixed refusal and returns 2:

```sh
python3 scripts/trial_grader_release_issuer.py issue --plan DIR --instruction FILE --config FILE --output PIN.json
python3 scripts/trial_grader_delivery.py dispatch --plan DIR --run DIR --config FILE --authority PIN.json
python3 scripts/trial_grader_delivery.py collect|route --plan DIR --run DIR --config FILE
```

The instruction is a separate private `cairn.grader-release-instruction/1`
document with the exact plan receipt and binding hashes, owner/coordinator
identity, two ordered grader selections (slot, agent, native session,
execution, selected model, route-file hash, and expected reported route),
`max_request_bytes`, and `valid_for_seconds` from 1 to 300. The issuer checks
the instruction's selection against the plan, reads each grader by exact agent
UUID from the current directory, requires its exact native session and
execution, and rejects stale presence or changed reported route. It writes the
authority with mode 0600 and prints only the output pin. The owner instruction
and authority remain private files; neither contains case or oracle material.

The config file is `{"delivery": CONFIG, "cli": {"cairn", "token_file", "socket"}}`.
`CliFabric` runs the existing `cairn agent` operations `remember`, `publish`,
`event-status` and `history`. It passes the publisher's `--agent-id` and
`--execution-id` and requires a `cairn.response/1` envelope with `ok: true`.
A timeout or unparseable output gives `grader_fabric_uncertain`. The caller
then retries with the same plan, which reuses the same request UUIDs. Refusals
are fixed codes. They contain no paths, payloads or exception text.

`CONFIG` is closed: `schema: cairn.grader-delivery-config/1`, `repo`,
`owner_destination` (the owner's exact principal), and
`publisher: {agent_id, execution_id}` (UUIDs of the coordinator's registered
session). The publisher must not be a grader. The owner destination must not
be a grader inbox, the publisher's inbox, or the inbox of any excluded
engineer, reviewer, coordinator or earlier-exposed agent. Disputes go to the
owner only.

## Release authority (held separately)

Dispatch requires a pin `{path, bytes, sha256}` to a closed
`cairn.grader-release-authority/2` document. The private
[`trial_grader_release_issuer.py`](../scripts/trial_grader_release_issuer.py)
tool creates one only from a separately selected
`cairn.grader-release-instruction/1` file bound to the exact plan-receipt
digest and two current Cairn directory entries. It
does not create sessions, claim inboxes, publish events, or call a provider.
The instruction names the exact recipient identities, model and route, byte
limit, and validity (at most five minutes). The issuer reads current presence
and refuses a missing, expired, stopped, duplicate, coordinator, actor,
reviewer, or earlier-exposed grader identity. This is an owner-selected input;
its identity fields are declarations, not credentials.

```text
plan_receipt_sha256   SHA256 of the plan's receipt.json bytes
binding_sha256        the plan's grading-join binding hash
instruction_sha256    SHA256 of the separately selected owner instruction
issuer                {role: owner|coordinator, agent_id, session_id}
observed_at, valid_until   current time and no more than five minutes later
recipients            [{slot, agent_id, session_id, execution_id, model, route,
                        declared_route_sha256, metadata_source, model_custody}]
packets               [{pair_id, slot, packet_sha256}] exactly the plan's deliveries
max_request_bytes     1..65536, the limit for the whole rendered request
```

`route` records the owner-selected and directory-reported `harness`, `project`,
`workspace`, and `delivery_mode`. Cairn presence and the transactional
resolution prove that the same execution and reported context were current at
publication. They do not attest to the actual model, account, or provider
route. `model_custody` is `owner_selected_and_reported`.

The adapter never creates or extends this document. It checks that the document
matches the plan and is current at `now`. The private issuer requires the caller
to select an explicit owner instruction file; it does not infer authorization
from the plan or grader-session metadata. A matching hash and role remain
declarations; they do not cryptographically authenticate the issuer. The
issuer cannot be a recipient.

## Recipients and exclusions

Each slot is addressed as `agent/<agent_id>`, so the slot's `agent_id` must be a
registered-session UUID. If it is not, dispatch refuses
`grader_slot_not_addressable`. A recipient is refused
(`grader_recipient_excluded`) when its agent, session or execution identity is
in the plan's exclusion inventory (`actor_engineer`, `reviewer`, `coordinator`,
`earlier_exposed`) or the join policy's excluded agents and sessions.
Engineering agents therefore cannot receive fresh grading packets. Model names
come from the authority and are recorded as `model_custody: "declared"`. The
fabric cannot observe which model actually answers.

## Request rendering and envelope accounting

Before writing any source note, dispatch resolves both recipients through the
live Cairn directory. It requires one current candidate for each exact agent,
native session, execution, model, and reported route. Each publish then supplies
that exact `AgentResolution` to Cairn; the store checks execution, generation,
context revision, and presence transactionally. A stale resolution refuses
publication and is never retargeted.

Each pair-and-slot request is a canonical `cairn.grader-request/1` JSON note.
It contains:

- the request UUID
- the pair ID and slot
- the recipient identity and declared model
- the binding and rubric hashes
- the exact canonical comparison bytes (`packet_sha256` hashes them)
- the base64 slot control with its hash and length
- the closed response contract (sample IDs in plan order and verdict labels)
- fixed instructions

The arm mapping, run IDs and source rows are excluded. A rendered request that
contains a quoted `"CURRENT"` or `"CAND"` token refuses `grader_request_not_blind`.

Every request is rendered and measured before the first effect. The whole
rendered byte count must fit the 64 KiB note bound, the authority's
`max_request_bytes`, and any `bytes` slot limit. A plan delivery whose budget is
`over_budget` also refuses. Each delivery reports `rendered_request_bytes`, and
the dispatch receipt reports their sum. `slot_fit` is `fits` only for a bytes
envelope. A token or unknown envelope stays `unknown`. No conversion from bytes
to tokens is made. Packets larger than one note are not supported yet. They
refuse rather than being split.

## One-shot delivery

Request UUIDs are UUIDv5 values derived from the plan receipt hash, the binding
hash, and the pair and slot. The run ID is derived in the same way. Store
publication idempotency therefore makes delivery one-shot per plan:

- a retry, a resume after an uncertain response, or a second run directory
  gets the original source and event
- changed content under the same request UUID refuses with
  `IDEMPOTENCY_CONFLICT`

The run directory is a private append-only journal of intents, sources, events
and grades. Each file is written once. If a later read differs, the adapter
refuses `grader_request_changed`, `grader_journal_conflict` or
`grade_custody_changed`. A mismatched plan or config refuses
`grader_run_mismatch`. Failed, ignored, invalid or pending deliveries are never
republished or reassigned.

## Returned-grade custody

`collect` reads `event-status` for each journaled event. It requires:

- the exact event ID, kind `request`, source reference, destination and
  correlation
- exactly one delivery, with consumer equal to the slot inbox (a second
  delivery refuses `grade_duplicate_delivery`)

For a handled delivery with a result, it reads that exact result version with
`history`. The body must be present, and its recomputed SHA256 and length must
equal the store's `body_sha256` and `body_bytes`. Any mismatch refuses
`grade_custody_mismatch`.

The retained grade row binds:

- pair, slot, recipient agent, session and execution, and declared model
- event and delivery IDs, and the consumer
- request record, version, hash and bytes
- result record, version, body hash and bytes, kind, observed writer, witness
  and write time
- attempts and late flag
- the verdicts and reason hash

`execution_custody` is `unverified` because a delivery does not expose the
completer's execution UUID. The observed writer is the store's channel
principal. It can be shared by several sessions.

The response must be a closed `cairn.grader-response/1` object. It echoes the
request ID, pair, slot, packet hash and binding hash. It has state
`determinate` (one allowed label per listed sample) or `undetermined` (null
verdicts), and a reason of at most 16 KiB. Any other body is retained as
`invalid`. Echoed IDs alone do not establish custody. The delivery binding
does.

The rendered request includes complete `response.examples`. For a determinate
response, `verdicts` is a JSON object whose keys are every exact sample ID from
`response.samples` and whose values are strings from `response.verdict_labels`:

```json
"verdicts": {"<exact sample ID>": "<allowed label>"}
```

For an undetermined response, `verdicts` is JSON `null`. In both cases the
response has exactly the fields shown by the corresponding example; do not
return a list of sample objects or add fields.

## Agreement and disagreements

Graded rows become join submissions, with the delivery UUID as submission ID.
The unchanged `join_results(bundle, submissions, [])` applies the original
agreement and disagreement rules. Its `declared_raw_agreement` is the reported
agreement. The join's own `custody` and `verified_raw_agreement` fields are
unchanged. `route_disagreements` publishes one `cairn.grader-dispute/1` request
per determinate disagreement to `owner_destination`. Each request carries both
grade custody references and verdicts and stays blind to arm. It uses derived
one-shot request UUIDs. No disposition is created, and `resolution` stays
`unresolved`. Only the owner decides.

## Validation and remaining work

```sh
python3 -B -m unittest discover -s scripts -p 'test_trial_grader_delivery*.py'
python3 -B -m unittest discover -s scripts -p 'test_trial_grader_release_issuer.py'
```

The tests cover:

- the unconfigured refusal and the CLI refusal
- stale or mismatched authority, models and packets
- excluded engineer, reviewer and coordinator identities
- unaddressable slots and stale sessions
- tampered plan files
- blindness and the note bound
- full envelope accounting
- one-shot delivery across run directories, and resume after an uncertain publish
- duplicate deliveries and wrong consumers
- store hash mismatch
- malformed and mismatched returns
- immutable terminal custody
- pending and failed handling without republication
- agreement and disagreement through the unchanged join
- owner routing without resolution
- the CLI argv contract
- explicit owner instruction, exact plan and packet binding, live presence,
  duplicate/excluded recipient, route mismatch, short validity, and refusal
  before effects

The following remain open:

- a real owner instruction and actual delivery to two fresh, eligible grader
  sessions (none are currently available)
- observed recipient model and execution identity
- packets larger than one note
- runtime admission and capacity
- two complete weekly campaigns
