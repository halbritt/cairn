# Candidate-branch paired run requests

`scripts/trial_candidate_request.py` lets a candidate branch request a weekly
paired run with one command. It needs a standing operator profile, which is
set up once. It builds both arms, declares the disposable environment, checks
usage, and queues a reviewable draft through the existing
[weekly draft producer](weekly-request-draft-v1.md). It starts no provider,
model, actor or store. It installs no timer.

```sh
python3 scripts/trial_candidate_request.py request --candidate feature/my-recall-change
python3 scripts/trial_candidate_request.py list
python3 scripts/trial_candidate_request.py prepare REQUEST_ID \
  --dispositions /control/dispositions.json --dispositions-sha256 SHA256 \
  --companion /control/companion.json --companion-sha256 SHA256 \
  --review /control/companion-review.json --review-sha256 SHA256
python3 scripts/trial_candidate_request.py launch REQUEST_ID   # always refuses
```

`--profile PATH` selects a profile. The default is
`~/.config/cairn/paired-candidate.json`. Add `--smoke-postgres` to `request`
to prove that the configured PostgreSQL binaries can create, query and remove a
disposable cluster before the request is queued. Output is one JSON object.
The exit code is 0 for success and 2 for refusal. Refusals report fixed codes
without paths. A usage refusal also includes the path-free pre-check summary.

## What one request does

1. Validates the closed profile and the private (mode 0700) queue directory.
2. Resolves `current_ref` and the candidate ref to full commits in `repo`.
   It refuses an unresolvable ref, an option-like ref, or a candidate equal to current.
3. Selects the next weekly occurrence: the first `schedule` slot (UTC weekday
   and `HH:MM`) after now, lasting `window_minutes`.
4. Derives the request, occurrence, reply event and correlation UUIDs with
   UUID5 from campaign, occurrence start, candidate commit and current commit.
   If that pair is already queued for the occurrence, it returns the stored
   receipt with `status: already_queued` and does not rebuild or write anything.
5. Runs the **usage pre-check** before any build or queue write (see below).
6. Creates `QUEUE/REQUEST_ID/` and builds both arms from `git archive` of the
   exact commit. No refs, hooks or worktree state are copied. Each arm pins
   `bin/cairn` (`go build -trimpath -buildvcs=false ./cmd/cairn`; the request
   pins the commit, because the export has no VCS stamp),
   `integrations/lifecycle/memory.py` as the hook and `inbox_recall.py` as the bridge.
7. Writes the `cairn.weekly-execution/1` sidecar: CURRENT and CAND arms with
   their own commits as API and recall revisions, and the profile's recall mode
   and the same embedding worker/config/model identity for both arms.
   `actor_api_base` is the current commit. The frozen cases, seeds,
   native declaration, workload and fixed 1900-distractor generator come from the profile.
8. Writes `environment.json`: a disposable cluster under `REQUEST_ID/cluster`
   (not created; it has the same flags as `WeeklyCluster`, socket-only, `listen_addresses=''`),
   plus the embedding pins and arm binaries. The profile accepts only a
   PostgreSQL binary directory. It cannot name a DSN, host or existing data directory.
9. Writes the `cairn.weekly-eval.proposal/1` and calls `trial_weekly_draft.draft`.
   The draft rebuilds and checks the frozen 21-case bundle, the 126-row schedule,
   capacity coverage of the occurrence, grading, supervisor and permission pins.
   It then emits `draft/{schedule,request-core,review-subjects}.json`.
10. Writes `entry.json` with `state: queued_for_review` and the receipt,
    including the draft file hashes.

Any refusal after step 6 leaves the entry with `state: failed` and its code.
A later request for the same pair moves it to `QUEUE/failed/REQUEST_ID.N` and
tries again with the same IDs. An entry without `entry.json` is still being
written and refuses `candidate_request_in_progress`. `list` shows queued,
prepared, failed and incomplete entries.

## Preparing a reviewed entry

`prepare` joins a queued draft to its reviews through the unchanged
[weekly request](weekly-request-v1.md) and [companion](paired-companion-v1.md)
contracts. It rereads `draft/request-core.json` and `draft/schedule.json` and
refuses `candidate_draft_changed` unless their hashes equal those recorded at
queue time. It adds the six disposition records from the pinned
`--dispositions` file to the core. It then runs `trial_weekly_request.check`
with the explicitly selected companion and review pins. A missing pin refuses
`weekly_companion_required`. A stale, late or mismatched one refuses in the
companion validator. Both refusals happen before `QUEUE/registry` is created
and before any hold or output is written. Only then does it call
`trial_weekly_request.prepare` with `QUEUE/registry` (mode 0700). That call
writes the occurrence and request holds and `REQUEST_ID/preparation/`. The
command records the three input pins and the preparation receipt in
`prepared.json`. Preparation must run inside the occurrence window, because
`scheduled_at <= now < expires_at` still applies. The weekly request/1 and paired
request/2 boundaries stay distinct. This command never uses request/2 or
supplemental fresh tasks.

