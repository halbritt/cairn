# Automatic idle-session wakeup verification — 2026-09-16

Status: implemented, tested, deployed and verified in the existing ai-newsroom
conversation. The earlier manual host-prompt trial remains a separate historical
observation.

## Implemented behavior

The existing watcher reads `session-inbox-ready`, verifies a unique idle,
unfocused Herdr conversation against its actual foreground process and native
session, retains an uncertain prompt marker, then submits a host wakeup. The
native hook remains the sole delivery claimant. The
[contract](../plans/idle-session-wakeups.md) describes host races, focused-pane
deferral, project exclusions and conservative submission recovery. Installation
adds `--idle-wakeup` alongside `--native-delivery`; the API needs the matching
readiness endpoint. There is no schema migration.

## Checks

The first tests failed for absent readiness, absent host prompting, expired
presence incorrectly remaining eligible, focused-pane prompting, and an opt-out
added after registration. The corresponding implementation passed those checks.

`make test-integration` completed on its disposable PostgreSQL cluster, including
race-enabled Go packages and real CLI/API/supervisor probes. `make check` passed.
The final Python suite passed 121 tests. It covers process/session mismatch,
foreground ownership, changed host state, focused/busy/blocked/unknown targets,
lost prompt response, suppression across watcher restarts, a following delivery,
project opt-out and Codex's open native-session filename.

The real CLI/API probe with a host protocol fixture observed one automatic host
submission while the delivery stayed pending with zero attempts. A subsequent
real native hook acquired it, explicit completion succeeded, and later watcher
polls did not prompt again. This exercises PostgreSQL and hook ownership but uses
a scripted Herdr response, so it does not prove live terminal wakeup.

Selected logs: `/tmp/cairn-idle-integration.log`,
`/tmp/cairn-idle-check-final.log`, `/tmp/cairn-idle-python-final-2.log`, and
`/tmp/cairn-idle-wake-api-3.log`. Failed-before-change logs use the
`/tmp/cairn-idle-*-red.log` names. Raw native transcripts and credentials are not
retained in the repository.

The offered agent's architecture and code reviews were read and their replies
acknowledged. Code review result `5a4f99fe-0a94-4776-b039-5da336d7cf52/1` identified
an unrelated closed-pane observation aborting target discovery. A regression
failed before the fix; recognized missing-target errors now skip that candidate.
Other host errors remain visible. The reported unhandled process-exit exceptions
were already caught as `OSError` per session in `watch_once`; no daemon-wide
failure or bypass was found. Native PID/conversation identity is checked; the
review's claim of a CWD equality check was inaccurate and is not accepted.
The [decision receipt](../plans/idle-session-wakeups-review.json) records the
selected doctrine, evidence, alternatives and remaining nonmaterial obligations.

## Deployment

Clean CLI/API `e81057b4ea7ba04fe6e5eb0b20a961cf77ab9715` and the matching shared
watcher were installed at 15:10 UTC. No schema change was needed; migration 047's
digest remained unchanged. A PostgreSQL backup was taken and its archive
inventory checked before rollout. Preflight found no unfinished native or
fresh-worker holds. The API, presence watcher, scheduler, seven worker services
and Hermes gateway were restarted and verified active with live processes.

All seven account bindings have native delivery and idle wakeup enabled. Cairn's
event guide from skillpack `29e637f` matches across both Codex homes, both Claude
homes, OpenCode, Agy's two skill locations and Hermes. The installed shared
adapter matches the committed source. The busy Rhumb session was not restarted.
Selected deployment observations are in `/tmp/cairn-idle-installed.json`.

## Automatic ai-newsroom exchange

Before publication, Herdr reported `wX:p1` done and unfocused. Fresh selectors
`harness=codex, project=ai-newsroom` resolved only `agent-7`, UUID
`ef65d567-aa80-4751-966a-f2730ef42b13`, execution
`79a22ef5-a7f2-433d-bdbf-2f40bccf4029`, with idle native delivery. Its open session
filename matched conversation `01a0a837-a74a-73e3-a1eb-b06611f74737`; no transcript
body was read. The existing process 1266149 was already using the native hooks.

At 15:15:31 UTC the parent published request
`129faeac-923f-45da-ad24-84031c9cebfe`, source
`6d8210ce-8845-48eb-a452-07e6bd26bd70/1`, delivery
`b276bf96-061a-4622-95f9-9755554f8ed5`. Publication revalidated context revision 6.
The bounded request asked for identity and branch/HEAD verification, explicit
completion and a reply, then return to idle. It authorized no project edits or
editorial jobs.

The parent sent no terminal prompt and did not manually run the watcher. The
running presence service supplied the wakeup. Native attempt
`9d976a22-d750-434b-a333-8b49f258a205` started at 15:15:56 UTC, approximately
25 seconds after publication. This is one observation, not a latency guarantee.
The delivery was claimed once and completed at 15:16:34 UTC with result
`edc1a06b-0af7-4e74-bbcd-fcd10925ef26/1`.

The parent read that exact result. The recipient confirmed matching native inbox
context, conversation and agent/execution IDs. It reported branch `main` and
HEAD `2ab8e592707684a043051ae9c8162e96caedd77e`, which the parent independently
verified. The recipient distinguished its own observed context from the external
submission mechanism and reported no project changes or editorial work.

Explicit response `2e067cfe-6fd7-4449-a249-10172d745df8` filled the 1-of-1 response
group at 15:16:48 UTC, before its deadline. The native hold finished at
15:16:53 UTC with `turn_ended`; operator review showed `held: false` and no
fresh-worker attempt. The parent acknowledged the read reply at 15:17:24 UTC.
Herdr returned to `done`; native PID, start time, boot identity and conversation
remained unchanged. Selected observations are retained locally in
`/tmp/cairn-ai-newsroom-automatic-acceptance`.

This verifies automatic idle wakeup, native handling and reply collection for
this installed Codex conversation. Other harnesses share the deployed adapter
and tested protocol contract; this trial does not establish live automatic
wakeup in every harness/account. Herdr's final check-to-submission race and
unobserved draft text remain the documented host limitations. No broad task
productivity claim follows from this read-only exchange.
