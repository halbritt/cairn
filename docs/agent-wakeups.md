# Automated agent wakeups

Status: implementation contract, 2026-09-15. The owner delegated design,
implementation and deployment, including all five named harness bindings.

## Selected behavior

A host-side `cairn wake serve --config FILE` process polls every two seconds.
Each owner-written binding fixes the authenticated inbox profile, workspace,
launcher arguments, runner observation profile and deadline. Only `request`
events launch fresh workers. Source text cannot select a command or workspace.
One unfinished wake attempt per inbox is enforced in PostgreSQL. Replies and
notices remain available to ordinary inbox consumers.

The claim transaction creates a durable wake attempt and an event lease together.
An unfinished wake attempt holds its delivery out of ordinary expired-lease
reclaim. This is essential: an expired lease cannot stop a live process.
The supervisor records launch intent before starting a uniquely named systemd
user unit. That unit runs the existing authenticated Cairn process runner.
The worker renews its delivery lease, cancels execution on renewal failure and
has a fixed deadline. systemd owns the whole worker cgroup and removes surviving
children when the unit stops. Supervisors use an exclusive host lock.

The worker receives the exact event source version and explicit completion
instructions. It uses its ordinary agent profile to complete the delivery.
Exit zero never acknowledges work. Runner receipts record process observations;
reported handling and result notes remain separate from task acceptance.

## Recovery and retries

On restart, the supervisor stops the recorded attempt unit and verifies that it
is inactive before releasing the inbox. Failure to verify termination leaves
that attempt held and reports an error. This includes launch intent with an
unknown start outcome. It does not search for PIDs or assume lease expiry kills
children. Unit names derive only from durable UUIDs.

Prepared attempts that never acquired launch intent can retry. Confirmed
pre-launch runner failures can retry with delays of 10 and 30 seconds, up to
three claims. Once execution may have started, missing completion is a terminal
processing failure requiring operator review and a new request to try again.
An explicit handler completion survives a later supervisor crash. All attempts,
runner receipt links, process outcome labels and failure reasons stay inspectable.
No automatic replay of uncertain external effects is promised.

An API outage stops new claims. A worker unable to renew stops promptly; an
unfinished attempt remains held until service recovery can reconcile it. A
restore invalidates leases but retains wake holds. Stop supervisors and worker
units before restoring; only restart against the fenced, verified database.
This is a single-host systemd user-service contract. Moving a database to another
host requires stopping old workers first. Local commands are not a sandbox and
must operate within the owner's authorized task scope.

## Scope and validation

PostgreSQL remains the operational store. No broker, session injection, cron
scheduler, cross-host leadership, or exactly-once external effects is included.
The bindings launch fresh Codex, Claude Code, Agy, OpenCode and Hermes workers; model/provider
settings are fixed by each installed binding. Selected result text can be shared;
raw worker output remains local and is not committed or captured as memory.

Required evidence: concurrent claims; request filtering; held expired leases;
explicit completion versus exit zero; pre-launch backoff; stale renewal;
API/supervisor restart; process-tree termination; actual native harness launch
with a controlled provider; disposable database backup/restore and normal checks.
Deployment and measured usefulness must be reported separately.

## Binding and service installation

Create an owner-written JSON file using absolute paths:

```json
{
  "name": "opencode",
  "principal": "agent/opencode",
  "repo": "/home/OWNER/git/cairn",
  "socket": "/home/OWNER/.local/share/cairn/api.sock",
  "agent_token": "/home/OWNER/.local/share/cairn/event-profiles/opencode.token",
  "observer_token": "/home/OWNER/.local/share/cairn/hosted-observer.token",
  "directory": "/home/OWNER/git/cairn",
  "command": ["/absolute/opencode", "run", "--pure", "--auto", "--format", "json", "-m", "PROVIDER/MODEL"],
  "timeout_seconds": 600,
  "state_directory": "/home/OWNER/.local/share/cairn/wake/artifacts/opencode"
}
```

