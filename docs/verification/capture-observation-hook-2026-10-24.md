# Capture observation hook probe (2026-10-24)

CAIRN-215's store and unit tests already covered the observation schema and
the hook's local outcome mapping. `scripts/check_capture_observation.py` now
exercises the actual PreCompact hook, CLI, authenticated API, and PostgreSQL
store in the disposable `make test-integration` cluster. The hook uses a local
selector executable that returns synthetic provider-shaped usage. It cannot
contact a provider. A temporary CLI shim forwards ordinary calls to the real
binary and injects only the named create/report refusals. No installed binding
is changed.

The probe checks a confirmed save with reported model, input/output/cache
tokens and cost; nothing selected; unchanged and courtesy skips; timeout and
reported provider error; a refused note write; and an observation refusal
after a confirmed write. A refused observation leaves no history row, while
the selected note and capture marker remain committed. Repeating that snapshot
reports `unchanged` without another note write. Selector or dialogue sentinel
text is absent from observation rows, selected note bodies, and state files.
Null metrics and absent rows are treated as unknown.

Local verification used a fresh disposable PostgreSQL 17 cluster, migrated it,
built the current Cairn binary, and ran the probe successfully. The eight
existing capture observation unit tests, `make check`, and `bash -n` for the
integration runner also passed. The full `make test-integration` suite was
not rerun for this test-only addition.

The probe above is a transport and persistence check using synthetic usage. The
bounded live candidate canary below adds one actual Claude selector result.

## Bounded subscription candidate canary

On 2026-10-24, one manually invoked `PreCompact` hook used a private,
capture-only candidate for the selected Claude account. The candidate copied
only that account's hook files and used a separate state directory. Its
`capture_observations` setting was `true`; the unrelated inbox binding stayed
disabled. The installed Claude settings and shared hook files were not changed.

The candidate ran the pinned lifecycle engine from source revision
`0653e27efe81ff52f27688b4f3fdc60ed1778580` (`memory.py` SHA-256
`6209b9b16a4d2317ccfee385f05fff7277d0db8dd6d90ff2ec231005dae390cf`). It used
the installed Cairn client SHA-256
`272e2ead0fe53d52fd1daa75fffb6ecf03aadf5a1d0030e73e4d18f8532e92c1`, version
`v0.0.0-20261024211620-19f39f942672`; the authenticated API reported the same
revision. The candidate config SHA-256 was
`4e10074169c98512551267e3ecd83cee1b262cabaea9b71d5ff2ef6118259cd0`; the
candidate hook settings SHA-256 was
`b8f7ae1dfdc50c8617c894f96aa997d18cf011a530263a5bc38747ebd5755120`. Its
private inbox manifest was pinned at
`0c5297c65111e55d974c5d9e2e7be88e450ed57a60d747f22ba19a7210ed75f8`.
The manifest was valid and disabled. Workflow `ca215-capture-bd0d352975924bf3889c18bdb613ba99`
used fixture SHA-256
`6500a7b6424cd62c03ae86b8253aab98d337ec9af65384f045ed08973f4bbd27`.
The fixture text and provider output are not in this repository.

Before the call, `claude auth status --json` reported a logged-in `claude.ai`
first-party Max subscription. The configured model was
`claude-fable-5-1[1m]`; the response reported `claude-fable-5-1`. No Anthropic
key or provider-base override was present. The inherited environment contained
an `OPENROUTER_API_KEY` variable name; the candidate child environment removed
all `OPENROUTER_*` variables. No fallback or retry was configured. The
candidate allowed one hook invocation, a 35-second selector timeout, at most
two CLI turns, and no tools or MCP servers. The hook bounds dialogue to 24,000
bytes and limits capture candidates to two handoffs, three durable notes, and
one checkpoint; this is not a 24,000-byte total selector-input cap. The
selector used `--no-session-persistence`. The auth status did not expose a
numeric remaining quota, so no second capture selector invocation was admitted.

The synthetic fixture completed and saved one selected record. The API
acknowledged observation `850a49b7-9937-419b-a49b-89c709653de0` for request
`eda6f23f-aa70-4c90-a41b-cf4645281623`; the selected record ID is
`7bbf902a-49d5-4e4c-8d6a-3eeef831e0d9`. The session SHA-256 was
`85e89b72a576b217205a8df67e0a246e08859a3f284d6f7476e36136da80ca31`; the
snapshot SHA-256 was
`4751bd2b807be2b7e4ebe17b6c9c5266f1fe6500f41379f7df60c3025994783c`. The
hook reported one selector call,
outcome `saved`, and selector outcome `completed`. Reported selector duration
was 12,991 ms and total hook duration was 18,447 ms. The Claude CLI returned
`input_tokens=2`, `cache_creation_input_tokens=2712`,
`cache_read_input_tokens=0`, `output_tokens=611`, and
`total_cost_usd=0.08481`. That cost is the CLI's report,
not proof of a charge or billing amount. The observation retains only the
session and snapshot hashes, usage fields, model names, durations, outcomes,
and observation identity. The observation row contains no dialogue, selected
note body, raw model output, or credentials.

