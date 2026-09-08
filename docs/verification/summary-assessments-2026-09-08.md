# Summary assessment correction — 2026-09-08

`cairn report` counted a task as unknown after an independent assessment had
rejected it. The record-specific use report already handled assessment versions;
the summary still read only the process-derived task outcome.

The summary now uses the latest assessment for each retained process outcome,
falling back to the process-derived value when no assessment exists. Its process
denominator and other counters are unchanged. This is a read-only query change
with no schema or authority changes.

The actual repository-only recurrence run M reproduced the discrepancy: one
process outcome, two retained assessments, latest assessment rejected, summary
unknown count one. The corrected binary reports zero unknown outcomes for that
same store, with the process count and assessment history unchanged. This run
had no memory exposures, so a record-specific use join could not demonstrate the
summary correction.

A disposable PostgreSQL test covers rejection, acceptance and a later correction
back to unknown without multiplying outcome rows. It also retains the process
fallback for a known launch failure and excludes a compile-only receipt. The
test failed before the repair and passed afterward. The full PostgreSQL race
and CLI integration suite plus static checks passed.

[Verification metadata](summary-assessments-2026-09-08.json) retains the before
and after counters, assessment versions, binary and source identities, and test
log digests. This verifies summary consistency; it does not establish memory
benefit or change any task assessment.
