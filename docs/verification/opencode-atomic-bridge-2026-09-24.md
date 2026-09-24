# OpenCode atomic inbox bridge candidate, 2026-09-24

This candidate replaces the OpenCode bridge's separate status check and
`promptAsync` submission with the patched native `prompt_idle` operation.
The delivery UUID is its stable native `requestID`. A `busy` result clears the
unadmitted wake for a later idle retry; conflict and other definite refusals
remain visible without replay. An unknown outcome after submission is retained
as uncertain. The plugin waits for admission before passing the exact request
and delivery IDs with OpenCode's observed user-message ID to the lifecycle
hook. That hook makes the native inbox claim with the exact delivery, turn ID
and `turn_exclusive: true`. OpenCode owner prompts cannot claim this inbox
while the native wake route is enabled.

The source depends on the patched OpenCode `prompt_idle` API from the approved
CAIRN-3 source at `f0ef1c0`. The installed OpenCode process and plugin have not
been changed. Native `cancel_request` remains unavailable to Cairn until
CAIRN-2 verifies host-observed interruption and cleanup.

Local checks on this source candidate:

- `OPENCODE_TEST_BINARY=.../packages/opencode/dist/opencode-linux-x64/bin/opencode python3 -m unittest scripts.test_opencode_queue -q`: 25 tests passed, including the patched binary's isolated request schema, atomic busy retry, conflict retention, hook attribution and exact exclusive claim payload.
- `make check`: passed.
- `make test`: Go packages passed and 331 Python tests passed (15 skipped). This does not run database integration without a disposable test cluster.

These checks establish the bridge contract and local fixtures. They do not
establish a real model turn, a live installed-session admission, interruption,
or the owner-visible smoke of Esc, quit, idle reload and idle-only wake. Review
the candidate before installing the paired binary and plugin. The combined
install and live smoke belong to the CAIRN-1/CAIRN-3 installation decision.
