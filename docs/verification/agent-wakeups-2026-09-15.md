# Agent wakeup verification — 2026-09-15

## Implemented slice

The [contract](../agent-wakeups.md) is implemented as PostgreSQL wake attempts,
request-only claims, delayed pre-launch retries, authenticated coordination APIs,
a host supervisor, and fresh systemd-managed workers using the existing runner.
The owner requested OpenCode and Hermes deployment and restart of Hermes and its
gateway. The [plan](../plans/agent-wakeups.md) tracks installation separately.

## Tested claims

| Boundary | Evidence |
| --- | --- |
| Only requests launch; one unfinished attempt per inbox; claim retries retain identity; expired deliveries remain held | `core/agent_wakeups_test.go`, disposable PostgreSQL and race-enabled integration suite |
| Duplicate worker entry is refused; zero exit without handling fails; completion survives reconciliation | Store tests and `scripts/check_wakeups.py` |
| Safe pre-launch retries wait, stop after three claims; restore fencing preserves holds; another inbox cannot finish an attempt | Store tests |
| Real API, process wrapper and systemd cgroup; explicit atomic result linked to runner receipt | `scripts/check_wakeups.py` |
| Supervisor killed while detached child lives; restart kills the cgroup and never relaunches uncertain work | Process probe with actual SIGKILL and detached `/usr/bin/sleep` |
| API outage stops a worker at failed renewal; recovery reconciles its hold | Process probe with stopped/restarted API |
| Installed OpenCode and Hermes launch a fresh run, invoke their native shell tool and complete the delivery | Optional native mode of `scripts/check_wakeups.py`, isolated homes and synthetic loopback provider |
| Operational event and wake tables survive a real backup and restore | `make test-lifecycle`, exact JSON comparison before/after restore |

`make test-integration` runs race-enabled Go packages and real CLI/API probes;
its systemd wake probe runs where a user manager is available and reports a skip
elsewhere. `make check` and all 101 Python unit tests passed. The final native
probe passed six checks, including both actual harness executables. Local logs
are `/tmp/cairn-wake-integration-final.log`, `/tmp/cairn-wake-check.log`,
`/tmp/cairn-wake-lifecycle.log`, `/tmp/cairn-wake-python.log`, and
`/tmp/cairn-wake-final-native.log`; these scratch logs are not repository artifacts.

## Limits

The native provider fixture establishes executable launch, tool execution and
completion wiring. It makes no model-quality, real-work usefulness, live-provider
reliability or exactly-once external-effect claim. Explicit handler completion
is still a report; independent task acceptance is separate.

The host is the recovery owner. Unit inspection includes cgroup population;
unconfirmed cleanup retains the hold. A database moved to another host cannot
verify the old host's processes. Stop old supervisors and workers before restore
or host migration. An operator can decide whether a new request is appropriate
after uncertain execution; the system does not make that decision automatically.

## Installed state

Implementation `e4fa7039916ba90d18a238c9fd702d193e797f4d` is installed as both
CLI and API, with clean matching build identities and identical binary SHA-256
`9c14f01797cd7330efa197e02d61dcb7240d0ec3f1f97ea24f3ae7d85699dafd`.
Migration 036 was applied after backup
`cairn-20260916T011406-2937817.dump` in the existing Cairn backup directory.
The API's semantic-worker command and settings were preserved. A connection
probe immediately after systemd restart raced socket readiness; the subsequent
authenticated version check succeeded with matching clean build identities.

Both `cairn-wake-opencode.service` and `cairn-wake-hermes.service` are enabled,
active and running with zero automatic restarts at verification. Their binding
files live in `~/.local/share/cairn/wake/`. Each uses the provisioned `agent/NAME`
profile, the shared collection, a ten-minute deadline and the existing hosted
observer profile for runner observations. Their inboxes were empty at deployment;
no live external-provider work request was manufactured as a smoke test.

OpenCode uses the installed native binary and a dedicated runtime override for
`llamacpp/qwen3.8-27b`, matching the model ID returned by the local endpoint.
The interactive OpenCode configuration still names qwen3.6 and was not edited.
Hermes uses its existing OpenRouter model, `deepseek/deepseek-v4.1-flash`, in
one-shot mode. These live model bindings were configured, not evaluated for
successful real-task execution by the synthetic-provider tests.

The existing Hermes terminal was stopped cleanly and restarted in the same
Herdr pane with its original session ID; Herdr reported it interactive and idle.
`hermes-gateway.service` was restarted and is active/running with a new process.
The Cairn skill was first updated and deployed before implementation (skillpack
`8d24765`), then extended with wakeup guidance and Hermes copying (`a4033bd`,
whitespace follow-up `5bf5676`). All eight deployed skill/reference copies match.


## Codex, Claude and Agy follow-up

After the owner asked about the remaining profiles, their installed
noninteractive interfaces were checked and exercised through the same API,
runner and systemd supervisor against a newly created disposable PostgreSQL
cluster. This follow-up explicitly used existing native account authentication
and real model calls; it was not another synthetic-provider fixture.

| Added service | Installed model | Native result |
| --- | --- | --- |
| `cairn-wake-codex.service` | `gpt-6-astra` | Explicit atomic completion with a runner receipt |
| `cairn-wake-claude.service` | `claude-sonnet-5` | Explicit atomic completion with a runner receipt |
| `cairn-wake-agy.service` | `gemini-3.8-flash-high` | Explicit atomic completion with a runner receipt |

All three services were enabled and observed active/running with zero restarts.
The existing OpenCode and Hermes services remain enabled. The installed Cairn
binary and schema are unchanged: this extension uses owner-configured launchers.

Claude's first probe used the configured `claude-fable-5-1[1m]` and returned an
out-of-usage-credits error. The successful follow-up selected `sonnet`, which the
native initialization identified as `claude-sonnet-5`; the installed binding pins
that resolved model. No credits were purchased and no account credentials were
provisioned or replaced. Interactive model settings remain unchanged.
The successful Claude probe disabled unrelated settings/hooks and MCP servers
while retaining account authentication; the installed binding preserves the
normal configuration and session persistence for the existing lifecycle hooks.
That distinction is not evidence of live hook conformance in a wakeup worker.

`scripts/check_wakeups.py --live-binding /absolute/binding.json` now supports
explicit, repeatable live launcher validation. It requires the disposable-test
DSN and uses its own API identities, sources and result records. Each live probe
requests only a selected verification result; broader task quality is unmeasured.
The same run also checks request filtering, exit-without-acknowledgment,
supervisor SIGKILL cleanup and API-outage recovery. All 101 Python tests passed
again after the probe extension. No store or runner changes required repeating
the earlier schema, Go race or restore campaign.

The Cairn skill's automatic-worker section now names all five harnesses and is
deployed to the existing eight skill locations, including Hermes. Successful
small native completion checks establish working authenticated launch paths;
future provider quota, authentication and task failures remain possible and
remain visible as failed or unresolved attempts.
