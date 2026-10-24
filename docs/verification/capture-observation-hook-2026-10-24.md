# Capture observation hook probe (2026-10-24)

CAIRN-215's store and unit tests already covered the observation schema and
the hook's local outcome mapping. `scripts/check_capture_observation.py` now
exercises the actual PreCompact hook, CLI, authenticated API, and PostgreSQL
store in the disposable `make test-integration` cluster. The hook uses a local
selector executable that returns synthetic provider-shaped usage. It cannot
contact a provider. A temporary CLI shim forwards ordinary calls to the real
binary and injects only the named create/report refusals. No installed binding
is changed.

The probe checks a confirmed save with reported model, input/output/cache
tokens and cost; nothing selected; unchanged and courtesy skips; timeout and
reported provider error; a refused note write; and an observation refusal
after a confirmed write. A refused observation leaves no history row, while
the selected note and capture marker remain committed. Repeating that snapshot
reports `unchanged` without another note write. Selector or dialogue sentinel
text is absent from observation rows, selected note bodies, and state files.
Null metrics and absent rows are treated as unknown.

Local verification used a fresh disposable PostgreSQL 17 cluster, migrated it,
built the current Cairn binary, and ran the probe successfully. The eight
existing capture observation unit tests, `make check`, and `bash -n` for the
integration runner also passed. The full `make test-integration` suite was
not rerun for this test-only addition.

This is a transport and persistence check using synthetic usage. It does not
establish provider billing, subscription result coverage, task usefulness, or
token savings. Those CAIRN-215 acceptance gates remain open.
