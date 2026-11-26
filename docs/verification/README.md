# Dated records that remain

The repository no longer keeps records of past runs, reviews and deployments;
see [decision 0023](../decisions/0023-repository-holds-current-contracts.md).
The files here stay for one of three reasons:

- Tracked code names the file: the CAIRN-301 release checks under `scripts/`
  list their record.
- The file is release evidence for the open hybrid runtime campaign (the
  `r0N-`, `f0N-` and November 2026 records).
- An open plan under [plans](../plans/README.md) cites it.

Each file describes what one run established when it was written. It is not a
live status page. Paths under `/tmp` and digests of logs identify artifacts on
the original host; they do not make those bytes available.

Remove these files when the work that cites them closes. Earlier records are
in Git history: `git show fe5743f8:docs/verification/<name>`.

## Surviving CAIRN-301 records

| Record | Basis |
| --- | --- |
| [J-01 inbox journal characterization](2026-11-06-j01-inbox-characterization.md) | CAIRN-301 record |
| [New selected-layer Agy dispatch duplicates, 2026-11-13](2026-11-13-agy-new-dispatch-duplicates.md) | CAIRN-301 record |
| [Retained Python /2 hook selection, 2026-11-13](2026-11-13-r04-retained2-hook-producer.md) | CAIRN-301 record |
| [Event admission deployment — 2026-11-06](event-admission-deployment-2026-11-06.md) | CAIRN-301 record |
| [Checked Claude reserved-wake state parity](f02-claude-reserved-wake-state.md) | CAIRN-301 record |
| [R-01 characterization — 2026-11-10](hybrid-runtime-r01-characterization-2026-11-10.md) | CAIRN-301 record |
| [Hybrid runtime supported operations — 2026-11-06](hybrid-runtime-supported-operations-2026-11-06.md) | CAIRN-301 record |
| [R03 Hermes continuity on the normal host](r03-hermes-continuity.md) | CAIRN-301 record (SHA-256 pinned by `scripts/check_r03_hermes_continuity.py`) |
| [R05-062 journal comparison](r05-journal-core-062.md) | CAIRN-301 record |
| [Agy checked publisher concurrency evidence](r06-agy-concurrency.md) | CAIRN-301 record |
| [Agy current observations](r06-agy-current-observation.md) | CAIRN-301 record |
| [R06 owned Agy Go /3 unit driver](r06-agy-native-unit.md) | CAIRN-301 record (SHA-256 pinned by `scripts/check_r06_agy_native_unit.py`) |
| [Unbound census association and the next Agy blocker](r06-agy-readback.md) | CAIRN-301 record |
| [Complete owned unit: native selection readback gap](r06-complete-owned-unit-gap.md) | CAIRN-301 record |
| [R-06 documentation source audit — 2026-11-07](r06-documentation-audit-2026-11-07.md) | CAIRN-301 record |
| [Retained Python /2 cold restoration](r06-lifecycle-offline-restore.md) | CAIRN-301 record |
| [Native session identity in the watch readers](r06-native-state-identity.md) | CAIRN-301 record |
| [Complete owned native unit driver](r06-native-unit.md) | CAIRN-301 record |
| [R06 release-candidate correspondence draft](r06-release-candidate.md) | CAIRN-301 record |
| [Retained issued effects fixture](r06-retained-effects.md) | CAIRN-301 record |
| [Disposable R06 status consumer fixture](r06-status-consumer.md) | CAIRN-301 record |
| [Finite watcher-unit census preflight](r06-unit-census.md) | CAIRN-301 record |
| [Private R06 unit control process](r06-unit-control.md) | CAIRN-301 record (SHA-256 pinned by `scripts/check_r06_unit_control.py`) |
| [R07 bootstrap exchange and retained reverse](r07-bootstrap-reverse.md) | CAIRN-301 record (SHA-256 pinned by `scripts/check_r07_bootstrap_reverse.py`) |
| [R07 execution packet draft](r07-execution-packet-draft.md) | CAIRN-301 record |
| [Disposable recovery rehearsal](r07-recovery-rehearsal.md) | CAIRN-301 record |
| [Selected-unit stopped observations](r07-selected-unit-fence.md) | CAIRN-301 record |
