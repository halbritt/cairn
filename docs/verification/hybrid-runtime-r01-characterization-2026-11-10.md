# R-01 characterization — 2026-11-10

This continues [the November 6 discovery](hybrid-runtime-supported-operations-2026-11-06.md)
for CAIRN-303. It records a fresh, independently selected baseline of
`87b2ad3c06b1fcfa44ac576a9c120207d21752e4`, including failures, and reconciles
the older 39-gap inventory with R-01's seven acceptance criteria. Later trunk
changes are not included in these measurements.

This is a characterization report. Passing source or synthetic-process tests
does not establish installed effects, effective hooks in continuing sessions,
model parity, full-unit drain/rollback, production readiness or the observation
window. Unrun or unavailable observations remain explicit. The private current
host/session inventory and raw test streams are retained outside Git.

Owner decision `40d0c42c-00d7-43b7-9460-d2dc9656a027/v1` permits `make check`,
`make test` and `make test-integration` on the host. Other tests and probes retain
the campaign isolation rule. No production change is authorized by a test run.
The RTERM1 observer and embedded-reference Source work remain paused.
Coordinator direction `2fb6a5a6-2620-4310-9fef-ab19c89819b2/v1` requires the named
live-client, remote-host and roster observations to remain inspection gaps;
they do not block completing this report. Coordination later landed the five
HOME-guard fixes in `7cbf6a56`. The two failed-gate reruns use a separate source cohort; their outcomes are
retained in a separate private record and do not replace the results below. The Release report
does not repair fixtures or production configuration.

## Criteria and evidence