A separate native Claude `--print` run checked the candidate `SessionEnd` hook
with `--no-session-persistence`, one actor turn, no tools or MCP servers, and
only the candidate hook settings loaded. The hook ran, but its receipt showed
that no transcript existed while it ran. The engine exited before selection;
the API acknowledged error observation
`dacb3f4c-bacc-4e60-a683-02ba530088b1` for request
`8228b6d5-e89d-446d-9034-27b0d80f5771`, with selector and usage fields absent.
The native session returned successfully, no transcript remained, and neither
its actor output nor usage was retained. This does not add another capture
selector result.

I also attempted one persistence-enabled native `--print` run through
`scripts/trial_task_eval.py:sandbox_command`. The bwrap profile mounted the
first-party credential file read-only, exposed no host project tree, and kept
the Claude home (including any session transcript) in the disposable sandbox.
The candidate settings contained only the staged `SessionEnd` hook; the actor
had one turn, no tools or MCP servers, and a synthetic prompt. The command
exited 1 with empty stdout and 3,383 bytes of stderr. It produced neither a
native-hook receipt nor a capture-observation receipt, and no session ID was
available for host filename matching. The sandbox exited and its home was
destroyed; the host Claude settings hash remained
`1654d27f5da01c0be4ece3b23c457bf2b696262aff14f20ad80b18c26f1e7dc`. Because
the CLI failure does not establish whether a provider request began, provider
use and outcome are unknown. Stderr and actor output were not retained. No
retry was made.

For rollback verification, a separate candidate config set
`capture_observations` to `false` and replayed the same event against its
isolated state. The capture result was `unchanged`, selector calls were zero,
the captured digest stayed the same, and no second observation was submitted.
Both the capture-enabled and rollback candidate bindings passed their pin
validator. The installed deployment check still reports `live_observed=false`
with four ordinary hooks verified and no issues; this was not an installed
Claude session or a persistent host rollout.

This establishes one bounded subscription-backed selector result, one native
no-persistence refusal observation, and a candidate-level opt-in/rollback
check. The persistence-enabled native attempt is inconclusive: it produced no
capture receipt, and its provider-call status is unknown. These results do not
establish accepted task usefulness, provider billing, net token savings, or a
successful provider-backed capture in a native Claude session. A fresh native
canary needs a reviewed method for retaining a receipt across CLI startup
failure. The full CAIRN-215 rollout gate remains open.

## Native local Claude SessionEnd capture

A focused follow-up ran a persistence-enabled Claude Code 2.1.310 `--print`
session with a fresh private `HOME`, `CLAUDE_CONFIG_DIR`, and hook destination.
The copied lifecycle config enabled only `capture_observations`; its selector
used the installed CLI with a fixture model name and inherited a fake
`ANTHROPIC_API_KEY` plus a loopback-only Messages endpoint. The actor and
selector requests were answered by a scripted local server. The process
environment was rebuilt from `PATH`, `LANG`, and these fixture settings; no
installed credentials or settings were read. The only Store was a fresh local
PostgreSQL cluster and the API used disposable hook/report identities.

The native host invoked the `SessionEnd` hook after the synthetic actor turn.
The hook read the private session transcript, made one actual Claude selector
CLI call to the local Messages stub, saved one scripted selected lesson, and
reported the capture under the SHA-256 of that native session ID. The
observation row joined to the `last_capture` state by session and snapshot
hashes. It recorded `outcome=saved`, `selector_outcome=completed`, requested
model `sonnet`, reported model `claude-sonnet-5-5`, selector duration 298 ms,
and total hook duration 340 ms. Reported usage was 137 input tokens, 23 output
tokens, 9 cache-creation input tokens, and 41 cache-read input tokens. The
Claude CLI also reported cost `$0.0005306` for this scripted response; this is
only a local CLI estimate and is not provider billing or a charge.

The fixture verified the selected record and `SessionEnd` observation in the
disposable API, and queried PostgreSQL to confirm the dialogue sentinel was
absent from capture observations and saved record versions. The state file and
private transcript/output were removed after the run. Reproduction is
`python3 -B scripts/check_claude_capture_observation_native_disposable.py`;
the native case is also gated into `scripts/check_capture_observation.py` by
`CAIRN_CLAUDE_BINARY`.

This verifies one native local capture and its usage/outcome join. It does not
show actor usefulness, accepted task outcomes, subscription usage, billing,
or installed canary behavior. The native case covers the saved selector path;
unchanged/courtesy/no-selector and opt-out behavior remains covered by the
separate model-free capture probe and unit tests, not by a second native
session here. CAIRN-215 remains open.