## Usage pre-check

The profile pins a `cairn.weekly-usage-observation/1` from the last run:

```json
{"schema": "cairn.weekly-usage-observation/1", "source_run": "2026-10-14 weekly",
 "roles": {"primary_actor":   {"attempts": 63, "tokens": 5000000, "requests": 900},
           "secondary_actor": {"attempts": 63, "tokens": 5100000, "requests": 910},
           "primary_grader":  {"attempts": 63, "tokens": 800000,  "requests": 63},
           "secondary_grader":{"attempts": 63, "tokens": 820000,  "requests": 63}}}
```

Actor roles are the CURRENT and CAND actors, with 63 attempts each. Each
grader role is budgeted for all 63 pairs. The required amount for each role is
`ceil(observed / attempts * 63 * margin_percent / 100)`. The live quota snapshot at
`usage.quota_snapshot` must be `cairn.provider-quota-snapshot/1`, with
`observed_at` no older than `max_age_seconds`. It must have absolute
`remaining_tokens`, `remaining_requests` and `http_429_active` for all four
roles, and it may have the same fields under `weekly_pool`. A missing, stale,
future-dated or percentage-only snapshot refuses `usage_snapshot_unavailable`.
The gate is the existing `check_weekly_paired_eval.check_usage_precheck`. Any
shortfall refuses `usage_precheck_refused`, and nothing is built or written.
The snapshot is operator-supplied evidence. This code does not query a provider
or prove that the account is dedicated, and the provider-cap stop remains the backstop.

## Profile

`cairn.paired-candidate-profile/1` is closed and has exactly these fields:

| Field | Contents |
| --- | --- |
| `repo`, `current_ref` | Absolute Cairn checkout and the current-build ref (for example `main`). |
| `queue` | Existing absolute private directory that receives request entries. |
| `campaign_id` | UUID shared by the weekly campaign. |
| `weekly_source` | `root`, `label_version`, `frozen_sha256` of the frozen cases. |
| `native`, `seeds`, `workload` | As in the [weekly input bundle](weekly-input-bundle-v1.md). |
| `embedding` | `worker`, `config`, `model_identity` file pins used by both arms. |
| `recall` | `recall_mode` (`ambient`/`agent_tools`) and `semantic_fallback`. |
| `postgres` | `bin_dir` containing `initdb`, `pg_ctl`, `postgres`, `psql`. |
| `schedule` | `weekday` (0 = Monday), `time` (`HH:MM` UTC), `window_minutes`. |
| `proposal` | `capacity`, `grading`, `supervisor_source`, `permission_profile` and `reply_recipient_id`, as in the weekly proposal. |
| `usage` | `observation` pin, `quota_snapshot` path, `max_age_seconds`, `margin_percent` (100–1000). |

The capacity declaration must cover the occurrence window. The draft producer
refuses `draft_capacity_interval` otherwise. The plan hash does not depend on
the arm builds, so one capacity declaration can serve every candidate in that window.

## Launch and what remains

`launch` delegates to `trial_weekly_request.launch` and refuses
`provider_execution_unavailable` before it reads anything. Every request receipt
reports the same refusal under `launch` and `runtime_admitted: false`. A queued
entry does not authorize execution. Independent reviewers must still accept the
six subjects in `draft/review-subjects.json`. A companion and its review must
be selected. `trial_weekly_request.py check/prepare` must pass. A supported
native launcher, dedicated account capacity, blind second grading and two
complete weekly campaigns remain separate CAIRN-107 work.

## Verification

```sh
cd scripts && python3 -B -m unittest -v test_trial_candidate_request
```

The tests use an owned temporary git repository and a fake compiler. They use
synthetic frozen weekly sources from the weekly request fixture, its synthetic
provider companion and file-based quota snapshots. These synthetic inputs prove
the source contract only. They do not show that real weekly cases, accounts or
reviews are compatible. The PostgreSQL smoke test creates and removes its own cluster
when `/usr/lib/postgresql/16/bin` exists. No provider, model or shared database is used.
