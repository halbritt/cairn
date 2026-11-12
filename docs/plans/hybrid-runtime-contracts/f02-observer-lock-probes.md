# Observer-backed API-call snapshots

The Hermes fixture's `Calls.append` performs a real nonblocking flock probe when
the API receives `recall-observation`. Deep-copying the list subclass reconstructs
it through `append`, replaying that probe without another frontend report.

The two existing paired collectors and the separate Hermes Source snapshot now
use `deepcopy(list(self.calls))`. The captured container is a plain list and the
request bodies/headers remain independent deep copies. Later nested copies cannot
replay observer callbacks. The real `Calls.append` algorithm, every assertion and
original method membership remain unchanged. Results are not deduplicated,
truncated, filtered or fabricated; a real False is preserved.

`scripts/test_f02_observer_lock_probes.py` runs the existing collectors with real
H13 installed Source/main and checked Go processes. A delegating parent-only
flock observer counts physical attempts separately from recorded results and
frontend requests. The original functions are traced to retain their actual
captured objects; later copies must not change physical/recorded tallies or
original API values. Nested body mutation must remain independent. The separate
malformed-identity Source snapshot is also exercised through its original method.

Positive controls require one physical probe and one recorded result per actual
report. Negative controls acquire a separate real lock only during the observer
probe: the original callback records False and the original post-lock assertion
must reject it. This discriminates replay masking, probe removal and False
removal. Complete raw streams/state/API tuples/trace/origins are retained and
validated independently of collector side effects. Source and Go handlers are
not patched.

The physical-probe event matrix covers both `SessionStart` and
`UserPromptSubmit`, using the existing `case.event` helpers for the prompt input.
Each raw frontend row retains its actual input and input digest; the input event
is checked against the parsed and exit trace events. For each event, Hermes and
OpenCode ambient each run one positive pair and one real-contention pair. The
five-method leaf therefore runs nine pairs and eighteen frontends:

| Event and case | Pairs | Reports / physical probes | Results |
| --- | --- | --- | --- |
| `SessionStart`, both kinds, positive | 2 | 4 / 4 | 4 True |
| `SessionStart`, both kinds, contention | 2 | 4 / 4 | 4 False |
| `UserPromptSubmit`, both kinds, positive | 2 | 4 / 4 | 4 True |
| `UserPromptSubmit`, both kinds, contention | 2 | 4 / 4 | 4 False |
| Separate malformed Hermes identity | 1 | 0 / 0 | Plain-list independence only |

The sixteen reports require sixteen physical probes, eight True and eight False,
with zero collector or future-copy probes. Every contention pair must also reach
and fail its original owning collector's post-lock assertion. The separate empty
snapshot checks plain-list type and independence only: it cannot replay a report
callback, and its zero physical count follows from the disabled target filter.

Ambient measurement uses explicit `recall_mode=ambient` and
`semantic_fallback=False`. Hermes measurement enables tracing and uses the
ambient Source launcher wrapper; this differs from the uninstrumented Hermes
suite. These counts do not cover default or absent ambient mode, other events,
other producers or native writers, all inputs, or other harness configurations.

Run from a clean committed head with a fresh receipt:

```sh
mise run host:exec -- --timeout 10m --weight 1 -- \
  make test-isolated RECEIPT=/absolute/evidence/observer-lock-probes.json \
  ISOLATED_ARGS='--timeout 600' \
  CMD='env CC=gcc TMPDIR=/tmp python3 -B scripts/test_f02_observer_lock_probes.py'
```

The shared host executor preserves the agent's host environment and bounds the
existing documented campaign runner; the application fixtures remain disposable.

Keep the historical cap artifacts unchanged. After this repair, the same private
13-second cap experiment can show genuine Source probe1/Go0 at its first lock
comparison, replacing neither the historical probe2/Go0 evidence nor its meaning:
the old whole four-entry test already rejected that measured mutant. The six-entry
case also distinguishes raw expand counts. This task makes no claim about every
cap value, older timing-normalizer backlogs, production locks, native writers or
campaign acceptance. No tracked Go or Source changes are part of this cut.
