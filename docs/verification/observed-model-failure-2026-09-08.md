# Observed model failure and review — 2026-09-08

The authenticated OpenCode trial host now has real-model evidence, including
timeout finalization and a rejected repair that became a reviewed local lesson.
The H0 model selected and cited the intended record, but its candidate lost
explicit environment overrides and failed the original cache gate. U1 remains
partial; this does not establish native Striatum ingress or general memory benefit.

## Observed run

The fresh known-recurrence trial used controller commit `25c339d`, the installed
Cairn executable from `b976ae7`, and the existing Pro binding. Its limits were
900 process seconds, 60 configured steps, 64 relay requests, 131,072 context and
32,768 output tokens. The wrapper timed out after 900.152 seconds. The relay
recorded 36 requests, 35 completed responses and no refusals; the final request
ended with `ConnectionResetError`. Reported cost across 35 usage-bearing responses
was $0.459437, which is telemetry rather than an invoice.

Attempt `e6e4fca4-cc7d-4864-a7ec-e75b7c304f69`, receipt
`ad4f4fad-1400-425c-810b-b08d10513b58` and outcome
`66fd4ab0-a99a-47fc-96e9-0671420c1e2d` match the actual host journal. Spawn was
confirmed before releasing the wrapper. Terminal state is `timeout`, with the
returned patch digest as its corresponding result reference. No pending host
observation remains. This verifies the normal controller timeout path, not
recovery after abrupt controller death.

The model cited selected record `0719eefe-ac9c-4f7c-93fd-f177334763da`, version 2.
The use report contains one actual exposure row after excluding the preflight
row. Citation remains `testimony`, usage coverage remains `unknown`, and the
host's bounded gate assessment is `instrumented`. Citation did not ensure a
correct repair.

## Independent repair review

The candidate stayed within its permitted file scope. Review ran the existing
held-out checks against the captured candidate, then changed only environment
append order in a separate diagnostic copy. The original candidate is preserved.

| Check | Original candidate | Separate diagnostic |
|---|---|---|
| Frozen cache-lifetime gate | Fail: inherited cache outside the lane | Pass |
| Cache write and reclamation | Fail: written inherited cache survives | Pass |
| Explicit override, unrelated inherited value preserved | Fail: inherited value wins | Pass |
| Inherited count/value metadata without overrides | Pass | Pass |
| Candidate's own confined-runtime override test | Fail | Pass |

The candidate places transported overrides before the inherited environment.
The installed Go toolchain's `os/exec.Cmd.Env` contract keeps the last value for
duplicate keys. Moving overrides after the inherited values removes the observed
failures in this diagnostic. Both the reference and diagnostic broader suites
still fail `TestRelaunchPinsOrderedAttemptClosures` for the same historical empty
rendering-contract assertion. No confinement tests skipped in these reviews.
The diagnostic is reviewer-authored evidence, not an accepted model recovery or
a production Striatum change.

A supplementary unwritable-workspace probe failed on the broken snapshot and
passed on the historical fix: the fix returned a permission error without
launching a runtime. That probe used the historical private `invoke` seam. The
candidate changed its signature and moved preparation, so the probe could not
compile. This unavailable diagnostic was retained separately and removed before
rerunning the applicable review checks; it is not a candidate defect. Cross-UID
behavior and complete preparation-failure coverage remain unverified.

## Failed run to reviewed lesson

The receipt-owning observer appended assessment version 2 with the explicit
override failure, selected diagnostic evidence and an error-signature digest.
The original rejected assessment, timeout outcome, report, patch and dump remain
unchanged. The latest use row still records a rejected task and citation testimony.

Operator generation produced standalone proposal
`f41b3559-59db-428f-92c2-3ba77bd39345` from assessment version 2. Repeated generation
reused that identity. It appeared as `TASK_FAILURE` on the docket; manual review
converted it to local class-A lesson `16a90b22-ae29-436c-a73f-343c7a2dc273`,
version 1, and removed that proposal from the active docket. No promotion or
accepted recovery was recorded. This exercises the existing demand lifecycle
with a real host-observed failure and memory exposure, not automatic grooming.

[Exact metadata](observed-model-failure-2026-09-08.json) records hashes, original
and reviewed assessments, test results, proposal conversion and private evidence
paths. Original and reviewed dumps were catalog-validated before removing the
stopped disposable database, reconstructed workspaces and derived caches. Host
journals, selected diagnostics and candidate patch remain private. Operational
memory, services, the installed executable and live Striatum state were unchanged.

The next useful test of this new lesson needs explicit admission and a suitable
task condition. Repeating this unchanged calibration would not establish transfer
or general benefit. Native host ingress and the remaining roadmap requirements
remain open.
