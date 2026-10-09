# Selected native evidence reader (`scripts/selected_native`)

Status: source only, not runtime admission. The package selects delivered Cairn evidence and public action metadata from a
native Claude stream-json stream, keeps terminal and process facts outside the success-only grade path, and stores selected
evidence under an explicit intent. It has no provider entry, no socket or process code, no credential access and no import
of the harness scripts; it is a dependency for a future supervisor bridge, which owns launch, custody, request/export and
admission. Nothing here changes the current harness routing or permissions.

## What is provided

| Module | Role |
|---|---|
| `evidence.py` | Ledger of calls and responses for `cairn_search` and `cairn_pull`: exact offered-pull linkage, spans, balances; `first-fitting-whole/2` is accepted only by a ledger that declares it (`CURRENT_READ_POLICIES`) |
| `frames.py` | Success-only stream reader. Without a profile it is the memory-only reader (search and pull, `dontAsk`, 4 MiB / 1 MiB / 2048 frames). A `Profile` pins init tools, action tools, permission modes, hook policy (`refuse` or `project`), inspection policies, the final-answer contract and stream caps; actions go to a caller-supplied sink (the harness `PublicActions`) |
| `terminal_seam.py` | `TerminalProbe` (tolerant scalar reader of the terminal result) and `classify_attempt` (routing label that keeps every observation; binding is supplied by an external owner) |
| `retention.py` | Selected-retention V2 sink (exclusive intent, `SELECTED.json`, `PREPARED.json`) |

Original numeric budgets are constants of the reader: memory 9500 bytes, final answer 8192 bytes, tool room 32000, at most 2
searches and 4 pulls. Stream caps are reader memory bounds, not budgets; no value here widens a budget.

## Limits that stay true

- Source acceptance of retention does not establish authenticated exit or return. The receipt keeps
  `exit_basis = caller_supplied_not_independently_attested` and `return_receipt = False`; a prepared record is not a task result.
- `Frames.finish(0)` is an assertion made after custody; the process exit, wait status, cleanup and input delivery come only
  from the custody record, never from this package.
- No actual caller exists yet: attempt id, session id and the assertion that a custody record belongs to the attempt must be
  supplied and verified by the external owner. Missing preconditions route to `harness_unknown`.
- The reader checks the pinned init tool set and permission mode; it does not choose them and it does not launch anything. The
  launch flags that would produce them are a requirement on the harness owner.
- Hook delivery is projected through `trial_source_delivery.hook_delivery` supplied by the caller; hook telemetry join and
  timing bounds stay in `trial_native_observation.measure`.
- Provider output, operational memory, raw sessions and generated binaries are not stored here. The fixtures under
  `tests/testdata` are curated, source-backed constructions.
- No provider, no native capture, not runtime admission, no measured usefulness.

## Tests

`python3 -B -m unittest discover -s scripts -p 'test_*.py'` (part of `make test`) runs `scripts/selected_native/tests`. The
tests use constructed events and the curated fixtures only; the harness modules they exercise (`trial_native_observation`,
`trial_source_delivery`, `trial_task_eval`) are imported from `scripts`.

`core-budget.*.json` and `mcp-wire.*.json` were emitted by pure Go tests (`presentSearch`, `toolResult` and the unexported budget
functions of `core`, no database) on `git archive` snapshots of commits 554037b65bb2f42f5cc01be510b76761bd3427c2 (main) and
b5ed072b8e23fcf635a85b6cd181184a9fd7d1cd (installed build); they are identical after UUID normalisation, which a test asserts.
`default-memory-only-evidence.golden.json` was produced by the accepted pilot reader on a constructed stream and protects the
default behaviour of this package.

## Provenance (reviewed originals and mechanical changes)

Reviewed originals are the V4 candidate files pinned by the independent source review of 2026-10-09 and the accepted
selected-retention V2 sources. The port changes imports and path anchors only; test removals are listed. Unported coverage:
tests of the pilot launcher `observe.py` (5), the optional native-content extension (2), and tests that reproduce defects of
superseded reader versions or compare with the accepted pilot reader file (6; the latter is replaced by the golden test).

