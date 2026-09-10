# Applicability mismatch precedence

An instruction pinned to revision A and task class `build` previously blocked a
`review` request that omitted the revision. `applicabilityReason` returned
`CONTEXT_MISSING` at the first field and never inspected the known task mismatch.
This caused false `POLICY_UNENFORCEABLE` refusals for both shareable instructions
and private instructions outside a hosted task's applicability.

The repaired check remembers missing pins while looking for any mismatch. Pins
form a conjunction: one known mismatch rules out applicability. When no pin
mismatches, missing context still prevents mandatory delivery. Validity exclusions
retain precedence. No pin, schema, ranking, destination or authority interface was
added. See the [contract](../currentness-and-replay.md).

Pre-fix PostgreSQL tests reproduced three private-policy combinations and the
shareable-policy failure. The repaired body and index tests verify:

- An inapplicable instruction does not block an unrelated task.
- A matching task with missing revision, or wholly unknown context, still refuses.
- Shareable omissions report `CURRENTNESS_MISMATCH` instead of `CONTEXT_MISSING`.
- Private bodies, record IDs, counts and candidate explanations remain excluded.
- Existing validity, matching policy and historical recompilation checks pass.

A separate disposable database compared the previously installed binary with the
repair. The old CLI refused the review and the new CLI allowed it; both enforce
unknown applicability. Body and index receipts created by the old binary
recompiled as identical packages under the new one, retaining the old omission
reason and seal. A fresh compile with the old request ID returns `STALE_PACKAGE`
when the corrected reason would change its package; a new request records the
mismatch and recompiles identically. This required no history rewrite or migration.

Full disposable PostgreSQL/race integration, all Go tests, 34 Python tests and
vet/format passed. The saved assessment-history procedure was read during the
initial use/outcome investigation; current source review found this separate bug.
No task assessment, model trial or memory-contribution claim was added. The repair
makes a required retrieval behavior correct; it does not establish cumulative
memory value.

[Metadata](applicability-precedence-2026-09-09.json) retains baseline failures,
compatibility results, source hashes and the validated design receipt. Initial
doctrine guidance supported causal repair and failing-test feedback. Reassembly
also activated unrelated ingestion, UI, presentation, configuration, ranking,
interface and monitoring obligations; thirty are explicitly nonmaterial to this
repair, with detailed classifications retained alongside the receipt.

## Local installation

Clean `63f63ed4f1c08abc2e05b68e9ff4ea2ea1f69e9d` is installed as CLI and API,
with SHA-256 `210c90cdac5515fa71a668470683c6488e7d4ddc0c780139e04b55d3425c1ca5`. The API is PID
191214; PostgreSQL remains PID 163669. A catalog-backed backup and
previous executables were retained before the update. No schema migration was
needed. Recorded harness and semantic-worker configuration hashes were preserved.
A hosted semantic query returned ready discovery and an exact prior procedure
body. The selected applicability lesson was captured with an identical retry,
then retrieved and checked against its body digest.

[Exact-source CI](https://github.com/halbritt/cairn/actions/runs/34419842234) passed
PostgreSQL/race, Python, vet/build, history and authenticated startup checks.
The new lesson and runtime verification add continuity and deployment evidence;
no host-run outcome or memory-benefit assessment was created.