Fresh worker command shapes (model and profile choices belong to the binding):

| Harness | Noninteractive entry point |
| --- | --- |
| Codex | `codex exec --json --ephemeral --sandbox danger-full-access -c 'approval_policy="never"' -m MODEL --` |
| Claude Code | `claude --print --verbose --output-format stream-json --permission-mode acceptEdits --permission-prompts none --allowedTools Bash --model MODEL --` |
| Agy | `agy --model MODEL --output-format stream-json --print-timeout 10m --dangerously-skip-permissions --print` |
| OpenCode | `opencode run --pure --auto --format json -m PROVIDER/MODEL` |
| Hermes | `hermes --provider PROVIDER --model MODEL -z` |

These unattended launch permissions apply only to the configured worker
invocation. They do not grant new task authority or change interactive settings.
Codex and Claude bindings explicitly select the owner's personal configuration
homes through `/usr/bin/env`; no credentials are embedded in binding files.
Codex's [noninteractive contract](https://learn.chatgpt.com/docs/non-interactive-mode)
and Agy's [headless contract](https://antigravity.google/docs/cli/headless/)
were checked against the installed CLI help. Agy may exit zero after a tool
permission denial, so explicit Cairn completion remains the handling criterion.

The worker appends the prompt as one argument. The profile's destination must be
hosted; the observation token must have the observer role in the same collection.
The asserted principal is checked through the authenticated API, never used as
a caller-selected identity. One host lock covers the collection and principal.
Each profile has its own concurrency limit; bindings that share a workspace must
coordinate edits through their task instructions or use separate workspaces.

```sh
cairn wake check --config /absolute/binding.json
python3 scripts/install-wake-service.py --config /absolute/binding.json
systemctl --user status cairn-wake-opencode.service
journalctl --user -u cairn-wake-opencode.service
```

The installer copies the binding under the Cairn home, installs a user service
and enables it. Updating an existing binding stops the old service first. Disable
with `systemctl --user disable --now cairn-wake-NAME.service`. If the API is down,
an unfinished hold can remain; restoring service access lets startup reconcile it.
Worker stdout/stderr prefixes are retained locally, capped at 4 MiB each. Runner
artifacts include full-stream hashes and pending outcomes where applicable.
Neither worker logs nor source prompts are automatically captured as notes.

## Coordination API

All calls use the profile's normal authenticated Unix API; the server never
executes a command. `cairn agent --token-file FILE OPERATION` accepts these JSON
operations, with `--help` for examples:

- `wake-claim`: `{request_id, repo?}`. Atomically claims the oldest eligible
  request and creates an attempt whose UUID is `request_id`. A retry of a
  successful claim returns its current attempt; an empty poll has no durable
  effect. It may find work on a later call with that UUID.
- `wake-attempts`: `{repo?, active?, attempt_id?, after?, limit?}`. Inbox-owner
  inspection, up to 100 results, with an exclusive UUID cursor. UUID order is
  pagination order, not chronological order; use `created_at` for timing.
- `wake-change`: `{request_id, attempt_id, operation, receipt_id?, process_state?,
  reason?}`. Operations are `start` (record launch intent), `enter` (one worker),
  `link` (runner receipt), `report` (process label), and `finish` (host confirmed
  unit stopped). Identical mutation retries use the same request UUID. A distinct
  `enter` request cannot launch an already-entered attempt again.

These labels report coordination under the authenticated principal. They do
not upgrade agent reports to instrumented observations. The linked runner receipt
carries the independently authenticated process observations. `finish` is an
explicit host report, not a server-side check of systemd; agents must not call it
as a substitute for completing their delivery.

Migration 036 adds wake attempts and delayed delivery availability. Existing
manual claims now honor wake holds and retry delays. Stopping the supervisors
leaves ordinary inbox commands available; do not run an older executable that
ignores wake holds while workers or unfinished attempts exist.
