# Decision records

A record states one choice, the options not taken and when to reopen it. A
record is not edited to follow later work; a new record supersedes it. For
what Cairn does today, read the guide that each record links to.

Records 0004 to 0017 were written on 2026-11-25 from the JSON decision
receipts that the repository used to keep. They carry the question, the
selected option, the options not taken and the reopening conditions of each
receipt. Number 0001 was never used.

## Direction

| Record | Date |
| --- | --- |
| [0002 — Local memory loop and operating defaults](0002-local-memory-loop.md) | 2026-09-07 |
| [0018 — Local validation, no GitHub CI](0018-local-validation-without-github-ci.md) | 2026-09-16 |
| [0019 — Subscription model routing, no OpenRouter fallback](0019-subscription-model-routing.md) | 2026-10-19 |
| [0020 — Go lifecycle runtime and a Rust semantic worker](0020-hybrid-runtime.md) | 2026-11-06 |
| [0021 — Activation is part of done](0021-activation-is-part-of-done.md) | 2026-11-24 |
| [0022 — Cairn is a kernel; a swarm view, not a swarm workspace](0022-kernel-boundary-and-swarm-view.md) | 2026-11-25 |
| [0023 — The repository holds current contracts and decisions, not run records](0023-repository-holds-current-contracts.md) | 2026-11-25 |

## Feature design

| Record | Date |
| --- | --- |
| [0003 — Record forgetting and retained copies](0003-record-forgetting.md) | 2026-09-08 |
| [0004 — Hermes extension path](0004-hermes-extension-path.md) | 2026-09-13 |
| [0005 — Hermes capture latency and workstream context](0005-hermes-capture-latency-and-workstream-context.md) | 2026-09-13 |
| [0006 — Session identity and routing](0006-session-identity-and-routing.md) | 2026-09-15 |
| [0007 — One-shot scheduled publication](0007-one-shot-scheduled-publication.md) | 2026-09-16 |
| [0008 — Resuming an inbox watch](0008-resuming-an-inbox-watch.md) | 2026-09-16 |
| [0009 — Operator recovery of uncertain execution](0009-operator-recovery-of-uncertain-execution.md) | 2026-09-16 |
| [0010 — Request expiry and cancellation](0010-request-expiry-and-cancellation.md) | 2026-09-16 |
| [0011 — Response groups](0011-response-groups.md) | 2026-09-16 |
| [0012 — Suspending a worker on a provider quota failure](0012-suspending-a-worker-on-a-provider-quota-failure.md) | 2026-09-16 |
| [0013 — Recognizing native subscription-limit errors](0013-recognizing-native-subscription-limit-errors.md) | 2026-09-16 |
| [0014 — Classifying an Agy HTTP 429 result](0014-classifying-an-agy-http-429-result.md) | 2026-09-16 |
| [0015 — Claude terminal status and earlier rate-limit observations](0015-claude-terminal-status-and-earlier-rate-limit-observations.md) | 2026-09-16 |
| [0016 — Waking an existing idle session](0016-waking-an-existing-idle-session.md) | 2026-09-16 |
| [0017 — Token-efficiency experiments](0017-token-efficiency-experiments.md) | 2026-10-23 |

## Writing a record

Use the next number. State the question, the decision, the options not taken
and the conditions for reopening. Name who accepted it and when. Keep run
output and review transcripts out; put those in the Plane work item.
