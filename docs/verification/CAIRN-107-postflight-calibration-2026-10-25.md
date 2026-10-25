# CAIRN-107 postflight calibration evidence — 2026-10-25

## Decision

The first weekly occurrence does not support a conservative percentage-based
whole-run capacity reserve. Keep `trial_capacity_adapter`'s absolute-unit
requirement. The next occurrence remains refused until authoritative capacity
evidence covers all 126 attempts and the whole slot window. The one-run override
from the completed occurrence does not carry forward.

This change adds a post-run read to improve future observation. It does not
admit, schedule, or execute another occurrence.

## First occurrence evidence

The private run data was inspected locally and only aggregate fields are
recorded here. The 2026-10-24 occurrence used three distinct subscription
homes, with 42 attempts per account. The retained preflight observed rounded
used-percent values of 36%, 68%, and 33%; each primary window was 10,080
minutes. Each account had a distinct reset anchor. Secondary windows were
absent and ordinary usage was allowed. The receipt retained the preflight, but
not a post-run rate-limit snapshot.

The trace contains provider-reported input, cached-input-subset, and output
token counters for 21 completed turns per account and arm. Cached input is
already included in input tokens; it must not be added again. The trace does
not establish request count, quota units, monetary cost, percentage rounding,
or a mapping from tokens to quota consumption. Cost fields are null. A separate
issue audit's aggregate pre/post percentage comparison does not have matching
per-account reset identities in the retained run receipt. Thus there is no
sound empirical upper bound or exact refusal threshold to use for a percentage
adapter.

## New observation behavior

After the selected executor exits, the runner performs one read-only
`account/rateLimits/read` through each selected account's pinned Codex
installation. It requests no model catalog and makes no inference call. The
receipt retains only the selected account identity, observation timestamp,
ordinary-usage flag, and normalized primary and secondary window values
(used-percent, duration, reset). It does not retain the raw response, auth
bytes, or process output.

If the read fails, its shape is invalid, or a returned account identity does
not match the selected account, the row stores `status: null` and a fixed
`codex_subscription_status_unavailable` error. It does not retry. Postflight
data never affects capacity, acceptance, or terminal run status, including on
handled executor failure. Stored receipts replay exactly; the completed first
occurrence is unchanged.

## Validation

Focused local validation covered status-only RPC behavior, normalized windows,
identity mismatch, unavailable status, one observation per selected account,
status independence, and stored receipt replay. No provider inference call,
timer enablement, grader retry, or campaign rerun was made.
