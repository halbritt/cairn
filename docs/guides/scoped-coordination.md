# Coordinate a bounded group of workers

This recipe shows a coordinator session handing one review to a small, known set of
worker sessions. It uses exact topic and direct destinations, a response group, durable
waits, selected results and explicit work-item links. When the round ends, it tears down
its temporary topic. Each participant receives only the work addressed to it, and the
coordinator never polls a model to learn whether replies have arrived.

It combines existing features. Cairn adds no orchestrator, task graph or automatic
subscriber discovery here. Read [session coordination](session-coordination.md) first for
registration, inbox handling and replies.

## Who owns what

| Fact | Owner |
| --- | --- |
| Issue state, priority, assignee | Plane. Cairn reads a projection and never writes it. |
| Who receives an event | The exact destination: one session inbox, or the topic's active subscribers when the event is published |
| Whether a reply arrived | The [response group](../response-groups.md) and the coordinator's [wait](../operator/work-coordination.md#wait-for-an-exact-coordination-fact) |
| Which results matter | The operator, who links selected exact result versions to the work item |
| Whether the task succeeded | A separate review. Collection, handling and wait satisfaction are not task acceptance. |

A topic is an exact name, not a project boundary or access control list. A collection is
not a project. A work association is a navigation link that the operator selected. Matching
labels, project names or presence never create an association, and an association never
grants a session any capability.

## Before you start

- Every participant is a registered session with its own `agent/UUID` inbox. Use the agent
  and execution UUIDs supplied by your native integration. Do not register again to get
  around a stale session.
- The coordinator session's base profile is in `CAIRN_TOKEN_FILE`. Shell variables such as
  `COORD_AGENT`, `COORD_EXEC` and `COLLECTION` hold real values. Uppercase words in the
  examples are placeholders.
- The operator has bound the Plane project with `cairn work project add` and enabled
  `work-view/1`, `work-artifacts/1` and `coordination-waits/1`. Waits need a running
  scheduler (`cairn schedule-serve`) or an operator sweep.
- `next-turn-notices/1` matters only if a recipient supports quiet delivery (see below).
  This recipe does not enable `work-advice/1`.

The commands are checked against the source revision that contains this guide. A running
installation can be older, so check it first:

```sh
cairn version
cairn agent --token-file "$CAIRN_TOKEN_FILE" version
cairn work features status
```

Equal revision stamps do not prove that a deployed API, scheduler or native harness
supports a capability. Check [build identity](../build-identity.md).

## 1. Choose a temporary topic and subscribe the workers

Pick a topic name unique to this round, for example `review.` followed by a new UUID
fragment. Each worker subscribes its own session:

```sh
cairn subscribe --token-file "$CAIRN_TOKEN_FILE" \
  --agent-id WORKER_AGENT_UUID --execution-id WORKER_EXECUTION_UUID \
  --repo "$COLLECTION" --request-id SUBSCRIBE_UUID --topic review.ROUND_ID
```

A session can change only its own subscription. Subscription affects future publications
only, and other sessions' subscriptions to unrelated topics are untouched. Before
publishing, confirm that the expected workers have subscribed. Fanout uses the subscribers
at the publication transaction, and later subscribers do not join that request.

## 2. Publish one grouped request

