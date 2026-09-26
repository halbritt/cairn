# Reading plans and decision receipts

The Markdown plans in this directory are working design documents; their status
lines and tables are updated as slices ship. The `*-review.json` files are
decision receipts. Each one records the question, evidence, decision, residual
risk and reopening conditions as reviewed when it was committed. They are not
edited to track later work, and their `content_sha256` values identify the
bytes reviewed then, not the current files.

Read a receipt as dated evidence for its decision. For the present contract,
use the linked plan or guide, [implementation status](../implementation-status.md)
and current source. The [verification guide](../verification/README.md)
explains the same limits for `/tmp` locators and recorded digests.

## Later changes to specific receipts

These notes were added on 2026-09-25. They do not change the recorded
decisions, outcomes or hashes.

- [Coordination v1](agent-coordination-v1-review.json) (2026-09-15). Its
  residual risk says directory routing and existing-session delivery are
  planned, not deployed. The [plan](agent-coordination-v1.md) now records
  resolution in migration 038, native turn-boundary delivery in 039 and
  fresh-worker conversation links in 040. The receipt's own
  `trusted-host-correction` assertion keeps the trusted-host model without
  per-session credentials; read its earlier "authenticated instances" wording
  in that light.
- [Request controls](request-controls-review.json) (2026-09-16). It refuses
  active native and manual cancellation until a host provides per-request
  interruption and completion observation, which is one of its reopening
  conditions. Migration `049_native_request_control.sql` and the
  [OpenCode trial gate](../request-controls.md#native-interactive-cancellation-opencode-trial-gate)
  added a gated native path for attested OpenCode turns. That path stays
  disabled in the installed configuration; the
  [2026-09-24 trial](../verification/opencode-cancel-trial-2026-09-24.md) ran
  against a disposable store.
- [Provider failures](provider-failure-review.json) (2026-09-16). Its residual
  risk says Agy wording remains unclassified. The later
  [Agy receipt](agy-provider-failures-review.json) and
  [report](../verification/agy-provider-failures-2026-09-16.md) classify Agy
  stream-json HTTP 429 and `RESOURCE_EXHAUSTED`; see
  [provider failures](../provider-failures.md) for current coverage.
- [Agy provider failures](agy-provider-failures-review.json) (2026-09-16). Its
  hashes for `internal/wakeup/provider_stream.go`, `docs/provider-failures.md`
  and `scripts/check_worker_pools.py` predate the
  [Claude receipt](claude-provider-failures-review.json), whose repair changed
  `provider_stream.go` in commit `1846f95`, and later edits.
- [Claude provider failures](claude-provider-failures-review.json)
  (2026-09-16). The `cairn-claude-tests` summary says the corrected native
  probe "is tracked separately until its process returns" and then that it
  passed. Read the second statement as the later result. The two runtime log
  entries share one digest because both logs held the same list of eight
  check labels (the files were byte-identical on the original host on
  2026-09-25), so the digest does not distinguish the runs. The
  `cairn-claude-native` evidence record has an empty `satisfies` list although
  the `reproduced` assertion cites it; the receipt does not say which criteria
  it was meant to meet.
- [Idle-session wakeups](idle-session-wakeups-review.json) (2026-09-16, commit
  `e81057b`). Its `implementation-checks` assertion says the checks do not
  establish the pending production trial, and it has no evidence record for
  that trial. The trial was recorded afterwards in commit `77f14da`; see the
  [plan](idle-session-wakeups.md) and
  [trial report](../verification/idle-session-wakeups-2026-09-16.md). The
  receipt's plan hash (`1b450b28…`) identifies the plan before that commit and
  before the later Codex and Claude native wakeup sections.
