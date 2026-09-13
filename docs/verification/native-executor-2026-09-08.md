# Native recurrence executor

Follow-up: the [actual model comparison and permission correction](native-permissions-2026-09-08.md)
supersede the prospective model-run status in this checkpoint.

Cairn now has an opt-in executor for the frozen native recurrence comparison.
The [trial instructions](../../trials/native-recurrence/README.md) describe source
calibration, model-free execution and the separately enabled model runs.
This checkpoint verifies the executor; it establishes no model task acceptance
or general memory benefit.

Source commits are Cairn `55c8ff3` and Striatum's proposed native integration
branch `ee8a463`. The Striatum addition is a test executor, using a supplied
source repository with the existing fixture planner/checks and actual Driver
and LLM supervisor. No production backend or accepted catalog was changed.

## Verified behavior

The final model-free comparison at `/tmp/cairn-native-executor-fixture-l` ran
all three conditions. Each used one invocation, received the exact sealed
prompt, produced a Change Set that passed Driver checks, and failed the real
connection-repair oracle as intended. The fixture adds a harmless test file;
it leaves the actual defect intact and makes zero provider requests.

The native condition acquires and admits a Cairn ECR, checks that its package
contains the entire selected lesson, names that ECR in the produced packet,
and records actual claim, delivery and outcome observations through Cairn.
The complete source is materialized from the sealed Product only after every
file matches its frozen hash. Both mounted views of the sealed inputs reject
writes; source scratch and declared outputs are writable. `make check` and
`make test` pass inside each actual supervised runtime sandbox.

The controller requires the complete successful fixture report and matching
code, Driver binary, toolchain and scenario pins before model contact. It uses
the existing bounded provider relay. The declaration allows a 600-second
invocation, a 660-second dispatch budget and zero internal retries; the
experiment adapter also refuses redispatch. Temporary harness credentials and
caches are removed after each condition. Native database evidence is dumped
before the owned service is stopped.

## Findings from executor calibration

- The supervisor selected system Go 1.23.4, while the frozen Cairn source
  requires Go 1.25. The bridge now receives explicit toolchain and module-cache
  paths from the controller, whose module context selects Go 1.25.0. A
  regression test prevents an ambient `go` lookup when these paths are pinned.
- A dispatch budget equal to the invocation limit lost finishability during
  preparation. The extra 60 seconds covers preparation/completion; it does not
  increase the model's 600-second invocation limit.
- The supervisor removes its working tree after submission. The bridge now
  retains its prompt checksum in the private controller directory, outside
  the model's mounts and the disposable working tree.
- Direct context appears twice because Striatum renders the packet purpose
  both in the objective and Work Graph input. Native context appears once;
  control has no added lesson. Counts are retained. This comparison is not
  exactly matched for exposure or framing overhead.

Early fixture declarations used the wrong retry-field placement, and an early
fixture output used v1 packet names inside a v2 Change Set. Existing guards
rejected those configurations or outputs. They were corrected in the
experiment; no production guard was weakened and no model request was spent
on those failed fixtures.

## Checks and limits

Cairn `make check` and `make test` pass, including 26 Python tests. New tests
cover source drift, path escape, exact text, toolchain selection and rejection
of stale or incomplete fixture evidence. Striatum formatting, build and RFC
lint pass. The affected Driver race suite passed under strict delegated
confinement in 52.899 seconds. A fresh source/oracle preflight retains the four
known baseline failures and all eleven passing reference cases. Database tests
skip in the connection evaluator; its HTTP transport is real and local.

The model-free runtime validates source, transport and output routing. It does
not verify a model's reasoning, its compliance with the Change Set contract or
useful application of memory. Those are the next observations from the actual
model comparison. Fixture planning and mechanical checks also do not grant
Principal acceptance or adopt the proposed native contracts.

[Verification metadata](native-executor-2026-09-08.json) contains hashes, checks
and bounded observations. Private logs and graphs remain under the fixture
root. Doctrine packet `pkt-e152faa478c0644e`, SHA-256
`e152faa478c0644e110ab6c86c778d2e66651bbd48b64f1015229420b43ad2a6`,
uses corpus `corpus-2026-07-12-a11702cc9217` and doctrine
`doctrine-f6bbb5196a3f8bf9`. Its typed observations, validated decision receipt,
thirteen nonmaterial residual obligations and citation closure are under
`/tmp/cairn-native-executor-*`. The verified claim is bounded executor readiness,
not model benefit.
