# Native recurrence source and oracle calibration

The new [native recurrence specification](../../trials/native-recurrence/README.md)
has a usable, frozen source workspace and a calibrated repair evaluator. It
does not yet have a model executor connected to the native Driver path. No
model requests were made and no memory benefit was measured in this checkpoint.

The scenario reuses the genuine explicit-agent-connection defect at
`45e37cf9d55c0b85c93e1b5532780a05cb03b4d1`, with the unchanged hidden oracle from
the earlier agent-env experiment. Its selected lesson concerns this same repair;
this is recurrence, not unfamiliar-task transfer. Only the lesson identity,
version and hash are committed. The body remains in private trial inputs.

## Observed checks

- The complete frozen repository contains 269 files. Its `make check` and
  `make test` pass inside the existing filesystem sandbox, including 14 Python
  tests. No live API socket, credentials or test database is mounted.
- The baseline fails exactly four oracle cases: `explicit_without_home`,
  `empty_token`, `empty_socket` and `invalid_flag_without_home`. Its other seven
  cases pass.
- The reviewed reference from `f4c5fe3bf1e59ec3dc83d723de7c18bb3a694ade` passes
  all eleven cases, formatting and existing non-database Go tests. The oracle
  checks real Unix HTTP transport, distinct authentication tokens, an
  unpredictable response marker and preservation of environment variables.
- Current Cairn `make check` and `make test` pass, including 21 Python tests.
  Four new calibration tests reject missing or skipped checks, a passing
  baseline, a failed reference and reruns that overwrite an earlier failure.

An initial partial archive failed `make test`: excluding older trial modules
also removed all Python tests. The source was changed to the complete frozen
repository before any model invocation. Its older, different-task experiments
are available equally to all proposed conditions. This repair's controller,
oracle and reference postdate that revision and remain outside the model source
tree. The parent preflight directory contains evaluator inputs and must never
be mounted into a model workspace.

Database-dependent Go tests skip without a test DSN (193 skips in each oracle
evaluation). These checks establish the connection-repair oracle's calibration;
they do not establish database integration, native model execution, Driver
admission of a model-produced repair or task benefit. The original experiment
and its inconclusive results remain unchanged.

Metadata and hashes are in the [verification record](native-recurrence-preflight-2026-09-08.json).
Private source manifests, evaluator trees and logs are under
`/tmp/cairn-native-recurrence-preflight-b`; the first failed preflight is retained
under `/tmp/cairn-native-recurrence-preflight-a`. Current repository check logs
are `/tmp/cairn-native-recurrence-check.log` and
`/tmp/cairn-native-recurrence-tests.log`.

Doctrine packet `pkt-9e4852698407d84f`, content SHA-256
`9e4852698407d84f9f9aec87722190f2ccd67c72d1598097820016d71572f0f3`,
uses corpus `corpus-2026-07-12-a11702cc9217` and doctrine
`doctrine-f6bbb5196a3f8bf9`. Typed observations, the scoped decision receipt,
seventeen retained nonmaterial obligations and citation closure are under
`/tmp/cairn-native-preflight-*`. The conclusion is limited to calibrated inputs;
native execution remains a material prerequisite to running the comparison.