| R-01 criterion | Recorded evidence and limit |
| --- | --- |
| Five-harness operation matrix | The table below and the detailed [operation expectations](hybrid-runtime-supported-operations-2026-11-06.md#operation-evidence-and-required-installed-effects) separate source support, unsupported operations, installed effects and live observations. |
| Current host/client inventory | Private selected Proximal configuration/source/manifest/service-image/platform/asset observations; Archon, Talos and the authoritative managed-host/client roster remain explicit inspection gaps. File identity does not attest loaded hooks or a semantic worker. |
| Real-process fixture mapping | Every selected ID retains its evidence lane, supported/unsupported/smoke row kind, REL IDs, exact outcome and fixture hash. Scripted recovery and synthetic cancellation remain labelled. |
| Reproducible measurements | Commands, interpreter/tool/source hashes, child environment, three samples, import/setup and suite durations, returned subprocess observations and failures are retained. Complete Popen-only hook and model latency is unavailable. |
| Continuing-session freshness | Private session rows retain native/Cairn/execution correspondence and account-selector uncertainty. Supported reload methods and their scope are below; no refresh or replacement conversation was performed. |
| Separate legacy selection shapes | Retained `/1` and `/2` binding metadata is separate from historical two-file code pins. Help, hint/no-op and excluded calls remain closure smoke. The effectful specification requires actual selected executables/adapters and resulting state. |
| Observation lanes | Host × harness × account/binding, plus the shared-database semantic lane; expected identity, minimum supported cycles and read-only reference/lease/drain facts are explicit. New `/3` execution and later full-unit qualification are separate acceptance work. |

## Source operations and observation boundary

The existing source event tables are in the
[Claude installer](../../scripts/install-claude-hooks.py),
[Codex installer](../../scripts/install-codex-hooks.py),
[OpenCode plugin](../../integrations/opencode/lifecycle.ts),
[Hermes provider](../../integrations/hermes/__init__.py) and
[coordination frontend](../../integrations/lifecycle/coordination.py).
They do not by themselves prove which callbacks a running client loaded.

| Harness | Ordinary memory source support | Coordination and unsupported boundary | Current evidence limit |
| --- | --- | --- | --- |
| Claude | SessionStart/prompt recall; tool hints; PreCompact/SessionEnd capture. Ordinary Stop has no memory capture. | Native hook/channel delivery and idle wake; public running native cancellation unsupported. `/2` bound recall has its own ownership/refusal contract. | Selected memory, required-context, process/recovery and installer assertions; no complete installed/live event matrix. |
| Codex | SessionStart/prompt recall; PreCompact and asynchronous Stop capture with threshold/retry/continuation rules. No ordinary SessionEnd capture or tool hints. | Exact queue/turn native delivery and idle wake. Interrupt presence is not request-cancel or cleanup proof; public native cancellation unsupported. | Selected memory/bridge/queue-double assertions; no exact current native queue, refresh or wake qualification. |
| OpenCode | Actual TS request-copy recall, tool hints and idle/compaction/disposal capture; child/title exclusions. | Host/peer delivery and idle bridge; cancellation only within the qualified opt-in trial. | Shared Python/peer assertions do not execute the actual TS callback matrix or qualify cancellation. |
| Agy | Ordinary automatic memory unsupported; explicit MCP memory is separate. | PreInvocation and fully idle Stop; top-level wake versus child deferral. Public native cancellation unsupported. | Source expectations and installer smoke; no invented ordinary memory or actual native cycle. |
| Hermes | Required Python provider/registration, owner-turn request-copy recall and capture/disposal behavior. | Host middleware/native queue; local abort does not attest public Store request cancellation. | Selected provider-engine assertions do not establish installed registration lifetime, native wake or recovery. |

For every supported installed row, the effectful specification requires seeded
retrieval or confirmed capture/journal state, exact event/frame/stdout/stderr/status,
selected executable and host adapter, plus the applicable wrong-account,
privacy/child, missing/changed-file, failure, stale-owner and lost-lease negatives.
A fake API response, help call, JSON key or configured command is insufficient.
Both hook orders and the host-specific required-context failure priority belong
to bound-recall acceptance. Retain asynchronous capture uncertainty.

## Frozen fixture selection and measurements

An independent clean source clone fixed the source at `87b2ad3c`; its Git metadata
was visible inside the existing wrapper. The runner is
[`check_hybrid_release_baseline.py`](../../scripts/check_hybrid_release_baseline.py),
SHA-256 `351a47d8b62159ab0e77cb3f7821291f295cef5fc93a2c91bef66b76385c6e48`.
Its [companion](../../scripts/test_hybrid_release_baseline.py) is SHA-256
`a756f4dae0f90a939345861362cf986d5e80ef89f71f895d7bb4ba833b7bebb5`.
The 16 suite pins below matched the captured bytes. The runner retains all
141 named IDs and the 35/23/83 profile split; no failing assertion was removed.

The first invocation was `legacy-all --samples 3`, launched through the wrapper
by the author. Its isolation receipt and full outer argv were not retained.
The raw child PATH and `/work` Git observations are consistent with that route;
they do not independently prove the full isolation boundary. Herdr was absent
there, so the installer suite's 20 IDs lacked coverage. A second
`legacy-closure --samples 3` invocation bound only the actual Herdr ELF read-only
at `/opt/r01-tools/herdr` and added that directory to PATH. All 20 installer IDs
then passed three times. The other 15 closure IDs were repeated and retained
as a separate population; they are not extra distinct coverage. The follow-up's
retained isolation receipt passed its wrapper checks at `87b2ad3c`.

| Population | Results | Full runner wall time |
| --- | --- | ---: |
| Original legacy-all: 48 suite executions | 36 passed, 9 failed, 3 missing coverage; 348 passing method executions and 15 nonpassing method executions; 60 selected executions unavailable | 102.792131 s |
| Closure follow-up: 15 suite executions | 6 passed, 9 failed; 90 passing and 15 nonpassing method executions; no missing coverage | 17.617567 s |
| Per-ID characterization, using the follow-up only for the 20 missing installer IDs | All 141 IDs observed three times: 408 passing method executions, 6 failed-subtest outcomes, 6 failures and 3 errors. Five IDs fail in every sample. | Separate populations above; no combined wall-time claim |

Neither invocation passed its whole selected profile. For the third row, use
the original observation for every available ID and the follow-up only for
previously missing installer IDs. Do not add the duplicate 45 closure method
executions to that 423-execution population.

Each table row below gives selected method count, exact fixture SHA-256 and
suite durations for samples 1/2/3. Durations include fixture setup/teardown.
The installer row uses the Herdr follow-up; all other rows use the original run.

| Fixture | IDs | SHA-256 | Suite seconds, samples 1 / 2 / 3 |
| --- | ---: | --- | ---: |
| [test_installed_lifecycle_bundle.py](../../scripts/test_installed_lifecycle_bundle.py) | 7 | `d3c9067614baf538dc45bf37b293e763240333a185b0ef0a63ceae9f512b49a0` | 0.300580 / 0.334990 / 0.425103 |
| [test_lifecycle_bundle.py](../../integrations/lifecycle/test_lifecycle_bundle.py) | 6 | `ee4cbbd05832dc717536b98c4491f25721e846ac9a6e91c84ac10c6e8d940966` | 1.005654 / 1.095863 / 1.518356 |
| [test_install_agent_coordination.py](../../scripts/test_install_agent_coordination.py) | 20 | `5bab96d52c0a907b9974670338c98a264695211a0d144919da770a5c98ee6935` | 1.734642 / 1.750113 / 1.704797 |
| [test_install_agent_coordination_timeout.py](../../scripts/test_install_agent_coordination_timeout.py) | 1 | `2af1de2aeb69672039962c51d71466d116d3d75c2ed928e26542aeb78af5f5c4` | 1.278299 / 1.287700 / 1.331095 |
| [test_install_agent_coordination_active_turn.py](../../scripts/test_install_agent_coordination_active_turn.py) | 1 | `dc62d773318658f5d5446072118fa6f59b177422397035d852055574dc3366b8` | 1.211372 / 1.218895 / 1.293574 |
| [test_claude_lifecycle.py](../../scripts/test_claude_lifecycle.py) | 10 | `4f93c793b3730de135ed5155d3491b35c768f6d26e3ddd461729a744b1d4af1a` | 0.174950 / 0.194815 / 0.210614 |
| [test_codex_lifecycle.py](../../scripts/test_codex_lifecycle.py) | 5 | `0ed57dbe391babaca4bec8e27edfaf041b64613a4dacfde2cd22e2ed032b6800` | 0.145621 / 0.210711 / 0.189987 |
| [test_hermes_lifecycle.py](../../scripts/test_hermes_lifecycle.py) | 4 | `f9bfa9972b974b966984e8752234f09af54b09276dd7356692b371aa486bfdf8` | 1.089272 / 1.155779 / 1.166273 |
| [test_claude_inbox_recall.py](../../scripts/test_claude_inbox_recall.py) | 3 | `2912f544d44a38d6d10a6bef27bf8a1f4a1965a23861fa3dfcea5c5544cafc03` | 1.289869 / 3.013359 / 1.976461 |
| [test_inbox_recall_bridge.py](../../scripts/test_inbox_recall_bridge.py) | 1 | `7133f88fe4f8d14b41c8a46f9b4dd1d9ebaabced70606fc53c4cd229e16a1949` | 0.335140 / 0.714761 / 0.430500 |
| [test_inbox_recovery.py](../../scripts/test_inbox_recovery.py) | 53 | `224605b00dfce70416c918bacc249fd419fc26f5cac925126eb6ddf6ee224e14` | 15.503840 / 19.721705 / 16.982764 |
| [test_native_session.py](../../integrations/lifecycle/test_native_session.py) | 10 | `d1d41372735ff299fec80244bcc04e835322e47a50a870148f6542b3ff499db5` | 0.009275 / 0.016305 / 0.009704 |
| [test_unix_peer_transport.py](../../integrations/lifecycle/test_unix_peer_transport.py) | 6 | `1cbc130943d9c2391ab22660cdf66eb2a14d95ef515c8609d444932dd13c4f29` | 0.031313 / 0.039420 / 0.037249 |
| [test_activation_contention.py](../../scripts/test_activation_contention.py) | 3 | `be2ebd69a02654a4f163582d6cc2864f4fe5cefcb23a167827b1d4c87e119cf5` | 3.785981 / 4.637329 / 3.958718 |
| [test_idle_wakeup.py](../../scripts/test_idle_wakeup.py) | 4 | `83975cebd94029fc3af92057e46cb69fe5233775813a91a9da153d31908084a8` | 1.898730 / 4.133436 / 2.924964 |
| [test_install_wake_service.py](../../scripts/test_install_wake_service.py) | 7 | `e1617dc7b8a1f6e0c0fc05824597b4ddbdc3fed046b041e951a66fbccb4e7bbd` | 0.032060 / 0.054033 / 0.034148 |

The same five nonpassing IDs occur in both applicable populations:

- `InstalledLifecycleTests.test_coordination_installations_run_outside_checkout_for_every_harness` — failed subtest.
- `InstalledLifecycleTests.test_installed_memory_refuses_changed_or_missing_helpers_before_state_write` — failure.
- `InstalledLifecycleTests.test_memory_installations_are_complete_for_every_supported_harness` — failed subtest.
- `LifecycleBundleTests.test_every_lifecycle_source_is_in_the_installed_snapshot` — failure.
- `ActiveTurnIntegrationTests.test_stalled_turn_cancel_and_namespace_descendant_cleanup` — error.

Their streams and tracebacks remain in the raw reports; no repair or blanket
classification as sandbox-only was made. Installer, help/hint/excluded smoke
and synthetic app-server cleanup are not successful native delivery or public
request cancellation.

No Go/Rust/model build was required by these selected Python fixtures. The raw
report separates import/loader time from suite time and records individual
method durations. The first report has 903 `subprocess.run` observations,
including 300 coordination issued-command launches; these are not a complete
lifecycle-event latency population. The 23 selected memory cases do not supply
complete inner hook-process measurements. Popen-only and mocked calls remain
unavailable. Concurrent machine activity was uncontrolled; no performance
improvement, target percentile or native/model latency is claimed.

The measured interpreter was Python 3.14.4, Linux x86_64/kernel 7.0.0-43.
The child runner selects disposable HOME/account/XDG/TMPDIR paths, LANG=C.UTF-8,
TZ=UTC, PYTHONDONTWRITEBYTECODE=1 and explicit tool lookup. It overrides the
outer TZ/TMPDIR, so an outer setting is not the child environment. No owner
credentials or native socket selectors were inherited. The raw reports retain
the exact child environments and all 30 selected source hashes.

Relevant tool SHA-256 values:

| Tool | SHA-256 |
| --- | --- |
| bwrap | `523da3e7399044be5163aee6f57a77a6bef7454376e28f0a0627920bae1b76b6` |
| node | `904a6d3484241d466989f9df9ba3aab822b1496191bdb28230eb491a55c32a52` |
| git | `ed8b2df973b28c0fb773b9c0dc0816d3ee2b3835bb932d9153c21417bb51b362` |
| Herdr, follow-up only | `14d9e8bc8b4a9e0118d77c32266f76d461f09b40342eb23752960bd89f59929d` |

Reproduce from the selected clean checkout with artifacts outside it. Use the
existing wrapper's `--ro-bind SOURCE:DEST` for the actual Herdr executable;
its binary dependency closure must exist in the namespace. Supply an unused
receipt filename. The two measured inner commands were:

```sh
python3 -B scripts/check_hybrid_release_baseline.py --profile legacy-all --samples 3
PATH=/opt/r01-tools:$PATH python3 -B scripts/check_hybrid_release_baseline.py --profile legacy-closure --samples 3
```

The outer invocation is `python3 -I -B scripts/test_hybrid_runtime_operations.py
--timeout 900 --receipt RECEIPT [--ro-bind HERDR:/opt/r01-tools/herdr] -- sh -c COMMAND`.
This is a template for a new run, not a retained transcript of the first outer
argv. Replace the uppercase placeholders with actual values; neither a real account
home nor production Cairn/socket state is an input. A repeated measurement is
a new dated sample, not proof of the earlier population or live qualification.

## Host standard gates

The three named standard targets ran sequentially against the same frozen
source. The environment was whitelisted, with disposable account/XDG paths,
fresh private Go caches and no production DSN/native profile selectors.
The integration script created only its own temporary cluster/databases and
stopped that cluster through its EXIT cleanup. The stopped cluster's artifacts were archived under the evidence root as
`failed-postgres-87b2ad3c.tar.gz`; the original temporary directory has no durable
lifetime guarantee. The original gate results remain unchanged by the later
guard fix.

| Target | Exit | Elapsed wall time | Boundary |
| --- | ---: | ---: | --- |
| `make check` | 0 | 53.319541 s | Go vet, contract check, gofmt check and design-source check passed. |
| `make test` | 2 | 88.015807 s | Go phase failed on five literal HOME guards; Python and Node recipe phases were not reached. |
| `make test-integration` | 2 | 130.430271 s | cmd/cairn package failed on three of those guards; later package/fixture phases were not reached. Disposable PostgreSQL stopped. |

The five IDs are `TestLifecycleCoordinationClassAPythonGoDifferential`,
`TestLifecycleCoordinationClassBRefusals`,
`TestLifecycleCoordinationMemoryBindingValidation`,
`TestStage2DClassAPythonGoCoordinationCorpus` and
`TestStage2DClassAPythonGoJournalCorpus`. Their source locations are
[cmd/cairn](../../cmd/cairn/lifecycle_coordination_test.go),
[coordination](../../internal/lifecycle/coordination/stage2d_parity_test.go) and
[journal](../../internal/lifecycle/journal/stage2d_parity_test.go).
At `87b2ad3c`, all five required literal HOME=`/tmp/home`; the private disposable
host HOME differed. Do not spoof that guard to imply namespace isolation.
Coordination replaced the guards with per-test temporary homes in `7cbf6a56`,
a later revision than this measurement. The separate two-gate rerun does not
replace any result in the table above.
Elapsed gate time includes build/setup and is not individual test or hook latency.

## Session freshness, lanes and selection

A current MCP connection, reported caller-origin, configured hook hash or
process parent does not attest the effective hook in a continuing session.
The private current session table keeps each native conversation, Cairn agent
and current execution separate, and marks uncorrelated account selectors and
loaded-hook identity unknown. It is not an authoritative all-host roster.

| Host client | Supported refresh/reload scope | Current report |
| --- | --- | --- |
| Codex | Exact-thread loaded-hook readback and supported guarded no-edit config refresh can affect all loaded threads, skills/plugins/MCP. Each affected thread requires its own expected-hook readback and natural Stop/idle/wake cycle. | No refresh performed. Historical single-conversation evidence is scoped to that conversation. |
| Claude | Settings load at session start; targeted existing sessions require supported restart/resume. | No restart or replacement performed; loaded-hook identity remains an inspection gap. |
| OpenCode | A fresh process is required for the selected host plugin. | No process replacement; actual callback identity remains a gap. |
| Agy | Native MCP reconnection is separate from hook identity; top-level/child ownership remains distinct. | No unsupported hook-refresh mechanism or parent identity is invented. |
| Hermes | Fresh CLI process or affected gateway restart for selected adapter/configuration. | No gateway or session restart; loaded provider/registration identity remains a gap. |

The [existing-conversation guide](../codex-lifecycle.md#existing-conversations),
[dispatcher guide](../recall-dispatch.md) and
[selection/lane description](hybrid-runtime-supported-operations-2026-11-06.md#selection-lanes-and-live-session-freshness)
retain the host-specific procedure and limits.

For each host × harness × account/binding lane, record the selected executable,
configuration, bundle/code-pin shape, native-session/attempt identity and exact
supported event. Minimum future coverage is one observed cycle for each
supported installed memory event and supported request/reply/recovery path,
with applicable idle, busy, restart, refusal and qualified cancellation cases.
Unsupported/no-op rows are separate negatives. The shared-database semantic
lane also identifies every writer and dependent lane. The later observation
window is at least 24 continuous hours after the last relevant change, with a
full semantic rebuild and resets for affected dependent lanes. This report
starts no window.

Available facts include selected file hashes, native/Cairn session metadata,
authenticated delivery/wake status and read-only journal facts. A
`settled_journal_index` result alone does not dispose of delivery-free
`reply_possible` obligations. Journal flush, compact inspect and some legacy
check-only routes can mutate or probe native state; none was used as inventory.
D1–D7 barriers, mixed-reader rollback and actual transition remain later R-06/
R-07 work. New `/3` invalid-code exit 1/no-native-prompt-veto remains an expected
row; report-and-stop is the remaining wake guard, not observed native refusal.

## Disposition of the prior 39 gaps

The prior manifest's one-dispatch read-only restriction and obsolete separate
execution terms are historical context, not new blanket approval gates. Current
owner direction permits isolated tests and the three named host gates, keeps
production changes gated and keeps RTERM1 paused. The table preserves each
missing relation; it does not turn an unknown observation into a pass.
Detailed source references, exact original wording and current evidence files
are in the private `criteria-and-39-gap-disposition.json`.

| Gap | Missing relation | Current disposition / next owner |
| ---: | --- | --- |
| 1 | A current selection consistent with the two changed suite files | **source selection refreshed**. Current runner `351a47d8` pins the actual bridge/wake fixture bytes; all 30 reported source hashes match both reports. Next: Release. |
| 2 | A fresh independently measured baseline for the selected current fixture bytes | **baseline observed; failed**. All 141 selected IDs have three observations across two reports: 136 pass and 5 fail per sample; no profile accepted. Next: Release. |
| 3 | Complete actual memory/coordination child latency rather than unittest duration or Popen-only timing | **explicit measurement gap**. Suite/import timings and 903 subprocess.run observations are retained. The 23 selected memory cases and Popen-only paths do not supply complete hook latency. Next: Release / later R-05 comparison. |
| 4 | Claude installed ordinary recall, capture and hint effects | **installed-effect gap**. Claude source/fixture boundaries mapped; no actual installed recall/capture/hint cycle is claimed. Specify seeded retrieval/write/marker, exact frames/status and privacy/failure negatives. Next: Release R-03/R-04 after Foundation. |
| 5 | Codex installed ordinary recall and capture effects | **installed-effect gap**. Codex source/fixture boundaries mapped; require SessionStart/prompt/PreCompact/async Stop and threshold/retry/continuation effects at the actual installed host. Next: Release R-03/R-04 after Foundation. |
| 6 | OpenCode actual TypeScript memory callback effects | **host-adapter inspection gap**. Actual OpenCode TS task/idle/compaction/dispose callbacks are not exercised by the shared Python selection. Require request-copy/hint/capture and child/title negatives. Next: Release R-04 / Foundation host adapter. |
| 7 | Hermes actual provider/registration memory effects | **host-adapter inspection gap**. Hermes fixtures do not establish the installed required provider/registration lifecycle. Require owner-turn/capture/dispose and pending-capture/error assertions. Next: Release R-04 / Foundation Hermes adapter. |
| 8 | Agy installed exclusion of ordinary automatic memory | **unsupported expectation; live gap**. Agy ordinary automatic memory remains unsupported. No actual Agy turn was used to manufacture a negative observation. Next: Release / Coordination host owner. |
| 9 | Claude exact required inbox-recall effects and failure priority | **native inspection gap**. Claude bound-source tests are not an actual channel cycle through both hook orders and accounting/lease/owner failures. Next: Coordination / later R-04/R-06. |
| 10 | Codex exact required inbox-recall effects and failure priority | **native inspection gap**. Codex shared bridge test is not an exact queued native turn through both hook orders and required-context refusal. Next: Coordination / later R-04/R-06. |
| 11 | Claude issued-context handling in a current native session | **native inspection gap**. No fresh Claude native request/reply cycle was scheduled. Current report acknowledgements are coordination, not independent qualification. Next: Coordination / later R-04/R-06. |
| 12 | Codex issued-context handling in a current native session | **native inspection gap**. No fresh Codex native request/reply cycle was scheduled; existing failed or uncertain deliveries remain fenced. Next: Coordination / later R-04/R-06. |
| 13 | OpenCode issued-context handling in the actual host | **native inspection gap**. Actual OpenCode host issued-context admission/settlement remains unobserved. Next: Coordination / later R-04/R-06. |
| 14 | Agy issued-context handling in the actual host | **native inspection gap**. Actual Agy PreInvocation/fully idle Stop admission and parent/child ownership remain unobserved. Next: Coordination / later R-04/R-06. |
| 15 | Hermes issued-context handling in the actual host | **native inspection gap**. Actual Hermes host middleware/queue issued-context admission remains unobserved. Next: Coordination / later R-04/R-06. |
| 16 | Claude idle wake with the selected loaded channel/hook | **native inspection gap**. Claude exact loaded channel/hook idle-wake cycle was not invoked. No production refresh/wake grant inferred. Next: Coordination / later R-04/R-06. |
| 17 | Codex idle wake with exact native queue/turn ownership | **native inspection gap**. Codex exact native-queue/turn idle-wake cycle was not invoked. The broken delivery route is not silently certified. Next: Coordination / later R-04/R-06. |
| 18 | OpenCode native peer idle wake | **native inspection gap**. OpenCode peer-verified idle bridge cycle and ownership/capability refusal remain unobserved. Next: Coordination / later R-04/R-06. |
| 19 | Agy top-level idle wake and child deferral | **native inspection gap**. Agy top-level idle wake and child deferral remain unobserved; no parent pane identity substituted. Next: Coordination / later R-04/R-06. |
| 20 | Hermes exact-session idle wake | **native inspection gap**. Hermes exact-session native queue wake remains unobserved; reconciliation is not wake. Next: Coordination / later R-04/R-06. |
| 21 | Actual qualified opt-in OpenCode cancellation | **qualified-trial inspection gap**. OpenCode cancellation remains opt-in and trial-qualified only. No new trial, frozen-trial replay or public cancellation guarantee. Next: Original qualified trial owner / Coordination. |
| 22 | Native refusal of public cancellation on Claude, Codex, Agy and Hermes | **unsupported expectation; live gap**. Claude/Codex/Agy/Hermes public native cancellation remains unsupported; synthetic timeout/cleanup is not a native refusal matrix. Next: Coordination. |
| 23 | Claude independent-process recovery behavior | **native recovery inspection gap**. 53 selected recovery methods include real children/scripted peers; they do not prove a Claude abrupt native writer death/restart cycle. Next: Coordination / later R-06. |
| 24 | Codex independent-process recovery behavior | **native recovery inspection gap**. No Codex native queue/process replacement was performed. Preserve exact old uncertainty rather than replaying it. Next: Coordination / later R-06. |
| 25 | OpenCode native recovery selection and actual outcome | **native recovery selection gap**. No exact OpenCode native recovery fixture is selected here. Existing owner must select actual peer/request crash case for later qualification. Next: Coordination / later R-06. |
| 26 | Agy native recovery selection and actual outcome | **native recovery selection gap**. No exact Agy owned host/parent recovery case is selected here. Next: Coordination / later R-06. |
| 27 | Hermes native recovery selection and actual outcome | **native recovery selection gap**. No exact Hermes provider/session recovery case and registration lifetime is selected here. Next: Coordination / later R-06. |
| 28 | Actual invalid-code /3 preflight exit1 without native prompt veto | **later /3 acceptance**. Invalid-code exit 1/no-native-veto/report-and-stop is an explicit expectation. New /3 execution is not an R-01 prerequisite and was not performed. Next: Foundation / Release R-04–R-06. |
| 29 | Which configured hooks are cached and whether the selected bytes took effect | **session inspection gap**. 20 registered Cairn session rows are mapped to reload semantics and explicit unknown effective-hook/account observations; no refresh performed. Next: Existing host owners / coordinator agent-1350. |
| 30 | Current service executable and semantic worker actually loaded | **partial current image observation**. Current selected services have PID-to-file image hashes. Actual semantic worker launch mode and loaded image correspondence remain unobserved. One selected installed bundle member mismatches its manifest; preserve it. Next: Existing service owners / Semantic agent-1339. |
| 31 | Current effective host kernel, processor capabilities and semantic loaded-library interface | **partial host ABI/CPU observation**. Proximal x86_64/kernel 7.0.0-43 / i5-11400F and running API ELF64 little-endian EM_X86_64 are observed; effective semantic CPU/library ABI remains unknown. Next: Semantic agent-1339 / existing host owner. |
| 32 | Loaded model/tokenizer/package identity and effective provider/thread settings | **partial asset observation**. Five current model/tokenizer files match the retained manifest. Imported packages, actual loaded model/provider/thread settings remain unobserved. Next: Semantic agent-1339. |
| 33 | Actual semantic outputs, full failure behavior and independent baseline measurements | **later semantic acceptance**. Current R-01 is file/platform inventory and fixture measurement, not model output/parity/resource qualification. Consume existing Semantic results under their own exact scope. Next: Semantic agent-1339 S-04/S-05. |
| 34 | Effective inherited Python flags/environment, full import closure and startup side effects for each launch route | **paused reference work; not R-01 prerequisite**. No original-process interpreter flags/import/startup probing. RTERM1 observer/embedded CPython work remains paused by owner decision `98d46758`. Next: Existing Release/Foundation owners; owner resume required. |
| 35 | Original reference-tracer registration, callback body/data/configuration and lifetime | **paused reference work; not R-01 prerequisite**. Original tracer registration/callback/data lifetime was not re-opened or invented from a header. Next: Existing Release/Foundation owners; owner resume required. |
| 36 | Actual source-operation, native call, thread/lock/namespace/resource and terminal-cleanup correspondence for the reference candidate | **paused reference work; not R-01 prerequisite**. No native/thread/namespace/resource/finalization execution. Preserve all prior Source and uncertain effects. Next: Existing Release/Foundation owners; owner resume required. |
| 37 | Qualified mixed-reader and rollback-rewriter compatibility for shared data | **later transition qualification**. Full mixed-reader/rollback-rewriter shared-domain qualification belongs to later exact-candidate R-06/R-07 gates, not current-source inventory. Next: Release R-06 / Coordination / Semantic. |
| 38 | Current Archon and Talos managed inventories | **explicit remote inspection gap**. Archon and Talos have historical installation evidence only. No remote probes started; agent-1350 is asking whether current owner observations are wanted. Next: Coordinator agent-1350 / existing host owners. |
| 39 | Complete set of other managed installations beyond the local configured routes | **explicit roster gap**. Authoritative managed-host/client roster is not supplied; 20 project session rows and local configured routes cannot prove completeness. Agent-1350 asks owner. Next: Coordinator agent-1350 / owner. |

## Retained evidence and completion boundary

The private evidence set contains the two raw reports, exact selected per-ID
characterization, host-gate commands/results/logs, current read-only inventory,
installed member-hash discrepancies, session map, semantic asset hashes and
the criteria/39-gap disposition. No private account files, raw streams, current
native session identifiers, credentials or generated binaries are committed.
A preserved mismatch remains a finding; it is not repaired or certified benign.

| Evidence artifact | SHA-256 |
| --- | --- |
| `legacy-all.json` | `d9fd3d4afe07d91e2672744d494906b8d3b980152eb868fba4365f0fe39a0562` |
| `closure-with-herdr.json` | `297458271509d81b80d4a7a72b69e1904812cb8004e05fa5da0387661a7c621f` |
| `selected-case-characterization.json` | `917c10516f90352b9b2c32a371868f92eb881795e82e1b1044eb3af90341d82d` |
| `host-standard-gates.json` | `e75ef3a50ce155f83518e7fda0ed240e3538e2809a1752fc443ae753fa5e9c29` |
| `criteria-and-39-gap-disposition.json` | `15de5840bfe8827fb6055cdddcb1027e68660c61cc63637b7d36a5b7042335b8` |
| `failed-postgres-87b2ad3c.tar.gz` | `356dbda39e58b9aa42b74297f7911d0f07afe6b50b590d38df78ef39dc797007` |

This report supplies R-01's source/fixture characterization with explicit
inspection and measurement gaps. It does not assert that the failed standard
gates passed or that current source equals installed/live behavior. Host roster
and remote observation decisions remain with the coordinator/owner. Later
provider, `/3`, live-cycle, semantic, transition and retirement acceptance keep
their own exact evidence and authority requirements.
