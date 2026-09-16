# Agent event fabric verification — 2026-09-15

Scope: [selected v1 contract](../agent-event-fabric.md), derived from the owner's
v0 draft and authorization to finish, implement and deliver it. PostgreSQL polling
replaces the draft's recommended NATS transport while retaining direct/topic
delivery, offline operation, retries, history and provenance.

## Evidence map

| Contract | Executable evidence |
|---|---|
| Retry-safe immutable publication; exact historical source | `TestAgentEventsPublishRetryExactVersionAndAtomicCompletion` |
| Topic fanout and subscription cutovers; unknown kinds; event pagination | `TestAgentEventsTopicMembershipFanoutAndPaging` |
| Concurrent polling; expiry; old-token fencing; renew/retry; terminal failure | `TestAgentEventsLeaseConcurrencyRetryExpiryAndRollback` |
| Result and consumption commit together; concurrent completion retries create one result | `TestAgentEventsCompletionRollbackAndConcurrentCompletion` injects a PostgreSQL trigger failure after result insertion |
| Authenticated ownership and visibility; cached private publication stays private | `TestAgentEventsIdentityAndVisibility` |
| Forgotten source remains explainable and can fail terminally; subscription paging | `TestAgentEventsForgettingAndSubscriptionPages` |
| Concurrent publication positions and complete paged enumeration | `TestAgentEventsConcurrentPublicationCursor` |
| Restore invalidates leases while retaining work | `TestAgentEventsRestoreFenceInvalidatesLease` |
| Private causal parents cannot leak through public events; result forgetting does not resurrect body bytes | `TestAgentEventsPrivateCausationAndForgottenResult` |
| Offline help and early argument rejection | `TestEventHelpAndInvalidCommandsNeedNoCredentials` |
| Actual terminal commands through authenticated Unix API; source reads, response causation, independent fanout, process restart, leases, forged identity refusal, stable named profile provisioning | `scripts/check_agent_events.py`, invoked by `make test-integration` |
| Real dump/restore preserves every event, delivery, subscription and subscription-history row | `scripts/test-local-lifecycle.sh`, exact before/after JSON comparison |

Tests use disposable PostgreSQL clusters. Synthetic profiles and messages remain
under the test directories; the operational database is not used for tests.
Checks establish the exercised mechanics, not general agent usefulness or
exactly-once external side effects. Acknowledgments remain agent testimony.

## Design review

The prior review used Pincite packet `pkt-817804b3e9c0301b`, content SHA256
`817804b3e9c0301bc49eca5befbc0af8198a4f9175a3724965021a0b8f9955cc`, corpus
`corpus-2026-07-12-a11702cc9217`, doctrine `doctrine-f6bbb5196a3f8bf9`, retriever
`retriever-ec995ecdd083b2c8`, from validated release
`d3e0c0d4ccd1920b2e045c156f1cf0db4fc5f04f`. It informed explicit invariant
ownership, temporal delivery contracts and repository-contract precedence.
The subsequent owner instruction authorized implementation and delivery.

The contract now assigns recovery and delivery state to Cairn. Tests above cover
the previously material request/lease/completion and recovery gaps. Performance,
capacity, arbitrary downstream effects, distributed replication and real task
benefit remain unmeasured and are outside the claims. A future broker requires
evidence of polling latency or capacity pressure and its own failure tests.
Implementing NATS immediately and retaining manual handoffs were considered;
the owner authorized delivery, and PostgreSQL already owns the durable state.

## Check and installation results

Passed: `make test-integration` (all Go packages with race checks and CLI/API
probes), `make check`, `make test-lifecycle` (including exact event-table
backup/restore), and all 101 Python tests. The final integration run includes
nine event-store tests and the actual CLI restart/profile probe.

The operational store was backed up before installation. Migration 035 and clean
source build `e40fb88c6170d02efcda80c4b3e4462770b496f1` are installed. The CLI
and running API report the same revision and `vcs_modified: false`. The existing
semantic worker configuration was preserved. The first version probe immediately
after restart raced socket startup; the following probe succeeded against the
same running service without another restart.

Named hosted profiles `codex`, `opencode`, `agy`, `claude` and `hermes` are
provisioned as `agent/NAME`. Read-only `event-stats --profile NAME` succeeded for
all five and showed empty inboxes. No test traffic was published to the operational
collection. Existing shared-memory profiles remain configured. Test traffic,
including all handling and restart checks, used disposable databases.

The full event delivery contract is implemented and the above checks passed.
Real coordination usefulness, broker scaling and automatic agent wakeups are
not claimed; polling is the selected v1 interface.
