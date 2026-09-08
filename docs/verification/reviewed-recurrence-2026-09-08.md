# Reviewed-lesson recurrence — 2026-09-08

The Cairn H0 run selected a reviewed lesson, cited its record UUID, and finished
within budget with a candidate that avoided the inherited-environment regression
found in the no-lesson baseline. Supplying the same lesson directly also avoided
that regression, although that run timed out. This is positive evidence for one
known recurrence case, with overall task acceptance still unknown for both lesson
runs. It does not establish causal attribution or general memory benefit.

| Condition | Process | Duration | Original gate | Inherited-count review | Reported cost |
|---|---|---:|---|---|---:|
| M: repository only | Exit 0 | 695.951 s | Pass | Fail | $0.434793 |
| N: lesson supplied directly | Timeout | 900.096 s | Pass | Pass | $0.479834 |
| O: same lesson through Cairn H0 | Exit 0 | 891.151 s | Pass | Pass | $0.579251 |

All candidates passed independent cache-write and explicit-override checks.
The two-package suite in each reconstruction had the same rendering-contract
assertion failure previously reproduced on the historical base. No confinement
tests skipped in these delegated-cgroup reviews. These results cover the local
runtime conditions; cross-UID behavior and all preparation-failure paths remain
unverified. The candidate patches are experimental evidence, not production
Striatum changes. Review also found a vacuous prefix-length assertion in O's
own tests; the separately supplied environment checks provide the reported
protection, not that assertion.

## What was held fixed

The three fresh snapshots used identical scenario, gate, controller, relay,
OpenCode binary, Cairn binary and Pro model identities. Each allowed 900 process
seconds, 60 configured steps, 64 relay requests, 131,072 context and a 32,768
output allowance. The actual OpenCode output request was 32,000. There were no
relay refusals. Providers could vary between calls; reported costs are telemetry,
not an invoice. The H0 run did not improve elapsed time or reported cost over
the baseline in these observations.

The single lesson came from the reviewed regression in calibration L. It was
captured as selected evidence by an authenticated collector and promoted by the
delegated operator in each disposable store. It was available after that review,
not at the historical repair date. The original earlier-history trials remain
unchanged. This tests carrying a known lesson into a recurrence of the same
repair, rather than transfer to an unseen task or native search capability.

The current gate permits a cache anywhere below the dispatch workspace and the
relevant init seam. M passed that gate but introduced a count-marker variant of
L's environment bug. The additional count-marker check was therefore derived
after seeing M and applied unchanged to all three candidates. It is exploratory
post-run evidence, separate from the original gate. Model inputs and the lesson
were not revised during the comparison.

## Retained evidence and interpretation

O's actual receipt is `66c85151-6577-4d81-bb6e-115a2c3af465`. Its selected lesson
is record `6abc3ee2-1c95-4ad7-90df-505dd6049ee9`, version 2. The controller checked
the exact body digest and version before launch and against the actual receipt.
The model cited that UUID. The use report contains one actual exposure row with
`usage=cited`, `usage_witness=testimony` and unknown usage coverage; it excludes
the selection-preflight row. Citation does not prove causal influence.

Post-run reviews append assessment version 2 while retaining the original
version 1 assessments and process outcomes. M is rejected for the reproduced
environment failure. N and O remain unknown overall because the successful
checks do not establish full repair or host acceptance. Their reviewed dumps
are separate from the original trial dumps.

The [metadata report](reviewed-recurrence-2026-09-08.json) retains exact identities,
settings, original assessments, independent reviews, latest assessment joins,
costs and private evidence locations. Stores and relays stopped; native session
homes and derived caches were removed. Candidate patches and selected historical
sources remain private. No raw model prose is committed.

This establishes a bounded positive H0 recurrence observation and a concrete
next demand case. A reviewed failure with no accepted recovery still needs a
generated review item and disposition lifecycle. Completing that path is more
useful now than repeating this unchanged calibration or enabling a groomer.
