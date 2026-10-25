# CAIRN-223 Codex/Luna panel plan, 2026-10-25

## Decision state

Root approved this panel in principle. Its status is
`admission_pending_runner_preflight`; no panel attempt has run. Attempts start
only after the runner proves the frozen account, workspace-isolation, tool, and
token gates. It is a new manifest with a separate run identity; the stopped Claude pilot and its
unused Claude recovery manifest remain unchanged. The Codex/Luna plan follows
one successful subscribed Luna execution that called Cairn search and pull
against a disposable Store (reported in the CAIRN-223 Plane comment updated at
2026-10-25 04:31 UTC). That is readiness evidence for one execution, not a
completed source-fit outcome or evidence for every account home.

The frozen fixture is
[`trials/source-fit/codex-luna-2026-10-25/FROZEN.json`](../../trials/source-fit/codex-luna-2026-10-25/FROZEN.json),
SHA-256
`0a4e44eb95ce2a26a48d9731a87fef1d75bf3b2d9958925f70ffb226a323f5bf`.
It uses baseline source commit `5c3ad21bfc5b6b3982496698af316d89dcfdc0a6`.
The six cases preserve all 15 planned arms from the source-fit recovery design:

| Case | Arms | Coverage | Assigned home |
| --- | --- | --- | --- |
| `buried_live_qualification` | current, direct, hints, author_split | Oversized mixed note with a buried live qualification; complete-source recovery; author-maintained current extract with original history preserved | `~/.codex` |
| `wrong_project_homonym` | current, direct, hints | Cairn/Trail Atlas confusion; exclude the wrong project's 90-day setting | `~/.codex-harm` |
| `current_correction` | current, direct | Current `/v2/export` correction with historical `/v1/export` retained | `~/.codex-3` |
| `required_conflict` | current, direct | Two mandatory thresholds remain unresolved | `~/.codex-harm` |
| `semantic_unavailable` | current, direct | Lexical recovery while the semantic worker is unavailable | `~/.codex-3` |
| `inaccessible_source` | current, direct | Local-only source stays unavailable in hosted search/pull and direct context | `~/.codex` |

Case-level assignment keeps all arms in a case on one account, preserving each
within-case comparison. It does not estimate account effects: the three homes
receive 6, 5 and 4 attempts, and account is confounded with case. Results must
be reported by case and arm; this panel cannot support cross-account quality
claims.

## Frozen route and admission limits

The proposed route is Codex CLI 0.164.0, explicit `model_provider="openai"`,
requested model alias `gpt-6-luna`, medium reasoning effort, and ChatGPT
subscription authentication only. Record the provider-resolved model ID only
when a native event actually exposes it; otherwise keep it unknown. A catalog
entry or requested alias does not establish the resolved provider model. Use the three homes above under their distinct verified account
identities. Before **each** attempt, bind the exact home/auth-file digest and
account identity, then read app-server `account/rateLimits/read` and
`model/list` from that home. Admit only if the account identity still matches,
`ordinaryUsageAllowed` is true, `limitId` is `codex`, `gpt-6-luna` is present,
and the primary window is below 80% used. Recheck the account file immediately
before launch. Any failed, unavailable, changed, or malformed route result
stops the campaign. Stop if an observed model identity conflicts with the
requested alias; record a missing resolved identity as unknown, not as a match.
Do not move a stopped attempt to another home.

Limits are 15 attempts, 180 seconds per attempt, 3,600 seconds total, 125,000
reported tokens per attempt, 2,000,000 reported tokens total, 1,200 output
tokens per attempt, and a 125,000-token reserve before each admission. The
total-token gate must require `observed_total + 125,000 <= 2,000,000` before
launch. The route audit found one Luna CLI turn with whole-turn usage of
92,111 input, 77,056 cached input, and 383 output tokens. Cached input is a
component of input and must not be added a second time. The 125,000 per-attempt
limit leaves about 32,500 tokens of headroom over that observed input-plus-
output amount; it is a planned stop threshold, not a provider-side cap. The
2,000,000 campaign limit accommodates 15 attempts at the per-attempt ceiling
plus one admission reserve. Missing or malformed terminal whole-turn usage, a
nonterminal result, an observed model mismatch, token or time limit, failed tool result, or failed
task-evidence parse marks the current attempt failed or unscored and stops
further admission. No automatic retry enters this run.

