# Codex Luna weekly executor: source verification

Status: implemented and synthetically verified, 2026-10-24. This adds a
Codex-specific route for the existing CAIRN-107 weekly plan. It does not run
the provider or establish permission, quota headroom, or runtime admission.

## Preserved contract

The weekly plan remains 21 cases × 2 arms × 3 seeds = 126 attempt identities,
with 1900 distractors. The executor uses the selected Codex auth file for both
arms and runs at `--parallel 1`; this keeps calls sequential on the one selected
subscription account. It pins the Codex executable SHA-256, `gpt-6-luna`, and
reasoning effort in `cairn.weekly-execution/2`. The live selection additionally
binds the account id, auth-file path and SHA-256, plus provider model/binary
hash. Immediately before execution the auth file is parsed without exposing
token values; its hash must match, `auth_mode` must be `chatgpt`, and a
non-empty `OPENAI_API_KEY` field is rejected. The file is mounted read-only
into an isolated Codex home. The invocation explicitly sets
`model_provider="openai"`; the runner removes API-key and OpenRouter
environment variables before launching the harness. The harness rechecks the
digest and auth mode before store startup and immediately before each Codex
process launch, so changed or API-key-mode credentials fail closed.

The legacy Claude route remains `cairn.weekly-execution/1` and
`--original-limits`. Codex uses `--codex-weekly-limits` and a separate parser
for Codex JSONL events. A result counts only when its MCP payload is complete,
the call status is complete, hook observations exist, stream timing matches
each captured line, and the stream ends in `turn.completed`. Unknown schema or
missing evidence is unknown and fails the weekly measurement gate. Both paths
use the same original CAIRN-107 limits values and the same task-wire charge;
Codex is not treated as a Claude stream.

## Verification performed

Synthetic tests exercise the version-2 provider contract, account path binding,
executor arguments/environment, sequential setting, and Codex event/limit
assessment. They make no provider calls. The CLI parser smoke check is:

```sh
PYTHONPATH=scripts python3 -B scripts/trial_weekly_run.py --help
```

The end-to-end admission command remains the installed runner entry point:

```sh
python3 scripts/trial_weekly_run.py --profile /path/to/weekly-run.json
```

That command is intentionally not issued by this source change. A launch still
requires a valid, pinned capacity receipt for all 126 attempts and the exact
selected account, an absolute allowance observation that covers the slot, and
a pinned live selection for the exact pair/harness/slot. The Codex subscription
probe recorded separately reported weekly usage at 7% but supplied no absolute
token allowance. That percentage is not evidence of capacity, and no candidate
evaluation has been launched.