| Repository file (under `scripts/selected_native/`) | Original sha256 | Repository sha256 | Mechanical change |
|---|---|---|---|
| `evidence.py` | `7f96bc5ee841…` | `7f96bc5ee841…` | byte-identical |
| `frames.py` | `ca67d2f90957…` | `68973016ba43…` | from .evidence import ×1 |
| `terminal_seam.py` | `859d3be2a09b…` | `859d3be2a09b…` | byte-identical |
| `retention.py` | `b3b99c7f0d6a…` | `38bdfc87770c…` | from .frames import ×1 |
| `tests/test_selected_evidence.py` | `6576b638f3fe…` | `b7b41874b2c8…` | from ..evidence import ×1 |
| `tests/test_selected_frames.py` | `775ee4648ff6…` | `f32e630f7db6…` | from ..evidence import ×1; from ..frames import ×1; from .test_selected_evidence import ×1 |
| `tests/test_selected_remaining.py` | `dacaac7d7f0f…` | `f1333de12976…` | from .. import frames ×1; from ..evidence import ×1; from ..frames import ×1; from .test_selected_evidence import ×1; from .test_selected_frames import ×1 |
| `tests/test_selected_retention.py` | `ad961e60ce03…` | `0b42356629ef…` | from ..evidence import ×1; from ..frames import ×1; from ..retention import ×1; patch('selected_native.retention. ×10; from .test_selected_evidence import ×1; from .test_selected_frames import ×1 |
| `tests/test_selected_coding.py` | `f4b816aade13…` | `fb0946cc2c1a…` | from .. import evidence ×1; from .. import frames ×1; from .. import terminal_seam as seam ×1; from ..evidence import ×1; from ..frames import ×1; import trial_task_eval as tte ×1; from .test_selected_evidence import ×1; sys.path.insert(0, str(Path(__file__).resolve().parents[2])) ×1; WORKSPACE = str(Path(__file__).resolve().parents[3]) ×1; import copy from pathlib import Path ×1; removed: TransactionalSink.test_defect_v1_leaks_a_valid_action_from_a_rejected_frame, load, frames_v1 |
| `tests/test_selected_current_policy.py` | `655406d2ef26…` | `71e7d7777a60…` | from .. import evidence ×1; from .. import frames ×1; from ..evidence import ×1; from ..frames import ×1; from . import test_selected_coding as t ×1; FIX = str(Path(__file__).resolve().parent / 'testdata') + '/' ×1; import copy from pathlib import Path ×1; json.loads(Path(FIX + 'core-budget.main-554037b.json').read_text()) ×1; json.loads(Path(FIX + 'mcp-wire.main-554037b.json').read_text()) ×1; Path(FIX + stem + '.main-554037b.json').read_text() ×1; Path(FIX + stem + '.b5ed072.json').read_text() ×1 |
| `tests/test_selected_terminal_v4.py` | `19eff7e27d6b…` | `2a42c0b1c193…` | from .. import frames ×1; from .. import terminal_seam as seam ×1; from ..frames import ×1; removed import terminal_seam_v2 as seam_v2 ×1; from . import test_selected_coding as t ×1; sys.path.insert(0, str(Path(__file__).resolve().parents[2])) ×1; import copy from pathlib import Path ×1; removed: ClassifierKeepsTerminalFacts.test_defect_v2_classifier_routes_four_defective_terminal_reports_to_grading, ProbeBoundaries.test_defect_v2_accepts_an_over_long_line_whose_newline_arrives_in_the_last_chunk, TerminalDenials.test_defect_v3_leaves_a_denied_action_as_tool_success |
| `tests/test_selected_retention_seam.py` | `1ee0d01731c0…` | `dc9f353329ca…` | from .. import terminal_seam as seam ×1; from ..frames import ×1; from ..retention import ×1; from . import test_selected_coding as t ×1; sys.path.insert(0, str(Path(__file__).resolve().parents[2])) ×1; import json from pathlib import Path as _Path ×1 |
| `tests/test_selected_composition.py` | `105394a18180…` | `f77b50788606…` | from .. import evidence ×1; from .. import frames ×1; from ..evidence import ×1; from ..frames import ×1; from .test_selected_evidence import ×2; sys.path.insert(0, str(Path(__file__).resolve().parents[2])) ×1; WORKSPACE = str(Path(__file__).resolve().parents[3]) ×1; import hashlib from pathlib import Path ×1; removed: DefaultStaysMemoryOnly.test_guard_accepted_reader_rejects_the_same_coding_stream, DefaultStaysMemoryOnly.test_guard_memory_evidence_is_identical_across_accepted_reader_default_and_profile, spec, frames_orig, spec.loader.exec_module(frames_orig) |
| `tests/test_selected_pilot_observed.py` | `1a2196085256…` | `af904895d0c6…` | from .. import evidence ×1; from .. import frames ×1; from ..evidence import ×1; from ..frames import ×1; from .test_selected_evidence import ×1; removed import observe ×1; removed: ObserverControls, ToolResultShapes.test_p3_native_text_size_shape_and_hash_are_retained_without_text, ToolResultShapes.test_p3_error_keeps_only_closed_code_and_length |
| `tests/testdata/core-budget.main-554037b.json` | `450c406cc569…` | `450c406cc569…` | byte-identical |
| `tests/testdata/core-budget.b5ed072.json` | `450c406cc569…` | `450c406cc569…` | byte-identical |
| `tests/testdata/mcp-wire.main-554037b.json` | `f0f1cf174b40…` | `f0f1cf174b40…` | byte-identical |
| `tests/testdata/mcp-wire.b5ed072.json` | `28c3bd34eecd…` | `28c3bd34eecd…` | byte-identical |

Substituted import forms: `from evidence import` / `from frames import` / `from retention import` become package-relative
imports in sources and `..`-relative imports in tests; `import evidence|frames|terminal_seam` become `from .. import`;
`from test_evidence|test_frames import` become `.test_selected_evidence|.test_selected_frames`; `patch('retention.` becomes
`patch('selected_native.retention.`; absolute host paths become paths derived from `__file__`. The complete list with counts is
kept with the port report.