Codex `turn.completed` usage can cover multiple provider responses. The
Codex-specific runner must retain selected answer text and sanitized
whole-turn usage, tool-call, and source-delivery receipts in an owner-only
private output directory, with stable turn/tool identifiers and deduplication. The selected answer is
needed for independent grading. Per-response usage is
unknown unless a native event exposes it; do not infer or allocate a whole-turn
aggregate among responses. Do not add cached input to total input. Keep raw
JSONL, source bodies, and credentials out of the repository and committed
artifacts. Record each attempt's exact argv, CWD, CLI hash/version, account
home alias, and selected configuration hashes. Use the existing authenticated
account home; never copy or symlink auth into a temporary `CODEX_HOME`. Run
with fixed isolated CWD `/work` and copy the exact repository `AGENTS.md`
there (SHA-256
`bbe777861b084ceea60ce01901edf5fafd55c710e43facdb2fb52f303dd98c49`). The
three existing Codex homes share an `AGENTS.md` SHA-256 of
`5d3505c77c0f45c670fa4d326824b0aed015cbd9d071c49020b8d0d449accc60`. Preserve
ordinary user config and instructions. Disable code and shell features with
`--disable code_mode_only --disable code_mode --disable shell_tool`; verify
that actual offered model tools match the arm allowlist. The bwrap workspace
must hide repo, fixtures, and private output. Because the selected home must
remain available to Codex for auth/config/instructions, do not assume it is
hidden from shell tools. If shell/file tools remain available or the runner
cannot prove the workspace boundary, stop before provider admission. Freeze
argv, CWD, and instruction/config hashes identically across arms and include
their token overhead in the limits.

Disable `shell_tool`, `code_mode`, and `code_mode_only` for every attempt.
Before provider admission, run a provider-free fixture with those same flags and
prove Cairn search and pull remain callable for retrieval arms; direct arms must
offer no tools. Verify the native tool inventory before every attempt. If this
preflight or any later inventory check fails, stop without admitting provider
work. The selected `CODEX_HOME` remains available to Codex for
auth/config/instructions, so the runner must not assume shell tools cannot read
it. Disable code/shell features and refuse admission if the offered tool set or
bwrap visibility does not match the plan. Enforce the 125,000-token attempt limit against
cumulative completed-turn usage as it streams. If a turn completes over the
limit, retain the selected answer and sanitized receipts, mark the attempt
over-limit/unscored, and stop the campaign. A single turn can still cross the
threshold before it can be stopped.

The private `cairn.codex-source-fit-accounts/1` manifest is stored at
`$HOME/.cache/cairn-source-fit/codex-luna-2026-10-25/accounts.json`. It has
one entry per account with `account_id`, `account_home_alias`, `config_home`,
`auth_file`, and `auth_sha256`; it contains no credentials. The runner pins its
SHA-256 with `--expected-accounts-sha256` and reads it using `--accounts` before
each attempt. The file is owner-only (0600 under a 0700 directory).

The frozen `maximum_primary_used_percent` is 80. Stop before an attempt at or
above that value. It is a tripwire, not an absolute quota budget.
Prior reads for these homes established distinct ChatGPT identities, Luna
catalog presence, and allowed ordinary usage, but returned only utilization
percentages. The route auditor most recently observed `.codex-3` at 37% used.
Those reads did not provide an absolute allowance. The successful Luna
tool execution likewise establishes no panel capacity. Root's route auditor
must re-read and report each selected home immediately before approving
admission; if any home is already at or above the threshold, or the auditor
cannot establish the subscription route, the panel stays unrun. If the owner
requires an absolute allowance rather than this bounded sequential tripwire,
the panel stays unrun until that evidence exists.

The reported-token ceiling is a harness admission limit, not a provider hard
cap. One unexpectedly large native response could cross it; the runner must
retain that attempt and stop without admitting another. The 125,000 reserve
allows only a next-attempt admission decision and does not prove the provider
will stay below that amount. The 80% gate and token reserve measure different
things and neither substitutes for the other.

The runner must use bwrap isolation with no repository or panel files mounted
in the model-visible workspace. It may expose only the disposable Store native
Cairn search and pull tools to retrieval arms; direct arms have no tools. A
shell or file read outside the frozen synthetic workspace makes the attempt
unscored and stops the campaign. If the runner cannot prove the local-only
source and panel manifest are outside the model-visible filesystem, do not
start the provider arm.

## Source and task checks

The fixture stores no operational notes. Its source hashes and UTF-8 byte
lengths are fixed in the manifest and validator report:

| Source label | Bytes | SHA-256 |
| --- | ---: | --- |
| `relay_mixed` | 11,482 | `66565b7e17f237062bad9aeea98179e632ebccd2f27d0219b699b58777cadbac` |
| `relay_current_extract` | 310 | `2afa58817bd41c504dbe8c4badd43c8153ffda00e3e262cfacf46b35fcb9f07c` |
| `trail_atlas` | 164 | `a9fa8a849e9c5926670ddd7a933d21a370442d1eb8fd7056d6de95a3e521e735` |
| `cairn_atlas` | 136 | `7850871a153f5eccc1140a58196280fc581abbdd024c124eb782d82ec1f709e9` |
| `old_export` | 137 | `cda6737ea6a7a63b2c9f67e715472e9b69904b709588d0789ef3f6d71048f45f` |
| `export_correction` | 173 | `0c4c4c4a2a58f1edcc488e2480cb11e335f4aaad1483e9721b9b7e9fc87c47ed` |
| `conflict_a` | 127 | `7217fa134b99849b07caa9e5e5bd475131dacff2f3a83c599384041dfd3b49ff` |
| `conflict_b` | 127 | `d84b4f2212c044d46f7b77f0962c82b2a8b41c9c66494f434bcbe4f5be4f5761` |
| `lexical_anchor` | 166 | `665f1105163d40a97842f547bd809c6d05a3acba11231d8b805ebb116a58b134` |
| `local_only` | 122 | `019810ac041bf31d337d5a8fbd8cbd071ef4fab5a88514d3a2d1675449a492dc` |

The frozen rubric requires 512 KiB for a live relay and identifies 2048 KiB as
offline history; requires Cairn Atlas at 30 days while excluding Trail's 90-day
setting; requires current `/v2/export` and labels `/v1/export` historical;
leaves conflicting 24-hour and 48-hour mandatory thresholds unresolved;
answers the lexical recovery item while semantic service is unavailable; and
refuses the inaccessible local-only source. For the buried case, a partial
span with an unread middle is not complete-source evidence. If current or hint context is
insufficient, an accepted recovery requires the model's allowed native Cairn
search and pull, using the exact returned `pull_arguments`, plus sanitized
whole-source identity, version, extent, and digest evidence. No source body or
raw provider stream belongs in committed reports.

Independent review scores every completed answer against that rubric and the
sanitized source-delivery ledger. Failed, unscored, and unrun attempts remain
visible in the 15-attempt denominator. This fixture cannot establish savings,
general usefulness, or a production retrieval change by itself.

## Readiness and execution boundary

The existing `scripts/run_source_fit.py` is a Claude-only runner. The Codex
route assessment also records that the Codex source-fit path was incomplete.
The one successful Luna search/pull execution closes neither gap: a Codex
runner still needs to preserve actual native tool results, record model identity
without inferring it from the alias, bind the selected account for each
attempt, enforce the limits above, and retain sanitized source extents. Do not
adapt the Claude runner by changing model flags. The Codex runner and its
per-home route checks remain separate work; this plan records no provider
outcome.

The frozen-plan validation command is:

```sh
python3 -B scripts/check_source_fit_panel.py \
  --panel trials/source-fit/codex-luna-2026-10-25/FROZEN.json
```

It reports six cases and 15 arms, assigned 6/5/4 across `codex_primary`,
`codex_harm`, and `codex_three`, with this panel hash. It checks fixture shape
and eligibility only. The Codex-specific source-fit runner is under
implementation. Its eventual invocation must pin this panel hash, a fresh
private output path outside the repository, all three account identities and
auth-file digests, the synthetic workspace/instruction hashes, and the
per-attempt rate-limit gates. No provider command is available in the current
source tree.

## Cost and coverage tradeoff

The 15-arm schedule preserves all six source-fit cases. At the observed
92,111-input-token turn size, the 2,000,000-token ceiling is a substantial
maximum exposure, not an expected cost or a subscription allowance. The
125,000 per-attempt threshold permits one response modestly larger than the
observed turn; the sequential reserve gate stops admission when remaining
headroom is insufficient. Keeping the full set retains the standalone
semantic-unavailable and inaccessible-source checks. A single
replicate cannot estimate variance, and the case-level account mapping
does not isolate account effects. Cost savings and broad retrieval benefit
remain outside this panel's claims.

The Pincite packet used for this plan is `pkt-1af6502637ffc562`, corpus
`corpus-2026-07-12-a11702cc9217`, doctrine `doctrine-f6bbb5196a3f8bf9`, release
`d3e0c0d4ccd1920b2e045c156f1cf0db4fc5f04f`. Its evidence-before-intervention,
authority-bounded, reversible-change, and no-change guidance supports keeping
this artifact at the reviewable plan stage. Repository ticket and source-fit
contracts take precedence; this plan records no provider execution.
