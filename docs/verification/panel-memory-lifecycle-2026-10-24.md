# Frozen panel memory lifecycle probe, 2026-10-24

The disposable [probe](../../scripts/check_panel_memory_lifecycle.py) exercises
three CAIRN-227 fixture cases through the built CLI, authenticated local API,
and PostgreSQL Store. The integration runner creates a fresh database and a
private hosted-destination agent profile. It reads exact sources from the
frozen fixture and records its SHA-256.

| Case | Checked Store and API boundary | Acceptance still missing |
| --- | --- | --- |
| `wrong_project_memory` | Both whole A sources are retrievable. The Cairn Atlas 30-day source ranks before the Trail Atlas 90-day source for the Cairn query. | The fixture's `project` value is descriptive metadata and source wording, not an authenticated project scope. An actor must choose the correct project and explain the other source; this probe does not grade that choice. |
| `corrected_note` | An authenticated edit preserves record identity, advances it from version 1 to 2, invalidates the old pull handle with `STALE_HANDLE`, indexes and whole-pulls the current v2 source, and retains exact v1 text through history. | No model answered which endpoint applies, and no paired cost or accepted task outcome was measured. |
| `meaningful_owner_correction` | The same currentness and historical-source checks hold for the frozen old/new preference wording. | These are self-claimed A records. They are not an authenticated owner instruction or a live correction response. |

The three cases contain no mandatory C source, so this probe makes no claim
about C currentness or authority. It does not call a provider, submit a native
actor task, or establish the four-arm gate's task acceptance. The CAIRN-227
release decision remains **defer** pending paired task results and effective
deployment evidence.

A focused fresh disposable PostgreSQL/API run passed all three cases.
`make check`, Python syntax parsing, `bash -n scripts/test-postgres.sh`,
and `git diff --check` passed. An optional full `make test-integration` run
was stopped after two package outputs to avoid competing with concurrent
agents; it is not a completed integration-suite result.
