# Work coordination acceptance

Locally verified on 2026-11-05 (Pacific time). This records the delivered
five-feature slice and its tested claims; deployment and measured usefulness
remain separate.

The runtime checks selected `3619c5bba930eb987cd81e05ca16609db655f6ac`, with
Cairn executable SHA-256
`52723207d6a94c52d7f847c32647d9769e6ea3680c1cf507848e34627768d93a`.
It combines the work view, artifact relationships, durable waits, next-turn
notices and advisory proposals. Validation used disposable stores and pinned
executables. Source and executable identities remained unchanged during the
selected checks; successful disposable runtimes were removed.

## Completed checks

| Check | Observed result |
| --- | --- |
| Actual schema 78→79 upgrade | Real project, progress and artifact-link state created with the landed schema 78 executable survived migration. All migration digests 1–78 and selected retained data stayed unchanged. A second migration was idempotent, advice remained disabled by default, and the old executable refused schema 79. |
| Lower-support compatibility | A separate build omitted only the two advice capability-map entries, retaining migrations and readers. Fresh CLI and the same authenticated HTTP process refused advice that was active or still required after disable. Settlement restored reads; lower-build Undo and exact retry succeeded. A separate running job kept compatibility fenced until its real 10-second timeout and confirmed process-family cleanup. Retirement, retained reads and the old-schema refusal also passed. |
| Combined physical restore | One actual database backup contained three durable-wait states, a genuine signed native notice hold, a queued notice, applied and pending advice, and a running advice subprocess. Post-backup source withdrawal was preserved through external recovery witness reapplication, rebuild, verification and resume. Restored copies were removed from the selected ordinary/advice/history views. Old wait/notice admission and advice application were fenced; exact Store reconciliation terminated the retained advice process family without fixture signals or an automatic provider rerun. A fresh native turn in the same conversation, new-generation waits, and fresh advice generation/application passed. |
| Registered native advice smoke | The real registered native package and existing subscription completed a synthetic shareable request using `gpt-6-astra`. Two generate commands with the same request UUID retained one identical completed job, process identity and proposal. One action was proposed and no annotation or artifact link was applied. The recorded process group was absent afterward. |

The physical restore used four invocations of a registered synthetic advice
executable and eight requests to a local scripted native Responses endpoint.
It exercised the complete native package with its default code-mode companion.
The separate native advice smoke used the subscription route. Its one-request
claim rests on the native guard and retained process identity; provider traffic
was not independently counted.

The first four features were previously accepted and landed at
`42de49de8345754a7e9da72018c1ee661ff0b9e4`. Their retained native owner-input
and physical wait/notice restore evidence remains part of this release.
The final full-suite candidate includes all five features.

## Evidence identity

Repository fixtures are `scripts/check_work_advice_upgrade.py`,
`scripts/check_work_advice_compatibility.py` and
`scripts/check_work_advice_restore.py`. Retained reports are private validation
artifacts; their SHA-256 digests identify the selected evidence without
publishing raw output.

| Retained report | SHA-256 |
| --- | --- |
| Schema upgrade | `f6c708b5123158feba74d87b82b6f441845c7dfcd1ec9dc5b9ea37cca3fda513` |
| Lower-support compatibility | `cd8f75a1b5abde850d5166fadfb7e3bc38fe1be5e601cada9da526be4c9d80a9` |
| Combined physical restore | `7ef05d722cc6b84dbcaeb510f59800bd4da5393156dacc4639ab34a3a5ef415c` |
| Native advice smoke | `2d5236e5e00f3237605e8f2e18fdc37962b1570d97d6b75782f563e17cd1f8ad` |

## Full-suite validation and limits

The exact clean candidate `b5233c0d721285a50edc7637ca01dbfec4f845f1` passed:

| Command | Result | Elapsed |
| --- | --- | --- |
| `make test-integration` | Passed: PostgreSQL race suites and executable integration fixtures | 1,454.800 s |
| `make check` | Passed | 21.789 s |
| `make test-lifecycle` | Passed: local start, backup, restore, stop and restart | 83.636 s |
| `python3 -B scripts/verify_docs.py` | All four tiers passed | 0.542 s |

All source fingerprints remained unchanged. The integration and lifecycle
clusters were stopped and removed. The full validation receipt SHA-256 is
`eb82a6d2911385f5d29f17ae148f7ec5eab556213d6356b21eff832ecd0007ca`.

The browser matrix passed all nine scenarios. Seven affected scenarios were
rerun after screenshot-position corrections in fixture-only commit `2abe7b6`.
All 56 retained images were inspected; 36 form the selected final visual set.
No material visual blocker was found. The selected 18 UIPass cases had no
blocking or suppressed findings. They retained 1,124 incomplete contrast
checks for partially obscured elements in the scrolling modal; this is not
a general accessibility certification.

The suite candidate differs from the runtime candidate only in screenshot
positioning and one test database-isolation fix. Runtime, migrations and native
packages are unchanged. The final browser review receipt SHA-256 is
`9f056ce88f4d058377b0cd389641951b08406a3449ea3c6327405a07f6cbcd19`.

The restore case deliberately stopped its old advice controller before restoring;
it does not establish a late-completion race. Lower-support results cover a
build that retains compatible readers, not a down migration or an unsupported
old binary. The subscription smoke used synthetic input and measures neither
proposal usefulness nor long-term outcomes. These checks establish no production
installation, production recovery exercise or 30-day usefulness result.
