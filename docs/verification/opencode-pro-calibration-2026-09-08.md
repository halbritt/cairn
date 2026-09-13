# Hosted repair and diagnostic review — 2026-09-08

Trial L produced the first substantive model-authored repair in this campaign.
Its cache worked and was reclaimed, but the candidate violated the declared
file scope and introduced an environment-handling regression. It is not an
accepted repair, and this repository-only run establishes no memory benefit.

Three fresh runs used the existing DeepSeek V4 Pro binding, 131,072 context,
900 process seconds and 60 configured steps. Task, source, tools and original
gate remained fixed. Their executed controllers and relay sources were frozen
before launch; later source edits are not substituted for those identities.

| Trial | Changed condition | Observed result |
|---|---|---|
| J | Pro with initial $1 input / $2 output price ceilings per million tokens | Both requests returned HTTP 404; no model activity or patch. Binding outcome unknown. |
| K | Price ceilings $1.50 / $4; privacy controls unchanged | Eleven requests returned HTTP 200. No patch; last response exhausted its 8,192-token allowance. Process exited zero after 208.591 seconds. |
| L | Output allowance 32,768; response bytes 32 MiB and checked response duration 600 seconds | 57 requests returned HTTP 200, no relay refusals. Real edits; normal exit after 856.018 seconds. |

OpenCode requested 32,000 output tokens in L. Reasoning shares the response
allowance. The larger capacity is a grouped condition, not an isolated causal
test. Reported costs were $0.143702 for K and $0.438841 for L; provider telemetry
is not an invoice. A synthetic route diagnostic distinguished the initial
privacy-compatible routing refusal from task failure before the ceiling change.
Neither provider data-collection policy nor global account configuration was
relaxed.

## What the candidate did

It added lane-owned build and module caches beside the per-attempt working
directory, inside the dispatch workspace. It forwarded per-invocation environment
entries through new supervisor-helper markers and decoded them in `init.go`.
That file was outside the original two-file production whitelist. The controller
therefore retained a scope rejection without executing its behavioral gate.

The opted-in final explanation acknowledged that cgroup-dependent tests had
skipped inside the sandbox. After private review, that explanation was removed;
only its digest, size and reviewed findings remain. No raw model prose or patch
is committed here.

## Independent checks after the run

These diagnostics do not rewrite the original trial assessment:

- The original gate fails because it requires the cache below the runtime
  working directory. The task said dispatch workspace; a sibling cache can
  satisfy that lifecycle. A separately recorded gate using the actual dispatch
  workspace passes on L, as do its cache reclamation and relaunch tests under
  delegated cgroups.
- The two-package suite has one failure, `TestRelaunchPinsOrderedAttemptClosures`.
  The same empty rendering-contract assertion fails on the unmodified base.
  It is not attributed to this patch.
- A new diagnostic sets an ordinary environment value and an inherited helper
  marker containing a conflicting value, then launches `StartConfined` without
  any `Spec.Env`. The original implementation preserves the ordinary value;
  L replaces it with the marker payload. This is a reproduced behavioral
  regression, independent of the file-scope rejection and cache placement.

The [metadata report](opencode-pro-calibration-2026-09-08.json) retains exact
identities, settings, receipts, original assessments, diagnostic results and
private evidence locations. All three stores and relays stopped; native homes
and derived caches were removed. Selected historical sources and candidate
evidence remain private.

The next experiment should use a declared workspace-lifetime contract, allow
the relevant init seam, and protect inherited environment behavior. Preserve
J–L unchanged. The reproduced L regression can support a separately labelled
later-recurrence lesson, but no avoided failure or useful adaptation has yet
been observed.