Save the assignment as an exact source under a dedicated task, such as
`coordination/PUBLICATION_UUID`, so it does not clutter ordinary memory search. The
[event guide](../agent-event-fabric.md#dedicated-assignment-sources-and-search-scope)
shows the save-then-publish sequence. Then publish it to the topic with a response group:

```sh
cairn publish --token-file "$CAIRN_TOKEN_FILE" \
  --agent-id "$COORD_AGENT" --execution-id "$COORD_EXEC" --repo "$COLLECTION" \
  --request-id PUBLICATION_UUID --topic review.ROUND_ID --kind request \
  --response-deadline DEADLINE_RFC3339 --response-policy partial \
  --version SOURCE_VERSION SOURCE_UUID
```

Keep the event body short: it is a pointer to the exact source version, and the
workers read the details there. The group fixes its members from the deliveries created by
this publication, between 1 and 100. `partial` keeps whatever replies arrived by the
deadline. `all` marks the round incomplete unless every member replies. An empty topic
refuses with `GROUP_EMPTY`.

Check the snapshot with `cairn event-status EVENT_UUID` using the same connection flags.
The deliveries should name exactly the subscribed workers.

## 3. Wait instead of polling

Register a durable wait on the group, then let the coordinator's turn end:

```sh
cairn wait create --token-file "$CAIRN_TOKEN_FILE" \
  --agent-id "$COORD_AGENT" --execution-id "$COORD_EXEC" \
  --repo "$COLLECTION" --request-id WAIT_UUID \
  --predicate response_group_closed --dependency EVENT_UUID \
  --deadline WAIT_DEADLINE_RFC3339
```

When the group closes, at its deadline or once every member has replied, the scheduler
satisfies the wait and publishes one notice to the coordinator's inbox. For a single
direct request, use `--predicate request_response` instead. A satisfied wait records that
replies were collected; it says nothing about whether the task succeeded.

## 4. Workers complete and reply

Each worker claims its delivery, reads the exact source version, and completes the delivery
with a short selected result:

```sh
cairn inbox next --token-file "$CAIRN_TOKEN_FILE" \
  --agent-id WORKER_AGENT_UUID --execution-id WORKER_EXECUTION_UUID
printf '%s\n' '{"record_id":"SOURCE_UUID","version":1}' | \
  cairn agent --token-file "$CAIRN_TOKEN_FILE" \
  --agent-id WORKER_AGENT_UUID --execution-id WORKER_EXECUTION_UUID history
cairn complete --token-file "$CAIRN_TOKEN_FILE" \
  --agent-id WORKER_AGENT_UUID --execution-id WORKER_EXECUTION_UUID \
  --request-id COMPLETE_UUID --lease LEASE_UUID --stdin --shareable \
  --task coordination-result/EVENT_UUID DELIVERY_UUID < result.txt
```

Replace `1` with the event's exact source version. Completion does not reply on its own. Publish a response to the coordinator's inbox,
citing the request event and its correlation UUID and pointing at the exact result version
that completion returned:

```sh
cairn publish --token-file "$CAIRN_TOKEN_FILE" \
  --agent-id WORKER_AGENT_UUID --execution-id WORKER_EXECUTION_UUID \
  --request-id REPLY_UUID --to "agent/$COORD_AGENT" --kind response \
  --causation-id EVENT_UUID --correlation-id CORRELATION_UUID \
  --version RESULT_VERSION RESULT_UUID
```

Native inbox contexts supply these arguments, or a `finish_reply` helper that journals
both steps. Each member counts once. A second reply is `duplicate`, and a first reply
after closure is `late`. A late reply never reopens a closed group.

Publish the result as a pointer to its exact version. Don't paste intermediate tool
output or transcripts into events or results, and don't capture private conversations.

## 5. Read the results and link the selected ones

After the wait notice arrives, the coordinator handles its inbox: the replies and the wait
notice. It reads each reply's result at its exact version with `history`. To inspect the
collection, use `cairn response-group EVENT_UUID` with the coordinator's connection flags.

The operator links the results worth keeping to the Plane work item. These commands use
the operator's database channel. The agent API has no route for them, so a worker or
coordinator session cannot create associations or artifact links:

```sh
cairn work associate --project SLUG --item ITEM_UUID --collection "$COLLECTION" \
  --target-type response_group --target-id EVENT_UUID --request-id ASSOCIATE_UUID
cairn work associate --project SLUG --item ITEM_UUID --collection "$COLLECTION" \
  --target-type result_record --target-id RESULT_UUID:RESULT_VERSION --request-id ASSOCIATE2_UUID
cairn work artifact register --kind cairn_result --record RESULT_UUID \
  --version RESULT_VERSION --display-name 'Selected review' \
  --collection "$COLLECTION" --request-id REGISTER_UUID
cairn work artifact link --project SLUG --item ITEM_UUID --revision REVISION_UUID \
  --role output --collection "$COLLECTION" --request-id LINK_UUID
```

Other target types are `session` (`AGENT_UUID:EXECUTION_UUID`), `event` and
`run_receipt`. The work view and `cairn work artifact list` show the links and exact
revisions, not result bodies. A result read remains subject to its current scope,
sensitivity and forgetting.

## 6. Optional updates: quiet or deferred notices

Use a notice for a routine update that needs no reply. Two existing delivery options keep
it from interrupting a recipient:

- **Quiet delivery at the next owner input** (`--notice-delivery next_organic_turn
  --notice-expires-at EXPIRY_RFC3339`). Only a supported Codex native owner binding can
  receive it; see [native owner input](../native-owner-origin.md). Any other recipient
  refuses it with `UNSUPPORTED_DELIVERY` and nothing is published. The refusal never falls
  back to wakeable delivery.
- **Routine deferral** (`--notice-urgency routine --notice-defer-seconds N`, at most 60).
  The delivery exists at once but becomes available only after the deferral.

Native integrations handle up to three already waiting notices in one turn, each with its
own outcome ([compact inbox](../compact-inbox.md)). Choose `urgent` only for notices that
should skip deferral. Neither option promises an immediate wake.

## 7. Tear down the temporary topic

At the end of the round, each worker unsubscribes its own session:

```sh
cairn unsubscribe --token-file "$CAIRN_TOKEN_FILE" \
  --agent-id WORKER_AGENT_UUID --execution-id WORKER_EXECUTION_UUID \
  --repo "$COLLECTION" --request-id UNSUBSCRIBE_UUID --topic review.ROUND_ID
```

Unsubscribing stops future fanout only. Deliveries already addressed to that worker stay
in its inbox and remain claimable and inspectable. A later publication to the topic
reaches no one. Retry an uncertain unsubscribe with the same request UUID.

Cairn does not expire subscriptions or drop subscribers automatically. A worker that left
(`stopped`) or whose presence expired (`offline`) still receives topic fanout until it
unsubscribes. The coordinator cannot remove another session's subscription. A resumed
session keeps its inbox address and can unsubscribe with its new execution UUID.

Cancel any waits you no longer need with `cairn wait cancel`. Cancelling a wait does not
withdraw the request.

## Verified behavior

`scripts/check_scoped_coordination_recipe.py` runs this recipe end to end with real CLI
and local API operations on a disposable database. Only Plane is a stand-in, and it
answers reads only. The fixture uses one coordinator, three workers and one unrelated
session. It checks that:

- the topic snapshot names exactly the three subscribed workers;
- the unrelated session receives no deliveries and its own subscription is unchanged;
- a direct request reaches only its recipient;
- two of three replies leave the group `partial` at its deadline, with
  `reported_task_outcome: unknown`;
- each wait is satisfied and its notice published by a scheduler sweep;
- selected results are readable at their exact versions and linked through the operator
  channel, and the work view shows only explicit associations and no copied result text;
- the agent API returns `NOT_FOUND` for the work-association and artifact operations;
- quiet delivery to an unsupported recipient refuses with no event, while a routine
  deferred notice stays pending;
- a stopped subscriber still receives fanout, and the coordinator's own unsubscribe
  changes no worker subscription;
- explicit teardown leaves no active subscription, a later publication has no deliveries,
  and already-addressed deliveries remain claimable;
- a late reply does not reopen the group, and Plane's projected fields are unchanged.

Run it through the integration runner, or against an explicit disposable database:

```sh
CAIRN_TEST_ONLY='check_scoped_coordination_recipe' make test-integration
```

The fixture does not exercise quiet delivery to a supported native recipient. The native
fixtures listed in [work coordination](../operator/work-coordination.md) cover that. It
makes no claim about task quality, model cost or efficiency, and it changes no
token-efficiency rollout decision.

## Related work

This recipe builds on the compact notice and response envelopes (CAIRN-216), routine-notice
urgency and deferral (CAIRN-220) and bounded notice batches (CAIRN-221). It also builds on
the work view, artifacts, waits, quiet notices and advice review from CAIRN-279 through
CAIRN-284. The token-efficiency rollout gate (CAIRN-227) remains in force; this recipe
does not enable its optional opt-ins. Event admission limits are separate work described
in [event pressure and admission](../event-admission.md).
