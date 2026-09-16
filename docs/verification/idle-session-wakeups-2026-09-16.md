# Automatic idle-session wakeup verification — 2026-09-16

Status: implemented and tested; production deployment and automatic native
exchange remain pending. The earlier ai-newsroom trial used a manual host prompt
and is not acceptance evidence for this feature.

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

## Remaining acceptance

Deploy the matching clean CLI/API and shared watcher, enable the existing account
bindings, and verify service state. Publish a request to the actual idle
ai-newsroom UUID and observe automatic handling and an explicit reply with no
manual terminal prompt. Read the selected result and verify native hold release.
