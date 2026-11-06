# Event admission documentation audit — 2026-11-06

This audit updates documentation for the event admission implementation at
`4388ade5256d56bd8bf2fb1ba7c2f027912541d4` (CAIRN-286–293). It is tracked as
CAIRN-298. The earlier [implementation report](https://artifact.harm.org/egab5k/index.html)
records runtime validation and its limits; this documentation pass does not
repeat or expand those runtime results.

## Scope and review

Astra, Fable and Argon inspected the current guides against source and existing
tests, then reviewed another model's changes. Argon led the documentation item;
the coordinating Astra integrated the result.

| Model | Update area | Cross-review |
| --- | --- | --- |
| Astra | Developer, API, CLI, test-fixture and refusal references; project overview and vocabulary | Operator and recovery procedures |
| Fable | Event, closure, scheduling, pool, native inbox and agent workflow guides | Astra's references and project overview |
| Argon | Deployment, upgrades, restore, recovery and operator procedures | Fable's workflow contracts |

The initial root and `docs/` inventory contained 417 Markdown files: 129 current
surfaces and 288 plans, verification records, historical or pinned documents.
The mechanical scan also included other repository Markdown. Current guides received a scan for
the affected contracts and source review of relevant passages. Unrelated memory
guides were not subjected to a new behavioral acceptance audit. Pinned design
sources, dated experiments and historical plans retain their original claims;
they are not presented as tests of schema 084.

## Corrected claims and coverage gaps

| Finding | Correction and source |
| --- | --- |
| Project overview, capability page and changelog omitted event admission | Added the disabled default, explicit limits, schema/binary/reconciliation gates, reserved replies and current feature links. Sources: [`event_admission.go`](../../core/event_admission.go), [`event_admission_enforcement.go`](../../core/event_admission_enforcement.go). |
| Policy JSON Schema allowed limits without a workload description | Added conditional `required: workload`, matching the server validator. Documented that JSON Schema's character limit does not enforce the server's UTF-8 byte limit. Sources: [schema](../event-admission-policy.schema.json), [`EventAdmissionPolicy.validate`](../../core/event_admission.go). |
| Local HTTP retry-header behavior was implied for relayed calls | Documented that the relay retains status/body but does not forward `Retry-After`; clients can use `admission.retry_after_seconds`. Sources: [`server.go`](../../localapi/server.go), [`relay.go`](../../localapi/relay.go). |
| Exported comments misdescribed partial reconciliation and explicit closure | Partial batches commit their work without a completion certificate. A declared request deadline alone does not prevent explicit closure; an open group or waiting request-response wait does. Source: [`agent_closure_reservations.go`](../../core/agent_closure_reservations.go). |
| Some workflow guides still described implemented coordination features as unavailable | Linked current scheduling, response-group, request-control and wakeup contracts; documented fresh admission versus transfers of accepted work. Sources: [`agent_schedules.go`](../../core/agent_schedules.go), [`event_admission_transfers.go`](../../core/event_admission_transfers.go), [`agent_pools.go`](../../core/agent_pools.go). |
| Generic refusal references omitted admission counters and committed-progress warnings | Documented typed capacity failures separately from durable compiler refusal traces, and scheduler/stopped-notice warning fields. Sources: [`event_admission_enforcement.go`](../../core/event_admission_enforcement.go), [`agent_schedules.go`](../../cmd/cairn/agent_schedules.go), [`agent_stopped_backlog.go`](../../core/agent_stopped_backlog.go). |
| Recovery wording could conflate missing closure reconciliation with a rate-generation invariant failure | Kept distinct recovery guidance, qualified eligible reserved replies, and distinguished existing-work settlement from fresh operator reissue. Source: [`event_admission_rates.go`](../../core/event_admission_rates.go), [`agent_event_reissue.go`](../../core/agent_event_reissue.go). |
| Test setup could be read as safe with only one DSN; fixture selector included a suffix the runner does not match | Documented both explicit disposable DSNs and a private CLI home, the limited scope of TestMain, and suffix-free fixture selection. Sources: [`test-postgres.sh`](../../scripts/test-postgres.sh), [`testmain_test.go`](../../cmd/cairn/testmain_test.go), [`disposable_host.py`](../../scripts/disposable_host.py). |

The four primary event-admission guides are now required by the documentation
checker's completeness tier. That tier checks file presence and minimum content;
it does not prove the claims in those files.

## Validation

- `python3 -B scripts/verify_docs.py --json`: passed all four tiers after trunk
  integration across 454 Markdown files, including 2,435 internal links, 548 CLI
  invocations, 377 code blocks and all 29 required documents. The 70 JSON and
  288 shell blocks passed their syntax checks; other fenced languages are
  counted but not executed.
- `make check`: passed Go vet, generated API contract drift checks, Go formatting
  and the SHA-256 checks for all four pinned design sources.
- The policy example and ten boundary cases passed JSON Schema validation checks,
  including rejection of missing/blank workload, excess fanout and zero burst.
  The Unicode case confirms that the server must enforce the separate byte cap.
- Cross-review findings were corrected and checked again against source. Runtime
  Go changes are comments only; the remaining edits are documentation, its JSON
  Schema and the checker's required-file list. Store and native integration
  suites were not rerun for this documentation pass.

## Verification limits

Changed command names, fields and behavior were checked against parsers, types,
transaction paths and existing assertions. Documentation syntax checks do not
execute the examples. No production store or installed policy was changed, and
no live-model efficiency or workload-capacity measurement was performed.
