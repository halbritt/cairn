# CAIRN-227 long-source packet preparation, 2026-10-24

`scripts/stage_token_efficiency_long_source.py` prepares one actor packet for
each frozen `long_source` workflow arm. It verifies the canonical fixture,
private controller and actor exports, and the ordinary-task binding. The packet
contains 120 numbered, complete copies of `relay_history`, followed by one
complete `relay_current` source. Each section carries the full body, source
body hash, full source-record hash, and `source_extent: whole`. The separate
receipt binds the actor packet bytes with SHA-256 and labels the result
`prepared_unread`.

The staging output must be a new direct child of an owner-only disposable root
named `/tmp/cairn-postgres.*`; the existing private panel export must also be
under that root. The actor packet and receipt remain inside the disposable
root. The verifier rereads both files and compares the packet against the
current frozen fixture, including exact section order, content and extent.
It refuses changed source bytes, a changed extent, a changed receipt, or an
export that differs from the canonical fixture. The preflight pins the
`repeat_source` transition to the exact 120-copy historical plan.

Local checks:

```sh
python3 -B scripts/test_stage_token_efficiency_long_source.py
python3 -B scripts/test_preflight_token_efficiency_panel.py
python3 -B scripts/preflight_token_efficiency_panel.py
make check
```

These checks prepare and verify bytes only. No native actor was launched, no
tool or prompt payload was emitted to a model, no provider call was made, and
the 512 KiB live-limit answer was not graded. The hidden acceptance check
stays in `controller.json` and is absent from the actor packet. A future
runner must confine actor access to that packet and own the actual source-read
emission and native turn join before claiming exposure or an accepted paired
task.
