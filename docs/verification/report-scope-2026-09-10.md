# Task and run report selection — 2026-09-10

`use-report` and `run-report` now select exact task/run labels before pagination.
A reviewer can follow one task's executions and memory exposures through the
existing reports. This supplies no benefit score or new outcome classification.
[Usage](../use-outcome-loop.md#follow-one-task-across-runs).

Both new PostgreSQL tests failed against the previous behavior by returning an
unrelated first row. The implementation adds optional parameterized predicates
to the existing queries and CLI flags; the API uses the same request structures.
No schema migration or report-access change is required.

`make test-integration` and `make check` passed. The checks cover:

- Task-only, run-only and combined filters, applied before page offsets; literal
  punctuation/Unicode labels, existing record/policy filters and empty matches.
- Linked retrievals retaining their own exposure and usage while joining the
  host's corrected assessment; one execution remains one run-report row.
- Zero-memory executions, exclusion of pure retrievals from run reports and
  unknown task outcomes after successful process exits.
- Authenticated JSON filtering, foreign-repository refusal and hosted denial.
- The CLI selecting one actual `/bin/true` execution and its two note exposures
  among another execution and 102 unrelated exposure rows. The `runs` alias also
  accepts the new flags.

The first API fixture omitted the observed exit code required for an exited
process. It was corrected before the full passing suite. That failure was in
test setup, not production behavior.

The [metadata](report-scope-2026-09-10.json) pins the evidence and doctrine receipt.
These checks establish report behavior. They do not measure review-time savings,
memory's incremental contribution or improved model-task outcomes. Existing
qualitative assessment narratives remain the place to explain those judgments.

The authenticated endpoint needs an updated API to accept the new fields.
Existing requests without filters retain their behavior.


## Fixture isolation correction

The initial CLI check used a disposable database but inherited the default Cairn
artifact home. Its two context directories were identified by synthetic note
content, receipt IDs and timestamps, hash-checked, and moved intact under the
private verification directory. No operational database was used for testing.

The fixture now sets a temporary `CAIRN_HOME` for its child runs and checks the
returned artifact paths against that directory. A fresh disposable CLI check
passed and left its sentinel artifact home untouched. The first standalone
check omitted database migration; the corrected check migrates before capture.
This follow-up changes test isolation, not report behavior.


## Installation

Clean CLI/API `b171a8b78b9b0a889a7718974cb0b4e60b71bfba` are installed after
[CI 34544439753](https://github.com/halbritt/cairn/actions/runs/34544439753)
passed both jobs and all steps. A store backup and previous executable copies
were retained before the API restart. PostgreSQL, migration 034, configuration
and native adapter files stayed unchanged; all 81 retained note versions match
their pre-installation inventory.

The installed executable passed the isolated CLI history and task/run selection
check. The running API reports the same clean build and refuses both protected
reports through the existing hosted profile. Positive authenticated filtering
was verified by the disposable Unix API suite. These checks establish the
installed capability and access boundary, without a new model-task claim.
