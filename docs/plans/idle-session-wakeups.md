# Automatic wakeups for existing idle sessions

The owner's September 16 continuation requires a message to wake an existing
idle conversation without another agent or the owner sending a terminal prompt.
The manual ai-newsroom trial does not satisfy this requirement. This extends
coordination v1 milestone 4. Clean runtime `e81057b` is deployed and the real
[automatic ai-newsroom exchange](../verification/idle-session-wakeups-2026-09-16.md)
completed with an explicit reply and released native hold.

## Delivery contract

The existing presence watcher checks for eligible inbox work during its 30-second
cycle. PostgreSQL remains the authority for session generations, delivery state,
leases and holds. `session-inbox-ready` is a read-only, bounded hint: it returns
the first eligible delivery UUID only for a current, online, idle existing
session with no competing native/wake hold or live manual lease. It creates no
poll history or delivery attempt. Eligibility uses the same selection conditions
as the native claim, including admission expiry and delayed availability.

The watcher checks the native process's own Herdr socket. It requires a unique
matching harness and conversation, an unfocused idle/done pane, the observed PID
in its foreground process group, and matching PID start time/boot identity.
Herdr's native session ID identifies Claude, Agy, OpenCode and Hermes when their
installed integrations report it. For Codex, which currently omits that field,
the unique open rollout-file UUID supplies the association. No transcript body
is read. Missing or ambiguous evidence causes deferral, never a replacement
conversation or a guessed pane.

Immediately before submission the watcher rechecks pane/terminal identity,
revision, state sequence, process ownership and Cairn readiness. It writes an
owner-only `idle_wake` marker containing the session, delivery UUID, target,
timestamp and `uncertain` status before invoking Herdr. This is host retry state;
the delivery itself stays pending in PostgreSQL. The marker suppresses another
submission for that delivery/execution across watcher restarts. A confirmed host
response changes it to `submitted`; native inbox context clears it when handling
actually starts. An uncertain submission is not retried on a timer.

The per-session lock is released before terminal submission so the new native
hook can acquire it. The prompt contains session identifiers and asks the agent
to handle only its supplied native context. It carries no source body or work
instructions. The native hook alone claims and renews the delivery, writes the
context and requires explicit completion/acknowledgment. Replies remain explicit.
Subsequent messages wait for another eligible idle cycle; no polling thread
drains the entire inbox or interrupts a busy turn.

## Host limitations and recovery

Herdr 0.9.0 has no atomic idle-only prompt or native composer reservation.
Snapshot checks cannot prevent a user action or process change in the final
interval before terminal submission. Deferring focused panes and verifying both
hook state and foreground ownership reduces exposure; it does not establish
an atomic guarantee or prove an unfocused composer contains no draft. The
transport is a trusted-host advisory wakeup, with native claiming still owning
message execution. It sends no interrupt, exit or input-clearing keys.

Unavailable Herdr/API, busy/blocked/unknown state, another foreground owner and
unavailable native identity leave work queued. An API restore fences the old
execution and refuses readiness; the watcher never resumes it to bypass fencing.
An uncertain submitted wake remains suppressed until a real native boundary
handles work. Inspect the selected `idle_wake` status in the session's protected
host state and `cairn coordination-review`; a genuine user prompt can create the
missing boundary. Do not republish the request or blindly remove the marker.

Sessions outside Herdr retain supported boundary delivery. Gateway conversations
without an idle interactive process are not terminal wake targets. Account homes
remain separate bindings; no new session credentials are introduced.

## Verification

- Disposable PostgreSQL checks: readiness is read-only; ownership, expiry, busy
  state and replaced execution prevent wake hints.
- Protocol fixtures with real process references: one submission across watcher
  restarts; lost reply suppression; focused/busy/blocked/unknown and changed
  target refusal; Codex native UUID matching; subsequent delivery progress.
- Real CLI/API/native hook probe: pending work causes one automatic host call,
  remains unclaimed until the hook, then completes once.
- Required deployment trial: publish to the actual idle ai-newsroom session and
  observe automatic wake, native handling, explicit reply and released hold with
  no manual terminal prompt. Review both success and installed service state.

The offered agent's review result `75627820-7f60-4a54-a076-75c9623b04cb/1` was read.
Its process/foreground, dual-state and restart concerns inform these checks.
Its proposed timed retries are rejected because elapsed time does not resolve
submission uncertainty. Holding the session lock through prompting is rejected
because the prompt hook requires that lock. The existing 30-second watcher cycle
bounds successive background nudges; an additional queue-depth prompt is not
needed for the one-delivery-per-turn contract.
