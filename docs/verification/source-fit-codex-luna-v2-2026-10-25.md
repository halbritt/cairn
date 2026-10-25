# CAIRN-223 Codex/Luna panel v2, 2026-10-25

## Decision state

This is a separate manifest correcting the Codex tool-isolation contract in
[`FROZEN.json`](../../trials/source-fit/codex-luna-2026-10-25-v2/FROZEN.json).
The landed v1 manifest remains unchanged. V2 keeps its six cases, all 15 arms,
account assignment, synthetic source bodies, task wording, and grading rubric.
The v2 status remains `admission_pending_runner_preflight`; it records no panel
attempt and makes no provider call.

V1 froze `code_mode_only`, `code_mode`, and `shell_tool` as disabled, then
overstated what its tool-omission preflight established. The provider-free
fake-auth loopback Responses fixture declares generic `exec` in its request;
the fake runtime rejects the attempted call with `code_mode_host=false`. This
is evidence about that local request and fake runtime only. It is not evidence
of the subscribed provider's offered-tool inventory. A registry listing or
request-shape proxy also cannot establish that inventory.

V2 adds `--disable code_mode_host`. Its effective-state check is the exact
`features list` invocation below; all four values must be false. The
independent bwrap request/call proof must also fail closed with the recorded
runtime denial. A flag alone is not proof of effective state. This is local
fake-auth evidence only: the request declared generic exec, and the fake call
failed closed. It must not be summarized as “exec was not offered.”

The route-audit fixture used this provider-free local command shape:

```sh
codex exec --ephemeral --json --ignore-user-config --ignore-rules \
  --skip-git-repo-check --cd /work --sandbox read-only --model gpt-6-luna \
  --disable code_mode_only --disable code_mode \
  --disable code_mode_host --disable shell_tool
```

It used `approval_policy="never"`, medium effort, and a custom loopback
fixture provider. The blank-root bwrap mounted only a synthetic `CODEX_HOME`
with a fake sentinel, `/work`, the Codex package, and system runtime. The stub
returned one `functions.exec` call that attempted to read the synthetic auth
sentinel. Codex emitted a completed error item with
`Code Mode is unavailable because code-mode host is disabled`, then returned
a normal agent message and completed turn after the stub's second response.
There were two loopback requests, no sentinel in tool output, the second
request, or stdout, and zero external calls. A separate direct-arm shape probe
made one request with no Cairn declarations; the generic `functions.exec`
declaration list was unchanged. Its observed request-shape digest was
`1b536a8b13d728340d89487f4a3c35045ba6fa848b8288e6a15e986ee9b5ab7a`; the raw
request/stream and a persistent evidence artifact were not retained. These
results support the local runtime-denial statement only.

## Unchanged panel and route limits

V2 is derived from v1 SHA-256
`0a4e44eb95ce2a26a48d9731a87fef1d75bf3b2d9958925f70ffb226a323f5bf`.
The v2 manifest SHA-256 is
`dffecf6c44de456354d0dbe254704717a2ca0231c291b212f775f0fd49d57c85`.
Validate the v1 and v2 manifests independently. The 15 arms remain assigned by
case to the same three existing homes: `codex_primary` (`~/.codex`),
`codex_harm` (`~/.codex-harm`), and `codex_three` (`~/.codex-3`). Preserve the
exact home/auth-file digest and distinct account identity pins. Never copy or
symlink auth into a temporary `CODEX_HOME`.

The subscription route remains Codex CLI 0.164.0, explicit
`model_provider="openai"`, requested alias `gpt-6-luna`, medium reasoning, and
no API-key or OpenRouter fallback. Before each attempt, re-read the selected
home's account identity, auth digest, `account/rateLimits/read`, and
`model/list`. Require matching identity, `ordinaryUsageAllowed=true`,
`limitId=codex`, the requested model present, and primary usage below 80%.
Stop on any route, account, auth, quota, model, or state mismatch; never move a
stopped attempt to another home.

The effective-feature check is:

```sh
codex --disable code_mode_only --disable code_mode \
  --disable code_mode_host --disable shell_tool features list
```

Require all four effective values to be false, and bind that observation to
the exact CLI, account home, and frozen flags. The bwrap fake-call denial is a
separate check of runtime fail-closed behavior.

Keep the sequential ceilings unchanged: 15 attempts, 180 seconds per attempt,
3,600 seconds total, 125,000 reported tokens per attempt, 2,000,000 total,
1,200 output tokens per attempt, and a 125,000-token reserve before each
admission. Require `observed_total + 125,000 <= 2,000,000` before launch.
Whole-turn usage is deduplicated by turn; cached input is already included in
input; per-response usage remains unknown unless a native event exposes it.
Missing or malformed terminal usage, model mismatch, a failed tool result,
limit breach, or failed task-evidence parse fails or leaves the attempt
unscored and stops further admission.

Keep the strict bwrap gate: fixed `/work` CWD, exact pinned `AGENTS.md`, no
repository, worktree, panel, fixture/source, operational memory, or private
answer/output files in model-visible space. Preserve the selected home for
Codex auth/config/instructions without assuming it is hidden from generic exec.
Prove each visibility boundary with fake auth before provider admission. If
bwrap isolation, account binding, effective feature state, or any relevant
visibility proof is missing, do not admit provider work.

## Tool evidence and stop rules

The local loopback fixture may establish its declared request shape and fake
runtime behavior. Its request declares generic exec, and the fake call failed
closed with `code_mode_host=false`. This does not establish the tool inventory
offered by the subscribed provider. Do not use registry contents or a proxy
request shape as a substitute for actual native offered-tool evidence.

Before each provider attempt, inspect the actual native offered-tool inventory
and stop if it is unavailable or unexpected. Direct arms stop on any offered
or invoked tool. Retrieval arms allow only Cairn search and pull; they stop on
any other offered or invoked tool, including generic exec. Validate allowed
Cairn calls and their source receipts using the exact returned
`pull_arguments`. Keep selected answers and sanitized usage/tool/source
receipts only in the owner-only output directory. Do not retain raw JSONL,
source bodies, or credentials in the repository.

## Existing task/privacy rubric

The full-source and privacy contract remains unchanged: answer the buried live
relay qualification with 512 KiB and classify 2048 KiB as offline history;
answer Cairn Atlas at 30 days while excluding Trail's 90-day setting; use
`POST /v2/export` and label `/v1/export` superseded; preserve the unresolved
24-hour/48-hour conflict; answer the lexical recovery item despite unavailable
semantic service; and report the local-only source unavailable. A partial span
with an unread middle is not complete-source support. The local-only sentinel
must not reach hosted search/pull, direct context, or an answer. Independent
grading must retain failed, unscored, and unrun attempts in the 15-attempt
denominator. This single synthetic replicate establishes neither savings nor
general usefulness.

V2 validation uses the existing fixture/eligibility checker, which does not
verify runner isolation or subscribed provider tools:

```sh
python3 -B scripts/check_source_fit_panel.py \
  --panel trials/source-fit/codex-luna-2026-10-25/FROZEN.json
python3 -B scripts/check_source_fit_panel.py \
  --panel trials/source-fit/codex-luna-2026-10-25-v2/FROZEN.json
```
