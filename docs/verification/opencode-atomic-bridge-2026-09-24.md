# OpenCode atomic inbox bridge candidate, 2026-09-24

This candidate replaces the OpenCode bridge's separate status check and
`promptAsync` submission with the patched native `prompt_idle` operation.
The delivery UUID is its stable native `requestID`. A `busy` result clears the
unadmitted wake for a later idle retry; conflict remains a retained refusal.
An unknown outcome after submission is retained as uncertain. The plugin waits
for admission before passing the exact request
and delivery IDs with OpenCode's observed user-message ID to the lifecycle
hook. That hook makes the native inbox claim with the exact delivery and turn ID;
it sets `turn_exclusive: true` only when the wake is `cancel_capable`.
OpenCode owner prompts cannot claim this inbox
while the native wake route is enabled.

The watcher probes a peer-verified bridge for `prompt_idle` support before
attempting an automatic wake. A process with an older plugin or SDK keeps
ordinary owner-prompt delivery. Definite missing-method responses clear a
prepared wake, so an upgrade in progress does not strand the delivery. Native
`completed`, `cancelled` and `failed` replies for the same idempotent request
mean it was already admitted. They stop automatic replay and pin the still
pending delivery to the next owner-turn claim without asserting exclusivity
for that owner turn. This recovery needs a future owner prompt; it is not a
second automatic wake.
The lifecycle hook matches the request and delivery IDs supplied only after
the plugin has matched the admitted native message. It does not regenerate
the wake text, so an in-flight request survives a watcher text change.

For an `uncertain` OpenCode wake, record the delivery and request UUIDs and
the process identity from the session state in the configured `state_dir`.
Inspect Cairn's delivery status, the native session's user-message history,
and any native inbox context for that delivery. If the admitted turn reaches
its hook, normal claim and completion clear the marker. Until admission or
non-admission is established, leave the marker intact: do not resend the
request, delete the state entry, or restart/resume the session as a recovery
shortcut. A process restart loses its in-memory idempotency record, so this
candidate provides no automatic safe retry after an uncertain process exit.
Escalate unresolved cases for an explicit operator disposition.

The source depends on the patched OpenCode `prompt_idle` API from the approved
CAIRN-3 source at `f0ef1c0`. The installed OpenCode process and plugin have not
been changed. Native `cancel_request` remains unavailable to Cairn until
CAIRN-2 verifies host-observed interruption and cleanup.

Local checks on this source candidate:

- `OPENCODE_TEST_BINARY=.../packages/opencode/dist/opencode-linux-x64/bin/opencode python3 -m unittest scripts.test_opencode_queue -q`: 32 tests passed, including the patched binary's isolated request schema, atomic busy retry, compatibility fallbacks, idempotent replay recovery, uncertain error classification, hook attribution and exact exclusive claim payload.
- `make check`: passed.
- `make test`: Go packages passed and 339 Python tests passed (15 skipped). This does not run database integration without a disposable test cluster.

These checks establish the bridge contract and local fixtures. They do not
establish a real model turn, a live installed-session admission, interruption,
or the owner-visible smoke of Esc, quit, idle reload and idle-only wake. Review
the candidate before installing the paired binary and plugin. The combined
install and live smoke belong to the CAIRN-1/CAIRN-3 installation decision.
