# Native model recurrence and scratch permission correction

The first native model comparison did not establish a memory benefit. All
three conditions exhausted the 600-second invocation limit without a
Driver-admitted repair. The independent repair oracle therefore had no candidate
to evaluate; task outcomes remain unknown.

| Condition | Provider requests | Permission denials | Result |
|---|---:|---:|---|
| No memory | 27 | 7 | Invocation timeout; no admitted repair |
| Direct context | 32 | 1 | Invocation timeout; no admitted repair |
| Native context | 29 | 0 | Invocation timeout; no admitted repair |

Native context was delivered through the real Driver and Cairn host. Its
admitted observation identity/version and exact prompt hash match the runtime
observation, which records claim, delivery and timeout. This establishes native
model contact, not successful use of memory. The native transcript also records
a generated test failing to bind its Unix socket; scratch permissions cannot
explain that failure.

[Verification metadata](native-permissions-2026-09-08.json) retains the complete
cohort summary and hashes. Raw operational memory and model transcripts remain
in the private experiment directory.

The comparison used Cairn `55c8ff3` and Striatum's experimental integration
branch `ee8a463`, the pinned OpenCode 1.18.21 binary and the frozen scenario in
`trials/native-recurrence`. Each condition had one invocation, up to 32 provider
requests, 8,192 output tokens per request and 600 seconds. The existing lesson
concerned this same defect; this is recurrence, not unfamiliar-task transfer.
Direct context contains the lesson twice and native context once, so the
comparison is not dose-matched. No accepted Striatum catalog or production
backend was changed.

## Observed setup defect

The control transcript records a denied `write` to `/tmp/opencode`, although
the task permits scratch under `/tmp`. The configured wildcard permission deny
comes after OpenCode's default scratch exception in its resolved build-agent
policy. The explicit tool allowances do not restore `external_directory` access.
Some other denied calls used incorrect workspace paths, so the scratch-policy
finding does not explain every tool failure or prove it caused the timeout.
The entire cohort retained its original configuration through completion.

A local scripted provider reproduced the denial in the actual pinned harness.
The correction adds `external_directory: {"/tmp/*": "allow"}` after the wildcard
deny. A second probe uses the same native filesystem bridge as the experiment:
OpenCode writes the exact expected temporary scratch bytes, reads the sealed
Product, fails to overwrite it, and fails to write an unrelated home file.
Host-side byte checks confirm the sealed input remains unchanged and the home
file is absent. The original policy fails the same scratch assertion. Both
processes exit zero, demonstrating why process status alone was inadequate.
These probes use six local scripted HTTP requests each and zero model requests.

## Calibration change

`scripts/trial_native_permissions.py` exercises real OpenCode tool dispatch
before the default native fixture comparison. Model execution requires a
successful permission probe for the pinned harness and current permission
profile as well as the existing matching Driver fixture. The probe source is
included in executor pins. The source/oracle, native process supervisor, lesson,
request and time limits, and model-owned output contract remain unchanged.

This check covers the observed scratch failure and two write boundaries. It
is not a general permission audit, task acceptance, or a memory-effect result.
The next model comparison must be separately recorded under the corrected
executor; the original failed conditions remain part of the evidence.

## Verification

The corrected calibration at `/tmp/cairn-native-executor-fixture-m` passes with
current executor pins. The actual OpenCode probe completes its four tool calls
as expected. All three native fixture conditions use one invocation, zero model
requests, receive the exact prompt, and produce checked harmless changes which
fail the real repair oracle. `make check` and `make test` pass, including 26
Python tests. The readiness test also rejects missing permission evidence,
a different harness hash, and an obsolete policy. No store schema changed.

Doctrine packet `pkt-7d12afd94ce2c487`, SHA-256
`7d12afd94ce2c487457d11575adcb9904857baf7221118ec90ab1cc3e932f713`,
uses the validated release `d3e0c0d4ccd1920b2e045c156f1cf0db4fc5f04f`.
The decision is limited to this reproduced experiment configuration defect;
no production deployment or native contract adoption follows. Twelve remaining
nonmaterial doctrine obligations are retained with their scope rationale under
`/tmp/cairn-native-permissions-residuals.json`.
