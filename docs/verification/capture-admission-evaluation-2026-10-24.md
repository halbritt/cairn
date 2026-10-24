# CAIRN-222 capture admission: frozen local evaluation

Date: 2026-10-24. This is a synthetic, model-free evaluation of the existing
`transport_only` **shadow** candidate in `integrations/lifecycle/memory.py`.
The [runner](../../scripts/evaluate_capture_admission.py) executes the real
capture admission and selected-write validation with a local selector stub.
Its [aggregate result](capture-admission-evaluation-2026-10-24.json) contains
no dialogue bodies, source records, operational memory, credentials, or
provider output. It neither changes admission nor calls a provider or database.

The frozen cases start from a confirmed substantive two-message snapshot.
Each subsequent case is evaluated against that same state; counts exclude the
one setup selection. `quota_failure` is a synthetic selector refusal represented
by `API_UNAVAILABLE`, not evidence of a live provider quota response.

| Case | Shadow candidate | Unchanged selector calls | Selected writes | Capture marker advanced | What a gate must preserve |
| --- | --- | ---: | ---: | --- | --- |
| Same snapshot | no | 0 | 0 | no | Existing digest deduplication |
| Exact transport wake | yes | 1 | 0 | yes | Current source and settlement still handled by the inbox route |
| Owner correction after wake | no | 1 | 1 | yes | Corrected preference |
| Unfinished handoff | no | 1 | 1 | yes | Recoverable checkpoint |
| Status-only turn | no | 1 | 0 | yes | One empty capture call remains |
| Synthetic quota refusal after wake | yes | 1 | 0 | no | Failure and retry state remain visible |
| Source refusal after wake | yes | 0 | 0 | no | Source failure must not become false completion |

Across seven cases, unchanged capture calls the selector five times. The
candidate appears in three cases and intersects two selector calls, but only
one has an empty stub selection. Both required writes remain outside the
candidate, so the **fixture miss count is zero**. Two candidate cases have
unresolved failure outcomes; a gate that simply treats every candidate as a
confirmed skip would hide those failure paths. The status-only case adds one
empty selector call that the exact-wake candidate does not cover. These counts
are fixed fixture events, not population rates, provider tokens, or savings.

The existing [capture observation](../plans/token-efficiency-2026-10-23.md)
records the shadow candidate without skipping selection. The local test suite
also covers unchanged snapshots, selected writes, selector timeout/error,
state-commit failure, source/write failure, and concurrent capture separately.
This evaluation does not join an observed candidate to accepted owner task
outcomes or measure the auxiliary model's true cost. It does not simulate
append during selection, state-commit races, exclusions, or a real quota error.
No production gate is justified by these seven cases; the shadow-only path
remains in place.

## Reachable hook boundary

The exact-wake candidate does not currently identify a safe hook at which to
skip selection. Codex removes a complete reserved wake while reading its
transcript. Its Stop gate counts only kept message offsets, so a wake by itself
does not trigger selection; after a correction or handoff arrives, the changed
dialogue is eligible for capture. Claude, OpenCode and Hermes retain the exact
wake in their capture dialogue, but expose capture at PreCompact or SessionEnd,
where checkpoint recovery must remain available. No candidate-based skip is
enabled on those boundaries.

`test_stop_ignores_exact_wake_then_captures_later_correction_and_handoff` runs
the Codex hook handler against a temporary transcript and state directory. A
stubbed first selection confirms a prior snapshot; a complete wake does not
advance the Stop marker or start another selector; then a correction and
unfinished handoff reach the real PreCompact capture path and produce both
selected writes. This is local preservation evidence only: the selector and
store are stubbed, and it establishes neither provider savings nor accepted
owner outcomes. A future gate needs a different reachable, non-recovery
boundary plus metered outcome evidence before suppression is considered.

For this bounded change, the selected option is to keep runtime admission
unchanged and add route-level regression coverage. A Stop-only suppression
would be unreachable for the exact Codex wake because the transcript filter
already removes it; suppressing the shared candidate at PreCompact or
SessionEnd would remove a recovery opportunity. No capture call, classifier,
configuration flag, or production gate was added. CAIRN-222 remains open for
the metered paired outcome evidence named above.

Decision review: Pincite packet `pkt-57469349dc11364b`, content SHA-256
`57469349dc11364b3b2fc5f42bbbffe6535a8a760ea904f2f35eb23a85108dcd`, doctrine
`doctrine-f6bbb5196a3f8bf9`, retriever `retriever-ec995ecdd083b2c8`, release
`d3e0c0d4ccd1920b2e045c156f1cf0db4fc5f04f`. The question was limited to a
provider-free admission change on the current hook paths. The evidence supports
test/documentation work under local execution authority; it does not support
selecting or accepting production suppression. Retained per-invocation usage
and paired accepted correction and handoff outcomes remain material to a
future gate. Revisit only when those are available and a reachable
non-recovery capture boundary is identified.

To revisit a gate, freeze a representative capture cohort and predeclare the
subscription route, model, token/elapsed limit, and acceptance criteria before
any provider call. Pair unchanged capture with the proposed skip across owner
correction, superseding decision, unfinished handoff, status-only turn, exact
wake, quota/source failure, and retry/state-commit boundaries. Count missed
notes/checkpoints, extra task-model or classifier work, capture input/output and
cache tokens, selected-write confirmation, and accepted task outcomes. Stop on
any missed correction, lost handoff, false closure, or subscription auth/quota
failure; do not use an OpenRouter fallback.

Reproduce locally:

```sh
python3 -B scripts/evaluate_capture_admission.py \
  --output /tmp/cairn-222-capture-evaluation.json
PYTHONPATH=scripts python3 -B -m unittest test_evaluate_capture_admission -q
```
