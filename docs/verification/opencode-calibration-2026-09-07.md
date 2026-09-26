# OpenCode harness calibration — 2026-09-07

Neither calibration produced a repair. OpenCode's mechanical read/edit route
works, but this local model/task combination has not qualified for another
memory-benefit comparison.

The [earlier comparison](opencode-recurrence-2026-09-07.md) retained three failed
arms. These follow-ups ran only the repository-only arm, without memory in the
compiled package. They preserved the historical source, task, shell policy,
8,192-token output limit, 20-step limit and five-minute process deadline.

| Calibration | Thinking override | Context | Process | Seconds | Patch | Cache check |
| --- | --- | ---: | --- | ---: | --- | --- |
| E | Disabled | 65,536 | Timeout | 300.095 | Empty | Failed |
| F | Disabled | 131,072 | Exit 0 | 149.815 | Empty | Failed |

The [retained metadata](opencode-calibration-2026-09-07.json) includes exact
controller and binary identities, fixture probes, serving-template metadata,
run receipts, selected gate evidence and task-assessment joins. Both runs used
renewing fleet leases. Their private PostgreSQL stores stopped and native
session homes were removed afterward. Source snapshots, dumps, reports and
executed controller copies remain in the private `/tmp/cairn-opencode-calibration-20260907-e`
and `-f` directories.

Later note (2026-09-25): the metadata has no `context_tokens` field for E.
E's 65,536 figure is the model `limit.context` in its OpenCode configuration,
which the metadata identifies by `config_sha256` `c8164c7d…`. That configuration
is one of the private `/tmp` artifacts above.

E changed only the per-request thinking setting relative to the earlier
repository-only condition. An inspection during E observed three native
compactions and repeated reads of the two target files. F additionally used the
live server's verified 131,072-token context. Its partial observation contained
no compaction. These are incomplete observations, not full compaction coverage
or proof that compaction caused the earlier failures. Neither condition produced
an edit. All actual necessary-condition failures remain rejected trial tasks
with operator testimony; no Striatum acceptance or capability judgment is inferred.

The installed OpenCode 1.18.21 binary forwarded
`chat_template_kwargs.enable_thinking=false` to a local fixture, while the default
probe left that setting unspecified. The actual serving template contains both
branches of that condition. This verifies configuration support, not improved
model behavior. The [protocol](../experiments/opencode-recurrence.md) links the
provider and server documentation and records the optional calibration flags.

A second fixture issued a read followed by an edit through OpenCode's real
tools and verified the resulting file bytes. The same check passed through
Cairn against a disposable PostgreSQL store. The ingress canary exists only in
the compiled memory record when using the wrapper; it is absent from the task
prompt. Tool availability and execution are therefore checked independently of
the real model's decision to use them. These scripted responses are explicitly
fixture behavior, not model competence or memory-benefit evidence.

The full integration run also exposed a shared-database test failure:
unrelated Go packages wrote to one database concurrently, and an artifact
deletion fixture exhausted the API's bounded Serializable retries. This is
consistent with interference between independent package suites. The local
integration script now gives each package its own database. CI serializes package
execution against its service database. Explicit concurrent transaction tests
and the race detector remain enabled. The isolated suite, including the wrapped
read/edit and option-forwarding probe, passed. Production transaction isolation
and retry limits were not changed to make the test pass.

Keep these negative results. Further memory comparison needs a binding/task
combination that can complete a real repair, or new evidence explaining this
failure. Repeating these conditions adds little. Actual host integration,
broader baselines, observed record use and an avoided recurring failure remain
open; the adapter fixture does not satisfy them.
