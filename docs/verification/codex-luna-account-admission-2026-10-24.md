# Codex Luna subscription admission check, 2026-10-24

On 2026-10-24, the selected `$HOME/.codex` account was signed in through
ChatGPT. The installed Codex CLI was 0.164.0. A read-only app-server
`account/rateLimits/read` call returned `ordinaryUsageAllowed: true` and a
`codex` weekly window at 7% used. The response did not provide an absolute
remaining token allowance. It did not establish how many future task turns fit
in the subscription window.

A single access probe used `gpt-6-luna`, the explicit `openai` model provider,
the signed-in account, an empty private working directory, read-only sandbox,
ephemeral session, ignored user configuration, and unset `OPENAI_API_KEY` and
`OPENROUTER_API_KEY`. It returned `READY` without a route or quota error. The
CLI reported
19,485 input tokens, including 11,520 cached input tokens, and five output
tokens for that turn. This confirms that the selected route and model worked
for one small turn; it is not a task outcome or an estimate of the 88 planned
panel workflows.

The [paired gate](token-efficiency-gate-2026-10-24.md) still needs a frozen
account budget and per-run admission policy, real native actor joins,
independent task grades, and effective deployment evidence. A sequential
campaign can stop on a fresh quota/usage check before each launch, but an
initial percentage snapshot cannot certify capacity for the complete panel.
No paired actor run or canary was launched by this access check.
