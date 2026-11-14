# R06 release-candidate correspondence draft

**DRAFT — NOT A QUALIFIED RELEASE CANDIDATE.** Evidence cut: 2026-11-14,
after the `8631c6f12b8b484d608d959683ed5a6767a2db66` landing, tree
`771aa3be71d205ab3db7f3b330f2e7c9183328c2`. This document indexes selected
criteria, source and retained evidence for R03/CAIRN-305, R04/CAIRN-306,
R06/CAIRN-308 and HC05/CAIRN-312. It closes none of them. It neither admits
native work nor starts R07 or an observation clock.

The controlling definitions are the [release contract](../plans/hybrid-runtime-contracts/release.md#independent-fixture-and-evidence-matrix)
and [dependency table](../plans/hybrid-runtime-contracts/release.md#work-items-and-release-gates).
The execution counterpart is the [R07 packet draft](r07-execution-packet-draft.md).
Standing owner grant `a3065785-ae68-4f8e-9148-57ba1468a768/v1` removes the
interactive human wait, while retaining technical gates. Supervisor directive
`40dd8464-f4d9-440e-9937-cb40010a89fb/v1` commissions the missing engineering
work; it does not establish its results. Current acceptance review goes through
agent-1415; the earlier references to agent-1416/agent-1381 as backups are
superseded by the Release routing instructions. Release owns integration and
the eventual final join.

## Evidence vocabulary and candidate identity

Use **qualified, finite scope** only for the particular independently accepted
case and tuple. **Failed** retains the actual failure. **Unsupported** is an
explicit source boundary. **Unobserved** means a supported observation was not
obtained; **unknown** means the fact or correspondence is unresolved.
**Not run** does not mean passed. A field absent from the selected evidence
remains unknown; do not fill it with a success Boolean.

| Tuple element | Selected identity or evidence | Disposition and missing final proof |
| --- | --- | --- |
| Landed preparation source | `8631c6f12b8b484d608d959683ed5a6767a2db66`, tree `771aa3be71d205ab3db7f3b330f2e7c9183328c2`; exact tree reviewed as author `33da5cb10e5854d928dcf6f8aa309eeb27ea8ceb`, full patch ID `ede013d04af0e88f0a29f9f2f0852accc031be72`. | Qualified finite engineering preparation; three new Python/test/doc files over `1508`. It is not an admitted Agy driver or installed candidate. |
| Five Go images | Build source `15080de3b1205b161faa66f60b8faf93c30f18da`, tree `11ec0c51352505930e64236a99b929767e71cc2c`; controlled manifest and lineage below. | BUILD-ONLY, five distinct original mode-0500 images. Do not relabel their build head as `8631`. Final common-RC images wait until the last Go change lands. |
| Source, build and runtime custody | Manifest contains 2,922 source SHA256s. Candidate binds image size/mode/device/inode, actual argv/cwd/environment, compiler/native/runtime before/after pins, buildinfo and build IDs. Archive SHA256 `bdaadabb287a3bcece242a1b1f356c83d0bb1b9434d1728eb6725e0dc94b15cd` matches the exact `1508` Git blobs. | Historical build observations, not newly sampled execution. The `8631` author archive has 2,925 blobs; the two populations differ. Binary `-buildvcs=false` stamps do not attest source identity. |
| Compatibility and configuration | Final tuple must name Python `/2` binding, retained argv and complete 17-member bundle; Go `/3` receipt, manifest, exact profiles; L/V/verifier, approved shell/Python trust closure and fixed launchers. | Finite produced A/B null profiles are evidence for their disposable unit. Complete installed configuration, account/project layers, outstanding issued argv and each effective session selection remain to be joined. |
| Host/client/session matrix | Intended hosts: proximal, archon, talos. Agy held 1.5.7 file SHA256 `5df525a2d6908989c57b897723caea66e00d7c4d2e24411f91ad306989574642`. | Frozen file and current ELF pins are not historical loaded-byte, imported-code, native-generation, hook-refresh or installed-adoption proof. No moving installed client substitutes for the frozen tuple. |
| Rust/model/semantic reverse | Retained S04/S04R/S05 evidence and non-production Semantic preparation below. | Match final source, model/config, derivation, launcher, ABI and actual loaded closure. Six changed Rust ABI libraries, seven changed live-Python native files and unknown deleted mapped bytes prevent a disk-pin-to-runtime inference. |
| Checks and acceptance | Reviewer `73afd8cc-e02e-4822-bab3-ab59a536a46b/v1`; landing `92024fb0-cc00-48b6-8033-51e46d32b767/v1`. | Acceptance is finite preparation only. The final combined source/image/host/config/receipt/raw-gate tuple and all predecessor joins are pending. |

The historical image root is
`/home/halbritt/.cache/cairn301-release-1465/r06-main-images-15080de3-20261114/tools/`.
The controlled manifest preserves these paths and their original device/inodes;
it copies no binary and rewrites no build argv.

| Role | Image basename | SHA256 |
| --- | --- | --- |
| API | `api` | `2ac409dc98a167ef4ebb5b622a816f4f8304ef322082c0be4bea37f8f94d5c79` |
| Control | `control` | `d36a79d4602b0ba6aebe835e81f280b036226c2ce015cc90265488c8c49f3396` |
| Stager | `stager` | `cf4365b44b87d8503114c326f7c0fce7b8ac18affaef2c5658ca0e4ed20314cf` |
| CLI race helper | `helper` | `9f807a0242ac6225a4e5253c7d9ddba253c1c15161693dc0b697a3bcd82cabd7` |
| Coordination race observer | `observer` | `ed601bfdb1b04c6534fcf73061412384d233658b624cbeacde13ce38e59e132b` |

## Selected sealed evidence

These are private operator artifact locators, not portable public downloads.
Only selected criteria/status and pins are recorded here; credentials, DSNs,
raw sessions, model output and operational memory are not repository content.

| Evidence | Primary locator | SHA256 |
| --- | --- | --- |
| Controlled five-role manifest | [images-host.json](/home/halbritt/.cache/cairn301-release-1465/r06-main-images-15080de3-controlled-20261114/images-host.json) | `e132531f982044e89546e67242f0ae28d7b0d3db05e8fee4f21c8f428feca068` |
| Current image candidate | [candidate.json](/home/halbritt/.cache/cairn301-release-1465/r06-main-images-15080de3-controlled-20261114/candidate.json) | `4e14e15f0f0aaa8a43cbdaf36bd396355f42dfdfd748584443b5570b0bb60397` |
| Controlled index | [index.json](/home/halbritt/.cache/cairn301-release-1465/r06-main-images-15080de3-controlled-20261114/index.json) | `f169590e02f41ba1b9e6578713a7f030a24d0a1f21432bf2a2c118bc200bbeaa` |
| Controlled-copy lineage | [lineage.json](/home/halbritt/.cache/cairn301-release-1465/r06-main-images-15080de3-controlled-20261114/lineage.json) | `61cedd96993792cdc9d0d6becc3633e33c2a4361752f9b377f2bd3c3b2dc43b5` |
| Final metadata diff, separately pinned | [lineage-final.json](/home/halbritt/.cache/cairn301-release-1465/r06-main-images-15080de3-controlled-20261114/communication/lineage-final.json) | `3a5ebeebde91562fd1bb99856ce661c99cbba739a9fb204877fcba7ab0dbae13` |
| Full independent preparation review index | [review index](/home/halbritt/.cache/cairn301-review-1415/r06-auth-prep-33-20261114/index.json) | `022cec5a17590f1a6bf223f2278c67c489df2d3976d53942973f90cfe3351d72` |
| Root source/raw/custody reconciliation | [review-custody-verification.json](/home/halbritt/.cache/cairn301-release-1481/wake-f1210ea1-20261114/review-custody-verification.json) | `9305fb9a1305038ba4fc1025e3864e94d0499bb4e29fbce6911609fda52f7769` |
| Exact-tree landing | [landing-verification.json](/home/halbritt/.cache/cairn301-release-1481/wake-f1210ea1-20261114/landing-verification.json) | `5dc3ca28db98f4cd8f68b44b7048a27b8c0014fef5aa940df687e4cc87bde792` |
| Finite global12 custody | [native12-custody.json](/home/halbritt/.cache/cairn301-release-1383/native-closures-20261113/wake-5c214bf2/native12-custody.json) | `8b9f7adfe00cbc454d881b7a9cf5aa5d298ecac09b182dbddb380f0179a1a009` |
| Global12 sealed raw population | [native12 evidence index](/home/halbritt/.cache/cairn-r06-native12-1468-20261113/evidence-index.json) | `b2fb7b2950e9fa2d19204f35aed6f31c62a7ba57aa0d6e2a61f0a1703938493c` |
| Finite Hermes3 custody | [native3-custody.json](/home/halbritt/.cache/cairn301-release-1383/native-closures-20261113/wake-619a4bc8/native3-custody.json) | `1b05a870fd3006f094d152aeb6fafc95ec06ba7c4690855e703fc5a4495b7823` |
| Hermes3 actual command/image map | [actual-command-image-map.json](/home/halbritt/.cache/cairn301-hermes-run3-native-1467-20261114/actual-command-image-map.json) | `887bfa932125bd38108ace5a1067c0e8ed4ef9611dd0e2a966093c8fc56066a0` |
| Installed-layout proposal | [inventory report](/home/halbritt/.cache/cairn301-r07-installed-layout-1467-20261114/report.md) | `4db796b61102022514f2a6824c959e02ace52fb889fa094f8f109f94b65d3217` |
| Semantic staged/reverse correspondence | [SUMMARY.md](/home/halbritt/.cache/cairn301-semantic-1382/r07-prep-20261113/SUMMARY.md) | `9c2ec45bbff741f1858fb0f8a9cd3279396236774655630a5e3bdc7400adefc1` |
| Proposed semantic reverse closure | [reverse-closure.json](/home/halbritt/.cache/cairn301-semantic-1382/r07-prep-20261113/reverse-closure.json) | `6bc3a9d8392840d2e314a0ffaf44ef0a65e6a99b16db09c7df26ac65d44da287` |

The original mode-0664 packet remains immutable. The controlled packet has
138 indexed mode-0600 members and preserves five external mode-0500 images.
The obsolete `29e76709` recipe rejection was corrected in the independently
reviewed `8631` preparation source; it is not a current recipe failure. That
correction does not release the unconditional native refusal at this baseline.

## Actual result correspondence and REL-EQUIVALENCE

Each row keeps its executor, population and evidence lane. These results cannot
be summed into a new native, installed or final-candidate pass.

| Observable | Retained-Python / Go correspondence | Selected actual result and remaining limit |
| --- | --- | --- |
| Request replay/refusal and journal state | [R05-062](r05-journal-core-062.md) maps the unchanged Python recovery method to the real Go journal using the same scripted CLI. Request identity, persisted status, refusal order, result and reply matter. | The source specification expects four sends and preserves eight original assertions. It discloses the old checker's insensitivity to exit status and final send count. Select the exact paired fault-results receipt for the final tuple; this draft does not invent one or retire the old assertions. |
| Issued effects and cross-reader state | [Retained effects](r06-retained-effects.md) distinguishes pending, completed, settled and `reply_possible`, actual emitted `/2` callbacks, Go retries and explicit `/2` selection. | The fixture's effect semantics are concrete; bind its exact accepted raw result to the final source/image tuple. A copied journal or fake status is not that result. |
| Disposable Go `/3` forward/reverse | `TargetReceipt` selects independently validated current/target closure; reverse restores exact retained Go `/3` A and the same watcher-lock inode. | Reviewer preparation: six operations, two publications, three preflight refusals and one actual final-D6 target-memory refusal; 12 authenticated reads. Empty session/callback inventories do not establish native D1/D2/D5. This is not Python `/2` reverse. |
| Trace and generation collection | The standalone preparation STUB uses an owned Python process, complete stdin EOF, literal argv and live Before/After generation. | One reviewer standalone STUB exec, 35 input bytes, complete 186,654-byte trace interval, zero exit. It emits no Agy session or hook and establishes no native refresh. Author's three historical STUB runs remain a separate population. |
| Historical complete native12 unit | Frozen source `caae7a032f98069bb9d6c2d6750c8cb2dbb45056`, tree `cd7dc6d1d9920f4c5926b0026357eb74c3f20538`; carried b8 image manifest SHA256 `eb3b550ed34cbe933df4c90da3491b56430ee177f7e79426b26f5a80858a3a8c`, not the current 1508 manifest. | Actual retained Python `/2` issued read/finish-reply, Go `/3` forward, orderly same-history join/resume and retained Python `/2` reverse/readback passed in that fixture. Outer/helper/child exits 0; 131 command exits include one expected `DELIVERY_ACTIVE` exit 4; seven empty polls/readbacks, 33 hook stdio records and 20 state pairs. This historical bound-unit reverse is not the pending unbound Agy/operator-bootstrap reverse at the final tuple. `ExternalCoverage` remains unknown and controller `NativeRefresh` unobserved; no live/full R06/HC05 acceptance. |
| Hermes native continuity and retained `/2` | The [Hermes procedure](r03-hermes-continuity.md#implemented-sequence-requiring-a-new-native-admission) uses the cached-A boundary, orderly finalization/reap, same-history B resume and explicit retained `/2` resume. | Hermes3 result `fb2b0b37-0f94-4401-a5bd-b93047b3dd56/v1`: eight callbacks over `UserPromptSubmit`/`SessionEnd`; history lengths 4/4, 6/6, 8/8; final record/capture/recall counts 5/4/4; attempt/delivery counts 0/0; zero audit violations. Qualified finite R03 fixture only; AC4 and common-unit joins remain open. Historical Go control and checker source identities are distinct. |

Raw reviewer preparation evidence under
`/home/halbritt/.cache/cairn301-review-1415/r06-auth-prep-33-20261114/` includes:

| Raw member | SHA256 | Observed correspondence |
| --- | --- | --- |
| `authenticated/auth-result.json` | `31529504b67cd8fed8540c965c3df4379de5fd46415b34047bc220ba512b050a` | Explicit native/refresh/external/legacy limits plus six-operation counts. |
| `authenticated/positive/A-to-B.exit.json` | `95d759ea8b87772bf3042724fd9ff7a0848728afc64fec1bab5e9b28ada0512e` | Actual public switch argv, exit 0. |
| `authenticated/positive/B-to-retained-A.exit.json` | `47a343b11770cde908efd3a21ee3133a8c003ffbcb1faa049e28246088b47883` | Actual public reverse argv, exit 0. |
| `authenticated/final-D6/final-target-memory.exit.json` | `05b757d7a7e01d9d639a05fc2e4b91e924d5150e93de1dd9b9ce8e162c23f33e` | Actual switch exit 1; paired result reaches inspected=true and Published=false. |
| `gates/focused-74.stderr` | `a0f7a8376f4b74c3bdc94ea8f825ccf28bd676dd5012c0bc22e4b80741582093` | 74 tests, OK; separate portable run has 60. Do not add these as distinct coverage. |

The review index seals the paired stdout/state/archive records. No raw trace,
credential content, private dialogue or producer payload is copied into Git.

Hermes3 checker source is `3637bb31b949db20bd4b67387dec369cba68dbfb`, tree
`c49fa491cec76ad2dd4bf98f7dd3b6f123376a0d`; its actual Go image source is
`7d22b663d6abb0295dda0263adc9033353e5cefa`, tree
`b9589ad0ac27a1557fd25e198143233239b322a5`. The actual command/image map binds
those selectors. Neither that carry nor native12's b8 carry is evidence that
the same result ran against the final common-RC images. Source, executed image,
checker and scope must remain separate columns in the eventual join.

## Supported and unsupported R01 matrix

This is source support plus evidence disposition, not a successful installed
cycle matrix. The [R01 characterization](hybrid-runtime-r01-characterization-2026-11-10.md#source-operations-and-observation-boundary)
and [supported-operation definitions](hybrid-runtime-supported-operations-2026-11-06.md#operation-evidence-and-required-installed-effects)
retain the exact event/refusal contracts.

| Harness | Ordinary memory support | Coordination / explicit unsupported cases | Evidence still required for the final unit |
| --- | --- | --- | --- |
| Claude | Start/prompt recall, tool hints, PreCompact/SessionEnd capture; ordinary Stop capture is unsupported. | Bound recall and channel delivery/idle wake are distinct; public running native cancellation unsupported. | Exact loaded selection, actual supported memory and required-context event matrix, channel/session continuity and supported idle-boundary refresh. |
| Codex | Start/prompt recall, PreCompact and asynchronous Stop capture; ordinary SessionEnd capture/tool hints unsupported. | Exact queue/turn delivery and wake; Interrupt is presence, not request cancellation/cleanup. | Every affected loaded thread's hook/config readback and natural supported cycle; no inference from cwd, parentage or current MCP alone. |
| OpenCode | Actual TS request-copy recall, tool hints, idle/compaction/disposal capture. | Child/title exclusions; cancellation only in the qualified opt-in trial, not general Store cancellation. | Actual TS execution and peer/capability/turn/tool correspondence; directory-wide disposal cannot be reported as a per-session refresh. |
| Agy | Ordinary automatic lifecycle memory **unsupported**; explicit MCP memory is separate. | PreInvocation and fully-idle Stop, top-level wake versus child deferral; public native cancellation unsupported. | Actual native generation/session, one managed layer, A-to-B same-process refresh and native drain; `ExternalCoverage` unknown, `NativeRefresh` unobserved at `8631`. |
| Hermes | Required Python provider/registration, owner-turn recall, completed-turn/pre-compaction capture and disposal. | Local abort does not attest public Store request cancellation. | Final installed provider/registration/host-return and common-unit correspondence beyond the finite native3 case; retain required Python. |

The November 10 historical R01 population contains 141 IDs observed three
times: 408 passing method executions, six failed-subtest outcomes, six failures
and three errors. Five IDs fail in every applicable sample. The separate Herdr
follow-up fills 20 previously missing installer IDs; duplicate closure samples
are not new coverage. Neither whole profile passed. Do not replace these dated
results with the earlier 35-test smoke run or infer complete hook latency.

| Intended unit | Dated observed membership | Installed/native qualification status |
| --- | --- | --- |
| proximal | Nov14 07:16 metadata: eight active JSON bindings, one watcher lock, 17 historical non-JSON members. Five bound `/2` selectors do not remove the other three members. | Complete all-member association, current/target runtime/refresh and writer/restart graph unknown. Extra members and unsafe ancestors require a reviewed bootstrap; a private subtree alone cannot change old unit identity. |
| archon | Nov14 07:17 metadata: `codex-archon` plus watcher lock, selected directories 0700. | Watcher observation does not establish all owner/runtime adoption, global uniqueness or cross-host independence. |
| talos | Nov14 07:17 metadata: `opencode-one`, bindings directory 0755, no named watcher-lock/state member. Existing socket relays are sanctioned operator facts. | Missing paths do not prove process absence. Lock/state initialization, safe ancestry, actual OpenCode refresh and unit closure require qualification. |

The earlier 19-versus-17 historical-name discrepancy has no selected causal
receipt; its cause remains unknown. The private layout report is a proposal,
not permission to move files, create locks, alter permissions or restart units.

## Predecessor join and exact pending slots

| Closure target | Required predecessor correspondence | Current selected evidence | Smallest remaining proof |
| --- | --- | --- | --- |
| R03 / CAIRN-305, including AC4 | F02, F03, J05 and R02 at the final tuple; real host/provider effects, lifetime, custody and shutdown. | Finite Hermes3 and retained-effect/journal source mappings above. | Complete AC4 common-unit and supported-row join, including unresolved host return/status rows; exact predecessor acceptance/source references, not a summary Boolean. |
| R04 / CAIRN-306 | J06 (including J05/F02/F03) and R02; actual emitted host frames/bytes/state. | Historical native12 and accepted produced preparation remain separate populations. | Complete harness/refresh/external coverage and final tuple correspondence; a selected native trace does not fill every supported installed row. |
| R06 / CAIRN-308 | R03, R04, R05, S05 and S04R plus REL-MANIFEST/REL-EQUIVALENCE. | Landed `8631`, finite Go `/3` roundtrip, historical native12/Hermes3, prepared semantic forward/reverse artifacts. | P1–P6 below and independent complete-candidate review. No prior finite verdict supplies whole qualification. |
| HC05 / CAIRN-312 | R02–R06 and every supported installed harness row; explicit unsupported outcomes. | R01 definitions and dated inventory identify the matrix, not completed installed cycles. | Final host × harness × binding/session effects, installed/loaded identity and predecessor joins, followed by independent acceptance. |

| Slot | Required proof; presently pending |
| --- | --- |
| P1 — Agy driver and single admitted native result | Agent-1468's reviewed/landed supported driver, exact single-use admission and one actual same-process A/B/restored-A native result. At this baseline `refuse_native` is unconditional. The future held-image copy and generation must both be inside disposable catalog.Root; client SHA must match before/after. |
| P2 — operator bootstrap and retained lifecycle reverse | Agent-1467's reviewed stopped/joined bootstrap seam, all-member/mode/root handling, same-lock preservation, supported adoption and exact executable reverse to retained Python `/2`. Current Go target loading requires `/3`; a `binding.json`/`retained-argv.json` shape is explicitly refused. |
| P3 — both executable reverse closures | Retained `/2` lifecycle plus S04R Python semantic closure, with actual forward/reverse effect/state/history and selected ABI/launcher proof. A Go `/3` retained-A reversal and a prepared relocated Python launcher do not fill this slot. |
| P4 — final common-RC images | After the last Go change lands, pin final source/tree/blobs and build all five roles, with compiler/tool/runtime/source before/after custody and raw gate correspondence. Keep the `1508` packet frozen; no rebuild or repin is authorized by this draft. |
| P5 — complete-unit/equivalence/predecessor join | D1–D7 positive/negative evidence, real native owners/grants/terminal facts, full unit resources, paired retained-Python/Go results and fault sensitivity, exact F/J/S acceptances and affected installed rows at the final tuple. |
| P6 — R07 technical packet | Both reverse closures, writer fence/join, backup/restore, fresh pre-cutover checkpoint/export before Rust or `/3`, independent review and joint 1386+1404 phase admission. No owner-only decision is currently identified; missing technical facts remain blockers. |

For each replacement proof, retain the criterion/case ID, source head/tree and
blob map, actual executable/config/host selections, exact argv and numeric
exits, raw-result/trace/state locator plus SHA256, expected versus actual
observable, distinct case counts/skips, before/after currentness and cleanup,
and exact independent review and landing references. Record the original failed
or not-run attempt separately. A completion note or a chain of prior ACCEPT
summaries without this correspondence does not fill a slot.

Global12 and Hermes3 remain consumed; their failures, ledgers and held assets
are not replay permissions. Supervisor40dd's conditional future Agy run is a
separate named gate after review, landing and fresh readiness, not a cap reset.
Foundation's STOP_UNCERTAIN/adoption holds and Semantic's loaded-byte/ABI holds
remain. An ordinary callback, current disk hash or a quiet interval cannot
manufacture their disposition.

## Reviewer source/range crosswalk at frozen 8631

Ranges below were checked against the actual frozen tree. Coordinator decision
`00facf60-5de7-400f-a0ec-e118c29de9ff/v1` requires symbol/range and raw-receipt
spot checks before an ACCEPT is acted on. Gate exits and independent code review
are separate evidence. Future source changes need a fresh crosswalk.

| Source | Actual definition/range | Claim bounded by that source |
| --- | --- | --- |
| [release contract](../plans/hybrid-runtime-contracts/release.md#activation-units-drain-and-coexistence) | 655–736; criteria table 898–932; dependencies 935–979 | Complete unit, D1–D7, compatibility, manifest/equivalence/installed gates and observation obligations. |
| [Agy preparation driver](../../scripts/check_r06_agy_native_unit.py) | `refuse_native` 131–133; `validate_build_recipe` 151–187; `checked_packet` 190–278 | Closed native entry, truthful race recipes, controlled 1508 packet and immutable image custody. Blob `4ba6d633a86807c2f5af419dde8aa486e2be6022`. |
| [Same driver](../../scripts/check_r06_agy_native_unit.py) | `prepare_authenticated` 467–547; `prepare_stubbed` 550–644 | Genuine finite auth setup is separate from the non-native traced STUB. |
| [Target association](../../internal/lifecycle/coordination/shared_unit_control_target.go) | `loadUnitTarget` 49–139; `verifyUnitTarget` 141–167 | Actual current/target association and Go `/3` receipt; no authorization flag. Blob `1400759a98cf3a2df22be45d9d51fc66f0935aea`. |
| [Census scope](../../internal/lifecycle/coordination/shared_unit_census.go) | `inspectWatchUnitCensus` 128–189, scope guard 171 | Current census accepts only `disposable-fixture`; it is not a production HOME bootstrap interface. |
| [Agy current readback](../../internal/lifecycle/coordination/shared_unit_census_agy_current.go) | `inspectAgyCurrent` declaration 245; root guard 272; `agyCurrentPair` 337 | Client and generation must be inside the declared root; staged code is not actual native-generation evidence. Blob `7055e2189c8f87019083f1556e464691b2bb604b`. |
| [Legacy refusal](../../internal/lifecycle/coordination/shared_unit_census_agy.go) | `agyLegacyLifecycleRefusal` 893–898 | Explicit missing legacy `/2` binding/argv/bundle observations; never normalize them into `/3`. |
| [R01 characterization](hybrid-runtime-r01-characterization-2026-11-10.md) | 38–62 and 64–145 | Supported/unsupported rows and dated failed populations; source support is not installed qualification. |
| [Semantic rollback/observation contract](../plans/hybrid-runtime-contracts/release.md#rollback-observation-and-retirement) | 841–896 | Executable offline Python reverse, all-writer fence/join, per-lane resets and full semantic rebuild. |

This draft changes documentation only. Its own documentation/check receipts
belong to its review packet and establish no runtime gate, host refresh,
native admission, service cutover, backup execution or elapsed observation.
