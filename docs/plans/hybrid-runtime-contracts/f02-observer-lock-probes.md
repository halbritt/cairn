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

Run from a clean committed head with a fresh receipt:

```sh
make test-isolated RECEIPT=/absolute/evidence/observer-lock-probes.json \
  ISOLATED_ARGS='--timeout 600' \
  CMD='env CC=gcc TMPDIR=/tmp python3 -B scripts/test_f02_observer_lock_probes.py'
```

Keep the historical cap artifacts unchanged. After this repair, the same private
13-second cap experiment can show genuine Source probe1/Go0 at its first lock
comparison, replacing neither the historical probe2/Go0 evidence nor its meaning:
the old whole four-entry test already rejected that measured mutant. The six-entry
case also distinguishes raw expand counts. This task makes no claim about every
cap value, older timing-normalizer backlogs, production locks, native writers or
campaign acceptance. No tracked Go or Source changes are part of this cut.
