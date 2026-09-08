# Known pre-launch failure — 2026-09-08

When the runner claimed a receipt and context preparation then failed, it returned
the error with process state `unknown` and no outcome. It already knew that no
process had started, but retained inspection could not distinguish that failure
from an unfinished run.

The runner now records `launch_failed` before returning a preparation, render or
launch-intent persistence error. The existing outcome contract derives
`not_attempted`, with no exit code, zero process duration and empty stream digests.
The original error remains inspectable. The same receipt still cannot launch
twice. No new migration, observation authority or API field is introduced.

The failure path attempts its database observation with a separate five-second
context so caller cancellation does not discard known non-execution. It performs
no filesystem write: context preparation may have refused an existing or unsafe
path. If the database observation also fails, both errors remain explicit;
there is no claimed local retry file on that refused path. Abrupt process death
before observation can still leave an unfinished run.

`TestPreparationFailureRetainsUnattemptedOutcome` first failed against the old
runner for two actual filesystem conditions: an artifact parent that is a file,
and a pre-existing run-directory symlink. It now verifies the retained outcome
and use-report semantics, original filesystem error, absent process marker,
untouched symlink target, and duplicate-launch refusal. The full disposable
PostgreSQL suite with the race detector and `make check` pass. Existing successful
launch, timeout, process-group cleanup and database-outage recovery tests remain.

Local red/green logs are `/tmp/cairn-prelaunch-red.log` and
`/tmp/cairn-prelaunch-green.log`. This is a repair to known failure reporting,
not a claim of complete crash recovery or memory usefulness.
