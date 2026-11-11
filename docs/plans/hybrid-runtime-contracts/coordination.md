# Coordination contracts: coordination/v7

Status: acceptance criteria and source characterization for the authorized hybrid
runtime consolidation campaign (`CAIRN-301`), prepared against baseline commit
`db45c4474fa7911b5569f2f55ebf2bd19c9a81bb` (`shared/1`, consumer ownership updated at `4b912bb2`, `8f7357b1`/`88b77474`, and `cca6fb78`) and reconciled with
`foundation/v4` ([`foundation.md`](foundation.md) `8b768649`, [`f04-consumer-decision.md`](f04-consumer-decision.md) `32ca633` + `cca6fb78`)
and `release/2` ([`release.md`](release.md) `aa0c26f` / `891acd75`, plus `C1`–`C3` compatibility receipt closure) on 2026-11-06. Revision
`coordination/v7` is a bounded source-preserving docs-only erratum over `coordination/v6`
(`c9a1908bacac65806f233d040bd0656c8b43f97e`, blob `e9e81fc2`, per overall instruction `987eadf4` v1)
correcting the nonexistent `10s` `INTENT_LOCK_TIMEOUT` prose against frozen `coordination.py`
(`92716c54`) without altering runtime scope, `X-01` agreements, or acceptance gates. These
contracts specify behavior to characterize, implement, and verify. They do **not**
claim that `internal/lifecycle/journal`, `internal/lifecycle/wake`,
`internal/lifecycle/transcript`, `internal/lifecycle/coordination`, or the
proposed `cairn lifecycle coordination` CLI subcommands exist today.

The [implementation plan](../hybrid-runtime-implementation.md) and shared
[contract index](README.md) (`shared/1`) control execution order and file ownership:
- Overall lead `agent-1249` owns [`README.md`](README.md), cross-team arbitration,
  and `X-01` (`CAIRN-302`) closure.
- Foundation `F-B1` (`F-01` / `CAIRN-316`) owns initial lifecycle command
  registration in `cmd/cairn/main.go` and `cmd/cairn/commands.go`; Semantic `S-B2`
  owns initial lifecycle targets in `Makefile`. Per [`f04-consumer-decision.md`](f04-consumer-decision.md)
  (`32ca633`, `cca6fb78`) and overall ownership `4a6d3110` v1 + `8a4baa32` v1, after `F-01` hands over the
  binding/CLI boundary, **`F-B3` (`F-04`) receives the sequenced compatibility-only
  slot for `integrations/lifecycle/inbox_binding.py`, original-event and
  checked-config-snapshot activation plumbing in `integrations/lifecycle/memory.py`
  and `integrations/lifecycle/coordination.py`, `inbox_handoff` wrapper construction,
  `inbox_cue_text` drain rendering, `inbox_context` compact/fallback/next-cue
  rendering, and the minimum checked-selection guard at `compact_inbox_command`
  config resolution** (retaining Coordination/`recall_dispatch.py`'s owned fixed
  error-family mappings and transferring **zero** command grammar, claim, journal,
  native-session, or watcher/recovery transition ownership); **Coordination defers
  overlapping Python frontend and `J-05` rewrites until `F-04`'s reviewed caller
  change lands**. Coordination owns this contract (`coordination/v7`) and separate
  `internal/lifecycle/{journal,wake,transcript,coordination}` packages, wiring CLI
  subcommands in a serialized shared-file slot after `F-01`.
- New Go implementation consuming shared contracts (`J-02` through `J-06`) waits
  for explicit `X-01` completion notice. Read-only source characterization,
  synthetic fixture capture, and discrepancy proofs (`J-01` / `CAIRN-332` +
  `CAIRN-276`) proceed immediately.

## Work items, backlog reconciliation, and dependencies

| Plan key | Plane item | Size | Builder | Required predecessors | Deliverable summary |
| --- | --- | --- | --- | --- | --- |
| `J-01` | `CAIRN-332` (paired with `CAIRN-276`) | L | `J-B1` | `X-01` / `CAIRN-302` for closure; fixture capture starts now | Golden traces for journal execution/recovery/7-reader inspection, CLI/exit/stream grammar, `cairn-inbox` launcher, `--config-dir` watcher, and `settled_journal_index` / `finish_reply` boundary |
| `J-02` | `CAIRN-333` (supersedes Python scope in `CAIRN-277`) | L | `J-B1` | `J-01`, `CAIRN-276`, `F-01` (`CAIRN-316`), `F-04` (`CAIRN-317`), `X-01` | Go `internal/lifecycle/journal` execution owner for explicit `inbox` commands (`complete`, `ack`, `respond`, `finish-reply`, `drain`, `compact`) |
| `J-03` | `CAIRN-334` (supersedes Python scope in `CAIRN-278`) | L | `J-B1` | `J-02`, `X-01` | Ordinary pending-slot replay plus the exact-result exception (`Recover`) and side-effect-free `Inspect` (`JournalStatus` for all 7 readers) |
| `J-04` | `CAIRN-335` | L | `F-B1` + `J-B2` (paired) | `X-01` (`CAIRN-302`), `F-01` (`CAIRN-316`); joint `F-R1` + `J-R2` (+ `J-R1`) review | Paired delivery: `F-B1` `internal/procpeer` client connect + `J-B2` `internal/lifecycle/wake` adapters (Codex, Claude, OpenCode, Hermes, Herdr) in separate files, integrated together |
| `J-05` | `CAIRN-336` | L | `J-B3` | `J-03`, `J-04`, `F-04` (`CAIRN-317`, incl. `F-B3` caller slot), `X-01` | Go native-session orchestration (`hook` & `watch --config-dir`), compiled `session.JournalBoundary` + `journal.SessionJournalAdapter`, `owner_process`, OpenCode `CAIRN_REQUEST_ID` scan, reminders, and shutdown |
| `J-06` | `CAIRN-337` | M | `J-B3` | `J-05`, `F-02` (`CAIRN-318`), `F-03` (`CAIRN-319`), `X-01` | Go `internal/lifecycle/transcript` (`OFFSET:LENGTH:SHA256` pins, `O_NOFOLLOW\|O_NONBLOCK`, bounded scans, strict JSONL) and recovery CLI modes |
| `HC-04` | `CAIRN-338` | Gate | Lead + `J-R1`/`J-R2` | `J-01`–`J-06`, reconciled `CAIRN-276`–`278` | Integrated Go coordination acceptance and full recovery/cancellation/transport matrix |

Backlog reconciliation rules enforced in Plane under parent `CAIRN-301`:
- **`CAIRN-276`** remains active and paired with `J-01`; its original evidence and
  `implementation-plan.txt` attachment remain intact.
- **`CAIRN-277`** and **`CAIRN-278`** remain open (not marked `Done` from planning
  alone) under `CAIRN-301` with single deduplicated reconciliation banners
  superseding their standalone Python `inbox_journal.py` extraction/caller-migration
  vehicle in favor of `J-02` and `J-03` while preserving every acceptance invariant.
- Native parent `CAIRN-301` is verified by readback on all 10 items. Native
  blocking relations are **unverified** because `plane` CLI exposes no relation
  API; item bodies and this contract enforce all dependency edges.

Independent Opus 5.5 Max reviewers review both specifications and candidate
commits from separate lenses before any slice lands:
- **`J-R1` (`claude`):** retry, refusal, completion, reply references, CLI/exit/stream
  grammar, `session.JournalBoundary`, and the `settled_journal_index` / `finish_reply` boundary.
- **`J-R2` (`claude-harm`):** peer identity, session custody, locking,
  cancellation, process death, and transcript recovery.

## Complete CLI and issued-command compatibility grammar

Reference source: [`coordination.py`](../../../integrations/lifecycle/coordination.py)
(`lines 2020-2305, 2611-2625, 2848-2869, 2955-2964, 4289-4520`) and
[`install-agent-coordination.py`](../../../scripts/install-agent-coordination.py) (`lines 37-48`).
Both the proposed Go `cairn lifecycle coordination` surface and retained `/2`
entrypoints must preserve every live session command, issued prompt cue, and
multi-binding watcher entrypoint:

1. **Hook & Multi-Binding Presence Watcher:**
   - `cairn lifecycle coordination hook --config <path> [--event <name>]`:
     reads up to `1048576` **bytes** (`1 MiB`) of raw `stdin` (`> 1 MiB` raises
     `INVALID_HOST`, `line 4468`); internal store `call()` defaults to `4s` timeout
     (`line 1502`). When `inbox_recall_binding` is configured and the event matches
     `inbox_recall_wake(event)`, prints `json.dumps(result, ensure_ascii=False, separators=(',', ':'))`
     plus `\n` (`lines 4486-4488`); all other hook paths print default `json.dumps(result)`
     plus `\n` (`line 4490`). Activation failure emits `phase=activation` diagnostic,
     calls `record_activation_refusal`, and exits `2` (`NATIVE_PROMPT_REFUSED`) so
     Claude erases only the joined channel message (`lines 4476-4484, 4508-4510`).
     Under the `F-04` compatibility caller (`C3`, [`f04-consumer-decision.md`](f04-consumer-decision.md)
     `8f7357b1`/`88b77474`, [`foundation.md`](foundation.md) `8b768649`), code/receipt
     trust precedes binding-data reads: missing, unsafe, or tampered receipt, Python
     executing closure, or Go executable produces the declared new compatibility-preflight
     failure even on excluded callbacks—empty `stdout`, exit `1`, and fixed single-line
     `stderr` `Cairn coordination diagnostic: phase=compatibility_preflight error_class=CompatibilityPreflightError\n`,
     with **no** `NATIVE_PROMPT_REFUSED` exit-`2` promotion and **no** ledger,
     refusal-record, token, or API effect (`/2` unchanged). With valid executing code,
     Go runs source `inbox_recall` admission once using the parent's captured
     environment, `cwd`, checked config, and raw event **before** opening or hashing
     any `/3` binding or target config pin; an excluded callback returns only an
     activation-prefix skip/`false` so the retained coordination frontend continues
     under its ordinary policy (never a whole-hook skip), whereas admitted callbacks
     run full `F-01` `/3` binding/image/config checks before state effects.
   - `cairn lifecycle coordination watch --config-dir <dir> [--once]`:
     `watch` accepts **only** `--config-dir` (omitting `--config-dir` exits `2` via
     `parser.error("watch requires --config-dir")`; `lines 4492-4493`, matching live
     `cairn-presence.service`). Acquires non-blocking BSD `flock` on
     `<config-dir>/.watch.lock` (`SESSION_BUSY` -> stderr + exit `1`), discovers
     sorted `<config-dir>/*.json` bindings on every cycle (`claude-one`,
     `claude-two`, `codex-one`, `agy-one`, etc.), runs `watch_once` per binding
     (any malformed binding or unhandled exception aborts the loop with exit `1` for
     service restart; `lines 4497-4515`), and waits up to `WATCH_INTERVAL`.
     Under the receipt-checked Go `/3` entry (`cairn-lifecycle coordination --receipt PATH --receipt-sha256 SHA --role coordination --entrypoint ALIAS --config ABS --hook-timeout H --launch-root ROOT --shell SHELL -- watch --config-dir DIR [--once]`, whereas Python `V` `lifecycle_compatibility.py` continues to reject `watch`), `VerifyTail` admits `watch --config-dir DIR [--once]` without a hook deadline (`isHook() == false`). Before acquiring `<DIR>/.watch.lock` and at the start of every watch cycle, `VerifyWatchConfigDir` resets cached receipt/manifest state and re-verifies the receipt, snapshot closure, binary pin, `/3` binding (`CheckedBinding` when bound, `UnboundProfiles` when unbound), exact 1-to-1 membership between sorted `<DIR>/*.json` and `receipt.coordination_configs` (where a file in a separate copies directory `DIR` whose SHA-256 equals an unused pinned descriptor stands for that descriptor's canonical path while the canonical file in the generation directory must also still match its pin), and every profile's mode `0500` launcher in `LaunchRoot` derived by substituting that profile's canonical path for `--config` in the base vector; any initial or per-cycle mismatch exits `1` with empty `stdout`, fixed `stderr` `Cairn coordination diagnostic: phase=compatibility_preflight error_class=CompatibilityPreflightError\n`, and zero `.watch.lock`/state/API effect on initial preflight. Because a staged generation directory contains non-coordination JSON artifacts, staged `--config-dir` uses a dedicated copies directory (such as `<root>/bindings`), and `<DIR>/.watch.lock` is scoped to `DIR`; `R-06` unit cutover must stop the retained `/2` watcher or reuse the unit's single active bindings directory so two watchers never serve the same `state_dir` concurrently.
2. **Explicit Journaled Inbox Commands, Drain, Compact Operations, and `C1` Authenticated Launch Closure:**
   - `inbox --context <context.json> {complete|ack|respond|finish-reply} [--disposition {handled|ignored|failed}] [--code {unsupported_kind|source_unavailable|processing_failed}] [--version V RECORD_UUID]`:
     reads up to `1048576` **decoded UTF-8 characters** on `stdin` (`> 1 MiB` chars
     -> `INVALID_REQUEST` exit `2`, `lines 2062-2064`). `--disposition` and `--code`
     apply only to `ack` (with mandatory pairing rules: `handled` forbids `--code`,
     `ignored`/`failed` require `--code`, inserted into `ack` argv before
     `<delivery_id>`; `lines 2031-2032, 2054-2061`). `--version` (`nargs=2`,
     `line 2030`) applies only to `respond` (`lines 2052-2053`). `finish-reply`
     requires a `request` context carrying `commands.respond` (`lines 2045-2047`).
     Turn-end reminder texts (`lines 2611-2625`) embed `context` argv and cover
     `request`, `response`, and `notice`.
   - `inbox drain --config <path> --agent-id <uuid> --execution-id <uuid> [--lease-seconds 900]`:
     `--lease-seconds` defaults to `900` (`60..3600`, `line 4289`), checks live
     non-ending session ownership (`NOT_THIS_SESSION`) and absence of `inbox_intent`
     / `inbox_attempt` (`NATIVE_ACTIVE`) before contacting the store, claims at most
     one item, writes owner-only `drain-<lease_uuid>.json` (`attempt_id = "drain-<lease_uuid>"`,
     `route = "drain"`, and **no** `finish_reply` or `renew` field; `lines 4341-4344`)
     before printing `cairn.inbox-drain/1` (which carries the direct `cairn renew` argv
     at `lines 4349-4350`), or prints `delivery_id: null` (`lines 4304-4358`). Drain
     journals are not added to `inbox_journals` (watcher, `settled_journal_index`,
     and leave gating ignore them, except legacy seeding of a state lacking
     `inbox_journals` adopts matching `agent_id` files). Legacy `/2` drain cues embed
     the interpreter, module path, and `HOOK_CONFIG_PATH` (`lines 2955-2964`);
     compatibility drain cues (`inbox_cue_text`) replace `[sys.executable, __file__]`
     with the authenticated compatibility launch while keeping `HOOK_CONFIG_PATH` equal
     to `V`'s selected `--config` and leaving the direct `cairn inbox next` hint unchanged.
   - `inbox compact {read|inspect|settle|advance} <16-hex-handle> [handled|failed|ignored] [--code {unsupported_kind|source_unavailable|processing_failed}]`
     **and `/2` bare `PATH` launcher `cairn-inbox` vs `C1` compatibility closure:**
     all four `inbox compact {read|inspect|settle|advance}` operations are **session-issued
     live-attempt operations**, distinct from excluded offline/operator inspection in
     Section 3 (`inspect-held-refusal`); specifically, compact `inspect` (`lines 2142, 2249-2282`)
     calls `event-renew` (`lease_seconds=90`) and renews a live delivery lease rather than
     performing effect-free operator inspection. Legacy `/2` prompts (`lines 2848-2864`)
     instruct live agents to run bare `cairn-inbox read|settle|advance <handle>`, resolved
     via `PATH` (`~/.local/bin/cairn-inbox` or staged `<account>/bin/cairn-inbox` ->
     `exec env CAIRN_INBOX_ROOT=<root> <python> <root>/coordination.py inbox compact "$@"`,
     `install-agent-coordination.py:37-48`, `stage_token_efficiency_canary.py:230-233`).
     Under legacy `/2` (no compatibility `V` context), resolves `CAIRN_INBOX_ROOT`
     (default `~/.local/share/cairn/coordination`); **under `C1`'s minimum
     checked-selection guard in `compact_inbox_command` (`F-B3` slot, [`f04-consumer-decision.md`](f04-consumer-decision.md)
     `cca6fb78`, [`foundation.md`](foundation.md) `8b768649`), because `L` contains only
     `exec <shlex.join(V)> -- "$@"` with no exported `CAIRN_INBOX_ROOT`, `compact_inbox_command`
     derives the authoritative lookup `root` from the grandparent of `V`'s selected
     `--config` (`Path(selected_config).resolve().parent.parent`, requiring
     `<root>/bindings/<binding>.json == Path(selected_config).resolve()`) when
     `CAIRN_INBOX_ROOT` is unset, and when `CAIRN_INBOX_ROOT` is set requires it to
     resolve to that exact same `root` (`INVALID_HOST` exit `7` otherwise), binding
     lookup `root`, `config_path`, and `entry['context']`/`entry['state']` to `V`'s
     selected configuration BEFORE `load_config(config_path)` (`line 2179`) or any
     state/context read, `session_lock`, lease renewal, or journal effect, and consuming
     `V`'s already-checked config snapshot bytes (or re-verifying its pinned digest on
     load) so a post-validation config swap refuses before effects (`CAIRN_INBOX_ROOT` is
     an optional lookup input only, never authority to select another profile/config).**
     Globs `<root>/state/*/compact/<h>.json` plus `<root>/state/compact/<h>.json`
     (`!= 1` match -> `NOT_FOUND`, `lines 2153-2159`), validates owner-only `<h>.json`
     (`cairn.compact-inbox-handle/1`: `{schema, binding, context, state, native_session_id, issued_at}`,
     TTL `3600s`, max `128`; contains **no** executable path) and owner-only
     `<root>/bindings/<binding>.json` (`lines 2161-2179`). Issued only for `notice` and `response` (`lines 2215, 2824`).
     Verifies live `owner_process` (`STALE_SESSION`), `caller_descends_from(owner_process)` +
     `CODEX_THREAD_ID` / `CLAUDE_SESSION_ID` (`NOT_THIS_SESSION`, `lines 2204-2208`), and
     immutable delivery fields (`INVALID_HOST`, `lines 2211-2216`). `advance` (`lines 2217-2248`)
     skips lease renew and source check, requires `notice_batch_version == 1`
     (`UNSUPPORTED_CONTROL`), acquires `session_lock` (`SESSION_BUSY`), and prints
     `cairn.compact-inbox-batch/1` (whose `next` cue calls `inbox_context` at `lines 2242-2246`
     and therefore renders the same authenticated compatibility cues). `read`/`inspect`/`settle`
     renew lease (`STALE_LEASE`) and verify whole-version source (`NOT_FOUND`,
     `PAYLOAD_UNAVAILABLE`, or local `SOURCE_UNAVAILABLE` at `lines 1839-1854`, bypassed
     only for `settle <h> failed --code source_unavailable`); `settle` runs
     `ack --disposition ...` (`lines 2283-2288`), prints `cairn.compact-inbox/1`, and
     raises `CONFIRMATION_MISMATCH` (`exit 7`) if inner exit `0` lacks committed `ack`
     (`lines 2293-2302`).
   - **`C1` Authenticated Launch Vector `V`, Short Launcher `L`, Coexistence, and Deadlines (`F-04` `F-B3` caller slot):**
     Machine execution of every newly issued compatibility Python command (`inbox_handoff`
     wrappers `completion`/`acknowledgement`/`response` plus derived `finish_reply`,
     non-compact turn-end reminders, and `drain` context/output; `inbox_cue_text` `drain`
     cue; and `inbox_context` compact, `> 4096`-byte fallback, `source_unavailable`
     fallback reused by `unacknowledged_turn_end_reminder` at `lines 2602-2610`, and
     `advance` `next`-item cues for `inbox compact read|inspect|settle|advance <handle>`)
     executes `V + ["--", *original_command_tail]`, where `V` fixes the approved absolute
     Python interpreter, `-I -S -c` trusted stdlib-only verifier literal,
     `--receipt PATH --receipt-sha256 SHA`, `--role coordination`, `--entrypoint ALIAS`,
     and `--config ABS` (plus the hook timeout selection, which applies **only** to `hook`
     mode). Per [`foundation.md`](foundation.md) (`foundation/v4` `8b768649`) and
     [`f04-consumer-decision.md`](f04-consumer-decision.md) (`cca6fb78`),
     agent-visible context command fields, cues, fallbacks, next cues, and reminders
     render `[L, *original_command_tail]` through the short immutable `0500` external
     launcher `L` (`<persistent-private-launch-root>/<sha256-of-L-bytes>/inbox`, using an
     approved noninteractive shell qualified against `ENV`/`BASH_ENV` startup injection
     and containing only `exec <shlex.join(V)> -- "$@"`) so each shell command is
     `<= 512` UTF-8 bytes and aggregate cue/reminder control prose is `<= 2048` bytes
     within `4096`-byte compact inline bounds and harness `additionalContextLimit` caps
     (e.g. Codex `12000` bytes), never printing the multi-KB `-c` literal or
     `[sys.executable, __file__, ...]`. **`L` (`exec`-only, no subshell reparenting) and
     `V` (in-process verifier dispatch, no reparenting or environment scrubbing) MUST
     preserve both the caller's `/proc` process ancestry (`caller_descends_from(owner_process)`
     at `line 2204`) and the caller's environment including `CODEX_THREAD_ID` and
     `CLAUDE_SESSION_ID` (`lines 2206-2208`, fail-open when unset and required to
     distinguish sibling Codex threads sharing one `codex app-server` owner tree).**
     Direct `context.read` (`cairn agent ... history`, `lines 2679-2680`), `drain` `renew`
     (`cairn renew ...`, `lines 4349-4350`), and inner `commands.{complete,ack,respond}`
     Store CLI argv remain **unchanged** (no invented Python `read` verb). Fresh issued
     processes revalidate their pinned receipt/closure/binding/config selection before
     running existing command authority checks (failing preflight with fixed
     `CompatibilityPreflightError` exit `1` and zero journal/lock/lease/API effects below;
     `inbox --context <path>` at `lines 2027-2065` is outside `F-B3`'s caller slot and
     keeps `/2` owner-only `0600` + `cairn.session-inbox/1` parity after `V` revalidation
     without silent `context.state_dir` hardening), reject any tail
     `--config != selected --config`, refuse direct snapshot-member/alias path execution
     before any adjacent loader or local import, perform **no** hook activation re-entry
     or hook-admission bypass, and impose **no** hook timeout on `inbox`/`drain`/`compact`
     modes (preserving explicit `inbox_command` `30s` `intent_lock(target, 30)` wait and
     `30s` `send_intent(..., 30)` timeout at `lines 2067, 2093, 2098, 2128` vs
     watcher/hook `flush_journal` `0s` `intent_lock(target, 0)` try-lock and `10s`
     `send_intent(..., 10)` timeout under `session_lock` at `lines 2365, 2372, 2381`;
     default `4s` API `call(..., timeout=4)` timeout at `line 1502` plus explicit
     caller/outer deadlines; and `90s`/`900s` lease deadlines at `native_session.py:37`
     and `lines 2251, 4289, 4310`). Existing immutable `/2` copies, bare `PATH`
     `cairn-inbox` launchers, `cairn.compact-inbox-handle/1` handles, and already-issued
     `/2` argv remain untouched and executable until qualified retirement (`R-08`).
3. **Offline Recovery & Inspection Subcommands (`J-06`, excluded from `V`/`L`):**
   - `recover-refused-wake`, `recover-native-refused-wake`,
     `recover-codex-refused-wake`, `recover-codex-ended-turn`,
     `recover-daemon-start-claim`, `check-codex-native-health`,
     `reconcile-codex-queue-wake`, and `inspect-held-refusal` (`lines 4381-4464`).
     Together with `watch --config-dir` under the Python `-I -S` verifier `V`/`L`
     (`codex_queue.py:_open` lazily imports `websocket`, unavailable under `-I -S`),
     these offline recovery and inspection modes are **not** admitted through `V`/`L`
     and remain on their explicit retained `/2` route until `J-06`/`R-06` qualify
     their Go replacement, whereas the receipt-checked Go `/3` entry
     (`cairn-lifecycle coordination --receipt ... -- watch --config-dir DIR [--once]`)
     provides the `J-05`/`R-06` Go watcher replacement above (not an active production
     route before `R-06` qualification).

### Exit-code, stream, and serialization parity rules

| Entrypoint / condition | `stdout` | `stderr` | Exit |
| --- | --- | --- | --- |
| `argparse` usage error (any mode, including `watch` without `--config-dir` or `hook` without `--config`) | empty | usage / error text | `2` |
| `inbox` (`complete\|ack\|respond`) pass-through (`2134-2136`, including non-JSON exit `0` or `ok:true` nonzero exit) | inner CLI `stdout` verbatim | inner CLI `stderr` verbatim | inner `returncode` |
| `inbox` inline slot conflict (`complete\|ack\|respond`, `2081-2083, 2115-2119`) | JSON `IDEMPOTENCY_CONFLICT` | empty | `4` |
| `inbox finish-reply` intent mismatch in `journal_finish_reply` (`1988-1991, 4375`) | JSON `IDEMPOTENCY_CONFLICT` | empty | `7` |
| `inbox finish-reply` (`2000-2010, 2101-2103`): `status` picked in order (non-`OK` `replay_code`, uncommitted `completion.code`, `response.code`, else `RESULT_UNCONFIRMED`; `OK` when both committed & no `replay_code`); `ok` / `status in INTENT_FINAL_REFUSALS` (even if both committed on e.g. `STALE_SESSION` replay) / other | `cairn.finish-reply/1` JSON | empty | `0` / `4` / `7` |
| `inbox` / `drain` / `compact`: `CoordinationError("INVALID_REQUEST")` (`2045-2064, 2106-2110, 4373-4375`) | JSON `INVALID_REQUEST` | empty | `2` |
| `inbox` / `drain` / `compact`: other `CoordinationError` (`SESSION_BUSY`, `NOT_THIS_SESSION`, `NATIVE_ACTIVE`, `STALE_LEASE`, etc.) | JSON `<CODE>` | empty | `7` |
| `inbox` subprocess `OSError` / `TimeoutExpired` (`2130-2133`) | JSON `API_CONNECTION_FAILED` | empty | `7` |
| `inbox` / `drain` / `compact`: `OSError\|ValueError\|KeyError\|TypeError` (e.g. invalid UUID or missing `result["version"]` on `respond`, `2051, 2109, 4376-4379`) | JSON `INVALID_HOST` | empty | `7` |
| `compact settle` (`2293-2305`): confirmed `ack` / inner nonzero (`1\|2\|3\|4\|7`) / unconfirmed inner `0` (`CONFIRMATION_MISMATCH` printed directly at `2300-2302`) | `cairn.compact-inbox/1` JSON | empty | `0` / inner (`1\|2\|3\|4\|7`, fallback `7`) / `7` |
| `hook` (`C3`), fresh `L`/`V`-launched `inbox` / `drain` / `compact` (`C1`), **or** receipt-checked Go `/3` `watch --config-dir` (before `.watch.lock` and at the start of every cycle) compatibility preflight failure: missing/unsafe/tampered receipt, Python closure, Go executable, `/3` binding, `coordination_configs` 1-to-1 membership/canonical pins, or per-profile mode `0500` launcher, or invalid `--role`/`--entrypoint`/tail `--config` (even on excluded callbacks; `/2` unchanged; **zero** journal write-before-send, `.watch.lock`, state, refusal-record, lease, token, or API effect on initial preflight) | empty | `Cairn coordination diagnostic: phase=compatibility_preflight error_class=CompatibilityPreflightError\n` | `1` |
| `hook`: normal / `NATIVE_PROMPT_REFUSED` (`4508-4510`) / any other error (`4511-4515`, or unhandled traceback on escaping `AttributeError` in `record_activation_refusal`) | JSON + `\n` / empty / empty | optional diag / rejection line / unavailable line (or traceback on escaping `AttributeError`) | `0` / `2` / `1` |
| `watch --config-dir`: `.watch.lock` `SESSION_BUSY` or malformed binding / unhandled error (`4497-4515`, or escaping `AttributeError` in `watch_once`) | empty | unavailable line / traceback | `1` |

Serialization parity across `/2` and `/3`:
- **Exact byte parity** is required on: `wake_message` (`WAKE_CLOSING` and
  `LEGACY_WAKE_CLOSING`), Claude `channelMeta` (`source="cairn-events"` and exact
  field order `native_session_id`, `agent_id`, `execution_id`, `delivery_id`), and
  `inbox_recall_wake` hook `stdout`: Go MUST use an order-preserving compact UTF-8
  JSON serializer matching Python `json.dumps(result, ensure_ascii=False, separators=(',', ':')) + "\n"`
  (emitting raw UTF-8 `U+2028`/`U+2029` without `\u2028`/`\u2029` escapes, no `<`/`>`/`&`
  HTML escaping, Python-compatible number formatting, and preserved insertion key
  order, because `encoding/json.Encoder` escapes `U+2028`/`U+2029`, reformats floats,
  and sorts map keys, altering the bridge-measured wire byte count).
- **Lossless semantic JSON roundtrip parity** (raw UTF-8 without HTML escaping,
  trailing `\n`, `0600` atomic write-before-send, preserving all unknown/extension
  fields and exact numeric/string types across Python-to-Go and Go-to-Python updates;
  note `attempt_commands_absent` treats any key outside `{schema, attempt_id, delivery_id, kind, session}`
  on `cairn.inbox-intent/1` as non-claim-only) applies to persisted state, journal,
  context, compact-handle, and ledger JSON files.

## Journal execution, recovery, and inspection contract (`J-01`–`J-03`)

Reference source: [`coordination.py`](../../../integrations/lifecycle/coordination.py)
(`lines 1470-1487, 1829-2136, 2255-2268, 2308-2460, 3184-3199, 3724-3829`) and
[`test_inbox_recovery.py`](../../../scripts/test_inbox_recovery.py).
`internal/lifecycle/journal` is the **sole** Go execution and transition owner for
`cairn.inbox-intent/1` at `<state_dir>/intents/<attempt_id>.json` (`0600`, lock at
`<state_dir>/intents/<attempt_id>.lock`, directory `700`):

1. **Schema, Two `response` Slot Shapes, & Durable Write-Before-Send Ordering:**
   - Top-level fields: `schema: "cairn.inbox-intent/1"`, `attempt_id`,
     `delivery_id`, `kind` (`request|response|notice`), `session` (`{agent_id, execution_id}`),
     optional `completion` slot (`kind: "complete"|"ack"`), optional `response`
     slot (`kind: "respond"`), and optional `finish_reply` intent (`{argv, request_id}`).
   - **Two distinct `response` slot shapes MUST be preserved (`lines 1940-1948, 1993-1994, 2106-2125`):**
     1. **`finish-reply` materialized slot (`journal_finish_reply`, `lines 1993-1994`):**
        carries `expected_ref: {record_id, version}` (from `finish_reply_ref(completion)`);
        `send_intent` verifies the published `(event_id, ref)` against `expected_ref`
        and keeps `status: "pending"` with `code: "SCHEMA_MISMATCH"` on mismatch
        (`lines 1940-1948`).
     2. **Explicit `respond --version V RECORD_UUID` slot (`lines 2049-2051, 2106-2125`):**
        `version` token MUST pass `str.isdigit()` (else `INVALID_REQUEST`, exit `2`,
        `lines 2049-2050`), `uuid.UUID(record_id)` failure raises `ValueError` ->
        `INVALID_HOST` (exit `7`, `line 2051`), and `[version_token, str(uuid.UUID(record_id))] != [str(result["version"]), result["record_id"]]`
        is checked via **exact string equality and dict subscript access** (no `int()`
        conversion, no `.get()`, and no re-canonicalization of stored `result["record_id"]`;
        `--version 01` vs `1` -> `INVALID_REQUEST` exit `2`, whereas missing `"version"`/`"record_id"`
        or non-dict `result` raises `KeyError`/`TypeError` -> `INVALID_HOST` exit `7`;
        `lines 2106-2110`). Persists the `response` slot **without** `expected_ref`
        (`lines 2121-2122`), compares only `(kind, argv, input)` on explicit retry
        (`line 2114`), and skips `SCHEMA_MISMATCH` in `send_intent` (`line 1940`).
        Go `J-02` must accept `/2`-written explicit `respond` slots without
        `expected_ref`; adding `expected_ref` would be a separate labelled repair.
   - Every slot (`{kind, request_id, argv, input, status: "pending", recorded_at}`)
     and `finish_reply` intent MUST be durably written (`0600` temp file in same dir
     -> write -> `fsync` -> `close` -> `rename` -> directory `fsync`; `lines 1470-1487, 2089-2090, 2127`)
     **before** any API/CLI command is invoked. Token contents never appear on disk;
     only `--token-file` path appears in `argv`.
2. **Explicit Execution (`Execute`) vs Watcher Recovery (`Recover`):**
   - **`Execute` (`inbox_command`):** acquires `intent_lock` with **`30s` wait**
     (holding no other lock) and **`30s` send timeout**. Single-slot commands
     (`complete`, `ack`, `respond`) **always** call `send_intent(slot, 30)` even
     when `slot.status` is `"committed"` or `"refused"` (`test_inbox_recovery.py:810-829`).
     `finish-reply` (`lines 2092-2100`) calls `send_intent(completion, 30)` whenever
     `completion.status == "pending"` or `finish_reply_ref(completion)` is `nil`
     (which **includes** `refused` completions and missing-ref `committed`
     completions), and once `completion.status == "committed"` with a valid ref,
     **always** calls `send_intent(response, 30)`. When `slot.status != "pending"`,
     the store's new outcome is recorded in `slot.replay_code` without mutating
     `status`/`code` (except backfilling `completion.result` when `None` on a
     `committed` replay; `lines 1949-1954`). `ok: true` with nonzero exit keeps
     `status: "pending"` with `code: "OK"`, and status codes truncate to `64` chars
     (`lines 1934-1939`).
   - **`Recover` (`flush_journal`):** acquires `intent_lock` with **`0s` wait**
     (`SESSION_BUSY` returns `None`/`Busy` sentinel, `lines 2365, 2399-2402`) and
     **`10s` send timeout**. Replays **only** `pending` slots plus the exact-result
     recovery exception when `finish_reply` exists, `response` is absent,
     `completion.status == "committed"`, and `finish_reply_ref(completion)` is `nil`
     (`lines 2369-2374`). Corrupt/unreadable indexed journals (`ValueError`/`OSError`
     at `line 2366`) abort that session's `watch_inbox` cycle (`lines 4118-4139`)
     and make `Stop` exit `1`; a **non-object JSON** journal raises escaping
     `AttributeError` (`lines 2325, 2375`), uncaught by `watch_once` (`lines 4134-4139`)
     and `main` (`lines 4507-4515`), exiting `1` for the entire shared watcher (which
     under `cairn-presence.service` `Restart=on-failure` crash-loops the daemon and
     starves other sessions/bindings: `J-01` classifies this as a verified baseline
     defect, and any per-session containment in `J-05` requires an explicitly
     labelled repair) and turning hook `NATIVE_PROMPT_REFUSED` (exit `2`) into exit
     `1` (`lines 4479-4482`).
   - **Final Refusal Set (`INTENT_FINAL_REFUSALS`, `lines 1829-1832`):**
     `STALE_LEASE`, `STALE_SESSION`, `HOLD_RELEASED`, `REQUEST_CANCELLED`,
     `DEADLINE_EXCEEDED`, `IDEMPOTENCY_CONFLICT`, `INVALID_REQUEST`, `NOT_FOUND`,
     `AUTHORITY_DENIED`, `PAYLOAD_UNAVAILABLE`, `DESTINATION_PROHIBITED`,
     `STALE_RESOLUTION`. All other failures keep `status: "pending"`.
   - **`agent_id`-Only Journal Seeding (`session_journals`, `lines 2308-2350`):**
     when `inbox_journals` is absent, seeding scans `<state_dir>/intents/*.json`,
     skipping `OSError`/`ValueError` (`lines 2323-2324`) while raising escaping
     `AttributeError` on non-object JSON, non-dict `session`, or non-dict slot on an
     ownerless journal (`lines 2325-2327`; a matched journal lacking `attempt_id`
     raises `KeyError` at `line 2328`, caught per-session in `watch_once` and exiting
     `1` in hook), and adopts **every** journal
     matching **`agent_id` only** (`journal.session.agent_id == agent_id` or
     `agent_id in slot["argv"]`; `execution_id` is **not** compared, `lines 2326-2329`),
     **including** already-final `notice` and `response` journals (which the next
     `flush_inbox_intents` prunes at `lines 2423-2425`; seeding inspects only
     `session.agent_id`, slots' `argv`, and `attempt_id`). A resumed agent (same
     `agent_id`, new `execution_id`) therefore adopts its predecessor execution's
     unpruned and `drain-*` journals; changing to `(agent_id, execution_id)`
     filtering would be a separate labelled repair.
3. **Complete Inventory of Raw Journal Readers (`Inspect` -> `JournalStatus`, `J-03`) & Release Drain Seam:**
   `Inspect(stateDir, attemptID)` reads `<attempt_id>.json` without acquiring locks,
   mutating files, or sending commands, supplying structured `JournalStatus`
   (`Exists`, `ReadErr`, `ParseErr`, `NonObject`, `Schema`, `AttemptID`,
   `DeliveryID`, `DeliveryKind`, `Session` `{AgentID, ExecutionID}`,
   `ArgvMentionsAgent`, `CompletionKind`, `CompletionRequestID`,
   `CompletionStatus`, `CompletionNonObject`, `HasUsableFinishReplyRef`,
   `ResponseStatus`, `ResponseNonObject`, `HasFinishReply`,
   `UnmaterializedFinishReply`, `PendingReplay`, `ReplyPossible`, `ClaimOnly`) for
   all **7** raw journal readers in `coordination.py` (and Release `release/2`
   `D1`–`D7` read-only drain verification):

   | Reader in `coordination.py` | Missing file | `OSError` / malformed JSON | Non-object JSON | Exact source behavior & required `JournalStatus` facts |
   | --- | --- | --- | --- | --- |
   | `compact` settle check (`2255-2268`, only on `STALE_LEASE` during `settle`) | **re-raise `STALE_LEASE` (`exit 7`)** | **re-raise `STALE_LEASE` (`exit 7`)** | **re-raise `STALE_LEASE` (`exit 7`)** | Re-raises `STALE_LEASE` unless `Schema == "cairn.inbox-intent/1"`, `AttemptID == attempt_id`, `DeliveryID == delivery_id`, `Session == {agent_id, execution_id}`, `CompletionKind == "ack"`, `CompletionRequestID == request_id`, and `CompletionStatus == "committed"` |
   | `session_journals` seeding (`2316-2329`) | N/A (glob) | skip file (`continue`) | **escaping `AttributeError`** (also non-dict `session`/ownerless slot; missing `attempt_id` -> `KeyError`) | Adopts **every** journal where `Session.AgentID == agent_id` or `ArgvMentionsAgent(agent_id)` (including final `notice`/`response`, pruned on next flush; reads no `DeliveryKind` or slot status) |
   | `journal_committed` (`2434-2443`) | `false, nil` | **propagate `OSError`/`ValueError`** | **escaping `AttributeError`** (or non-dict `completion`) | `CompletionStatus == "committed"` on active `inbox_attempt` |
   | `journal_completion_state` (`2446-2459`) | `'absent'` | `'unknown'` | `'absent'` (falsy non-dict `completion` -> `'absent'`; truthy non-dict `completion` -> **escaping `AttributeError`**, `2457-2458`) | `CompletionStatus` in `('committed', 'pending', 'refused')` else `'absent'` |
   | `attempt_commands_absent` (`3184-3199`) | `true` | `false` (`OSError`/`ValueError`/`KeyError`/`TypeError`) | `false` | `ClaimOnly`: `isinstance(journal, dict) and set(journal) <= {'schema', 'attempt_id', 'delivery_id', 'kind', 'session'} and journal.get('attempt_id', attempt_id) == attempt_id` (any extension key -> `false`) |
   | `settled_journal_index` (`3724-3752`) | **skip (`continue`, counts as settled!)** | `false` | `false` | Non-list `inbox_journals` or non-string entry -> `false`; no `schema` check; `false` if truthy `completion`/`response` is non-dict or `status not in ('committed', 'refused')`, else `true` |
   | `reconcile_rejected_wake` legacy unindexed (`3786-3790`, `'inbox_journals' not in state`) | ok (glob) | **propagate `OSError`/`ValueError`** | **escaping `AttributeError`** | Calls `session_journals(config, dict(state))`: returns `false` if **any** `agent_id`-matched journal exists at all |

4. **`settled_journal_index` vs `finish_reply` Discrepancy Across Both Call Sites (`J-01` -> `J-03`):**
   - `flush_journal` (`lines 2390-2392`) marks `journal.get("finish_reply") and not journal.get("response") and completion.status != "refused"`
     as pending (`pending.add("response")`), whereas `settled_journal_index`
     (`lines 3724-3752`) walks `state.get("inbox_journals")` (`lines 3733-3736`) and
     checks only `("completion", "response")`, returning `True` even when a
     historical journal in `inbox_journals` has `completion.status == "committed"`
     and an unmaterialized `finish_reply`. (`reconcile_rejected_wake` requires
     absent `inbox_attempt`, `inbox_intent`, `inbox_result_policy`, `inbox_close`,
     and `active_prompt` at `lines 3780-3781`, so only historical `inbox_journals`
     apply.)
   - **Two distinct call sites:** (a) watcher `prepare_idle_wake` (`line 825`) runs
     *after* `watch_inbox`'s `flush_inbox_intents` in the same cycle (`lines 4118-4121`),
     reaching the state only if that flush hit `SESSION_BUSY` or if
     `finish_reply_ref(completion)` remains `None`; (b) hook
     `record_activation_refusal` (`line 3871`) runs with **no** prior flush, so a
     late `finish-reply` crashing between `line 2095` and `line 1996` followed by an
     activation refusal before the next watcher cycle immediately sees
     `settled_journal_index == True`. Furthermore, if `finish_reply_ref` is
     permanently `None` (missing/malformed result), `flush_journal` re-sends
     completion every cycle forever—so treating `UnmaterializedFinishReply`
     unconditionally as unsettled in Go would wedge wake reconciliation indefinitely.
     `J-01` pins both call sites and the persistent missing-ref case; `CAIRN-276`'s
     `"deliberate compatibility limitation"` disposition remains open alongside any
     bounded `J-03` repair.

## Native-session journal adapter (`session.JournalBoundary` / `journal.SessionJournalAdapter`)

Agreed with Foundation ([`foundation.md`](foundation.md) `foundation/v4` `8b768649`
`## Native-session journal adapter`, [`f04-consumer-decision.md`](f04-consumer-decision.md)
`32ca633` + `cca6fb78`, `shared/1` `README.md:72-81, 109-110`, events `deb6c59a`, `e7977c58`, `14f74fd1`). Foundation (`internal/lifecycle/session`)
owns `session.JournalBoundary` and imports no `journal` package (`F-04` independent
of `J-05`); `J-03` (`CAIRN-334`) delivers pure `Recover()` + `Inspect()` in
`internal/lifecycle/journal` with zero `session` import or dead adapter, and
**`J-05` (`CAIRN-336`)** (which depends on `J-03`, `J-04`, and `F-04`) delivers the
concrete Go `journal.SessionJournalAdapter` wiring alongside its first real
production Coordination consumer (`native_session.Host`, `coordination.py:78-85`):

```go
type FlushScope string // exactly "attempt" or "all"
type JournalBoundary interface {
    Flush(ctx context.Context, state *State, scope FlushScope) (settled bool, err error)
    Committed(ctx context.Context, state *State) (bool, error)
    TurnEndDiagnostic(ctx context.Context, state *State) error
}
```

- **Caller lock & `Flush` (`flush_inbox_intents`, `lines 2405-2426`):** runs under
  the caller's held `session_lock` using `0s` `intent_lock` wait (`SESSION_BUSY` ->
  uncertain -> `settled = false, nil`). Returns `(true, nil)` when `state_dir` or
  `state.agent` is absent. Both `"attempt"` and `"all"` replay **all**
  conversation-owned journals (`session_journals` seeding/pruning `inbox_journals`
  without dropping unrelated `State` fields; non-active journals with empty pending
  sets are pruned at `lines 2423-2425`). `scope` gates only `settled` (`"attempt"`
  checks active attempt uncertainty; `"all"` checks any owned journal uncertainty;
  `'unused'` and `'reply_possible'` are not uncertain). Preserves
  completion-before-response ordering and the `finish_reply` exact-result recovery
  exception (`lines 2369-2374`).
- **`Committed` (`journal_committed`, `lines 2434-2443`):** side-effect-free check
  of active `inbox_attempt` `completion.status == "committed"`; missing attempt/file
  returns `(false, nil)`; `OSError`/`ValueError` returns classified `(false, err)`;
  non-object JSON or non-object `completion` returns escaping error (`AttributeError`
  counterpart).
- **`TurnEndDiagnostic` (`record_unacknowledged_turn_end`, `lines 2462-2492`):**
  writes `cairn.unacknowledged-turn-end/1` (skipping when `wake_refusal_diagnostic.attempt_id`
  matches, emitting caller-owned `stderr` once per `attempt_id`) and never settles
  deliveries.
- **Classified-vs-escaping failure & persistence order (`native_session.py:376-485`, `foundation/v4` `## Native-session journal adapter`):**
  `settle` persists ended-turn evidence (`inbox_turn_ended = true`,
  `delivered_since_idle = false`, clearing matching `active_prompt`) **before**
  `Flush("attempt")`, and runs `Flush` **before** `end_turn`/`release`. Within the
  `Flush`/`end_turn` `try` region only (`native_session.py:405-415`):
  1. **Caught classified errors** (`Refusal`, `OSError`, `ValueError`, `KeyError`,
     `TypeError` counterparts, e.g. unreadable/malformed JSON journal) **always**
     mark `settlement = "turn_ended_pending"` under **both** `strict == true` and
     `strict == false`.
  2. **Non-exception unsettled return** (`Flush` returning `(false, nil)`, including
     `send_intent` timeout/transport/context-deadline uncertainty mapped to
     `status = "pending"` — Go `ctx` deadlines in `send_intent` MUST NOT become
     caught errors!) marks `settlement = "turn_ended_pending"` **only when
     `strict == true`** (`strict == false` preserves prior `settlement`, including
     initial `"pending"`).
  3. **Escaping errors** (`AttributeError` counterpart such as a non-object JSON
     journal, or errors before/after that `try` region) propagate without marking
     `turn_ended_pending` or making the second settlement save, leaving only the
     first ended-turn evidence save. Flush/turn-end `stderr` remains caller-owned
     (and Coordination/`recall_dispatch.py` retain their owned fixed error-family
     mappings without turning unexpected/panic errors into exit `2`; exact
     `UnexpectedError` fixed-line applies to `memory` only).
  - **External cancellation & pre-`J-05` rule (`f04-consumer-decision.md:88-100`, `89e8c1d6` v1):**
    `J-05` defines external `ctx` cancellation semantics **before** any cancellable
    `Flush(ctx, ...)` caller runs (never fabricating committed/refused journal slots
    or returning a no-op `settled = true`; harness turn cancellation follows `J-05`'s
    `sessionInboxHold` / `process_scan` contract). Until `J-05` lands, callers use
    the retained Python executor or explicit unavailability—never a no-op flush.
  `retire` persists `ending = true` before API calls, recovers `organic_claim_intent` /
  `organic_advance_intent` (with `_retirement_organic_control` fallback on
  `NOT_FOUND` / `AUTHORITY_DENIED`), refuses `agent-leave` with `INTENT_PENDING`
  when `Flush("all")` is `false`, and leaves (ignoring `STALE_SESSION` / `NOT_FOUND`)
  + fenced `release` before persisting `retired = true`.

## Session custody, locking, cancellation, and watcher contract (`J-05`)

Reference source: [`native_session.py`](../../../integrations/lifecycle/native_session.py),
[`grant_ledger.py`](../../../integrations/lifecycle/grant_ledger.py),
[`process_scan.py`](../../../integrations/lifecycle/process_scan.py), and
[`coordination.py`](../../../integrations/lifecycle/coordination.py) (`lines 1315-1351, 1490-1499, 1614-1813, 2990-3001, 3814-3816, 4093-4141`).

1. **Coordination Lifecycle 5-Family BSD `flock(2)` Inventory, Coexistence & Hierarchy:**
   Go MUST use `syscall.Flock` (BSD `flock(2)`, never `fcntl`/OFD locks) and adopt
   [`foundation.md`](foundation.md) (`foundation/v4` `8b768649`, `### Mixed-language lock coexistence`)
   by reference
   (same inode and pathname in both directions; **never** `chmod`, replace, or reject
   existing `0644`/`0664` Python locks; legacy lifecycle `.lock` opens follow
   symlinks without `O_NOFOLLOW` and lock the resolved inode) across all 5
   coordination lifecycle lock families (adjacent non-lifecycle locks are Claude
   bridge publish `<claude_channel_dir>/.publish.<parent_pid>.lock` in
   `claudechannel.go:265-276`, owned by `J-04` if ported, and Hermes blocking
   `flock` `<home>/cairn/conversations/<sha256>.lock` in
   `integrations/hermes/controls.py:39-44`, owned by `R-03`):
   - **Session lock (`native_session.open_session`, `native_session.py:490-496`):**
     `<coordination.state_dir>/<sha256(binding + "\0" + native_session_id)>.lock`
     guarding `<sha256(binding + "\0" + native_session_id)>.json` (`cairn.native-session/1`),
     non-blocking `LOCK_EX|LOCK_NB` (`0s` wait -> `SESSION_BUSY`; `coordination.py:1490-1499, 1614-1616`).
   - **Grant-ledger lock (`grant_ledger.py:16, 103-116, 147-158`, `inbox_recall.py:459-460`):**
     `<memory.state_dir>/<native_session_id>.lock` guarding
     `<native_session_id>.{inbox-recall,memory-budget}.json` and `.initialized` (no
     `GrantLedger.open_session` exists). Activation (`grant_ledger.py:139-150`) runs
     **outside** `session_lock` (`coordination.py:4475-4485`, `<= 1.5s`). **Nested
     acquisitions under `session_lock`:** (i) hook `_handle` (`lines 3903, 3930`)
     calls `bridge.output(..., outer_deadline=bridge_deadline)` (`lines 4058-4075`,
     `bridge_deadline = now + 12s`), which waits **up to `1.5s`** on `<native_id>.lock`
     (`inbox_recall.py:459-460`) while holding `session_lock` so concurrent memory
     and wake hooks serialize cleanly; (ii) `coordination.py:3814-3816`,
     `grant_ledger.py:535-537` (`recall_grant_evidence`), and `grant_ledger.py:582-584`
     (`codex_pre_grant_ledger_absent`) use `0s` try-locks.
   - **Intent journal lock (`coordination.py:1901-1913`):**
     `<coordination.state_dir>/intents/<attempt_id>.lock` (`30s` wait only in
     standalone `inbox_command` / `compact settle` with no other lock held; `0s`
     try-lock in watcher/hook `flush_journal` under `session_lock`).
   - **Watcher lock (`coordination.py:4497`):** `<config_dir>/.watch.lock`, `0s` try-lock.
   - **Cue lock (`coordination.py:2990-3001`):** `<stem>.cuelock`, `0s` try-lock.
   - **Strict one-way lock hierarchy (`V2-1`):** deadlock freedom is guaranteed by
     strict one-way ordering (`session_lock` -> `ledger_lock` / `0s` `intent_lock`)
     plus bounded waits (`<= 1.5s` on `ledger_lock`). Go memory/ledger holders
     (`memory`, `inbox_recall`, `grant_ledger`, `inbox_binding`, `recall_dispatch`)
     **MUST NEVER** acquire coordination `session`, `intent`, `watch`, or `cue` locks.
   - **Bounded probes under `session_lock` vs lock-free submission/cancel (`lines 764-889, 1315-1351, 1789-1813, 4100-4131`):**
     `prepare_idle_wake` runs **under `session_lock`** and executes bounded (`<= 4s`)
     read-only probes there: Codex `probe_codex_native_health` -> `queue.inspect_thread`
     (`lines 459, 786, 820`), OpenCode capability probes (`supports_idle`,
     `supports_cancel`, `supports_tool_capture` in `wake_transport.py:200-223`,
     `opencode_queue.py:102-129`; `opencode_queue.status` has no production caller),
     and Herdr reads
     (`agent list`, `pane process-info`, `agent get`) plus `session-inbox-ready`
     (`wake_transport.py:347-397`, `coordination.py:265-300`); moving those probes
     outside `session_lock` would open a TOCTOU window and requires a separate
     labelled decision. **Only submission and cancel I/O run outside `session_lock`:**
     Codex queue `add`/`start` (`wake_transport.py:96-136`), Claude channel write
     (`168-189`), OpenCode `prompt_idle` (`238-287`), Hermes `queue_message`
     (`316-337`), Herdr `agent prompt` (`399-406`), and OpenCode `cancel_request`
     (`coordination.py:1789-1813`) — persisting `idle_wake` or `cancel_submission`
     (`status = "uncertain"`) before sending bytes, releasing `session_lock`, running
     submission/cancel I/O, and re-acquiring `session_lock` with compare-and-set
     while tolerating `SESSION_BUSY`.
2. **`owner_process` Trust Anchor (`state["process"]`, `lines 1378-1433`, callers `3033, 3841, 3925`), Custody, Reminders, & Shutdown:**
   - **`owner_process` derivation:** `state["process"]` anchors process-mode peer
     equality, liveness, compact `caller_descends_from`, `herdr_environment`, and
     Codex `fd` lookup. A declared `host_pid` MUST be `int >= 2` (else `INVALID_HOST`)
     **and** MUST appear on the hook's actual `/proc` ancestor walk (a payload PID is
     never trusted alone); otherwise `owner_process` selects the nearest
     non-supervisor ancestor whose `comm` or `exe` basename is in `process_names` or
     whose `exe` is under `claude_exe_dirs` (default `~/.local/share/claude/versions`,
     `lines 1381-1389`), skipping Claude `daemon`, `bg-pty-host`, and `bg-spare`
     supervisors (`CAIRN-178`, `lines 1378, 1392-1406`), bounded at `128` levels
     stopping at `pid < 2`, raising `INVALID_HOST` if an ancestor exits mid-walk.
   - Persist `inbox_intent` and `inbox_result_policy: {claim_request_id, version: 1}`
     (`--task coordination-result/<delivery_id> --run *`) before `session-inbox-claim`
     (`test_inbox_recovery.py:603-737`); reject mismatched Codex turn takeovers with
     `NATIVE_TURN_MISMATCH` (`test_inbox_recovery.py:632-666`).
   - On `STALE_LEASE` during `event-renew`, set `inbox_attempt.lease_lapsed = true`
     and retain the hold (`test_inbox_recovery.py:1054-1062, 1175-1193`).
   - Deduplicate `Stop` reminders by `inbox_ack_reminder_revision`; on second
     unsettled `Stop`, reconcile `turn_ended` and record
     `cairn.unacknowledged-turn-end/1` (`test_inbox_recovery.py:1063-1174`).
3. **OpenCode `CAIRN_REQUEST_ID` Cancel Scan vs Process-Death Retirement (`process_scan.py`, `coordination.py:1740-1786, 2900-2902`):**
   - Cancellation/interrupt signals never clear `inbox_intent` / `inbox_attempt`
     alone, and `inbox_close` (`{request_id, reason}`) is preserved across lost
     replies unless freed by `DELIVERY_ACTIVE`.
   - **Only** the OpenCode cancel path (`scan_opencode_cancel`, `lines 1740-1786`)
     runs `process_scan.markers("CAIRN_REQUEST_ID", delivery_id, since=since)`
     (`process_scan.py:1-79`), scanning every same-UID `/proc/*/environ` for exact
     `CAIRN_REQUEST_ID=<delivery_id>` (catching `setsid`/double-fork escapees while
     ignoring unrelated long-lived children such as `cairn-events`), skipping
     zombies, and reporting `coverage = "unknown"` (`clear = false`) if any
     unreadable/non-dumpable same-UID process started at or after `since = min(opencode_turn_since or process_start, each tool's since)`.
     Requires `turn_stop_state in {"ended", "interrupted"}`, reports `terminal_scan`
     (`"clear"` | `"unknown_remaining"`) via `session-tool-stop`, and releases only
     as `cancel_confirmed` or `exclusivity_revoked` (`core/native_cleanup.go:133-200`).
   - **Process death never substitutes for turn-stop or tool-scan cleanup:** process
     death runs `finish_presence` -> `NativeSession.retire(reason="process_exited")`
     with no `process_scan`, and any pending-cancel hold stays held under store
     `CLEANUP_UNCONFIRMED`.

## Peer identity and wake transport contracts (`J-04`)

Reference source: [`unix_peer_transport.py`](../../../integrations/lifecycle/unix_peer_transport.py) (`lines 70-136, 295-331`),
[`wake_transport.py`](../../../integrations/lifecycle/wake_transport.py) (`lines 378-406`),
[`codex_queue.py`](../../../integrations/lifecycle/codex_queue.py) (`lines 54-68, 113-131, 185, 245-250`),
[`claude_channel.py`](../../../integrations/lifecycle/claude_channel.py) (`lines 51-70`),
[`opencode_queue.py`](../../../integrations/lifecycle/opencode_queue.py) (`lines 92-99, 150-158`),
[`hermes_queue.py`](../../../integrations/lifecycle/hermes_queue.py) (`lines 54-61, 149-168`),
[`coordination.py`](../../../integrations/lifecycle/coordination.py) (`lines 261-300, 1031-1190, 3084-3099`),
[`claudechannel.go`](../../../internal/claudechannel/claudechannel.go),
[`frame.go`](../../../internal/procpeer/frame.go) (`lines 13-41`), and
[`process.go`](../../../internal/procpeer/process.go) (`lines 13-54`).

1. **Per-Mode `SO_PEERCRED` & Per-Harness Endpoint Matrix (`foundation/v4` `8b768649`, `## Shared process and peer contract`):**
   Transports consume `internal/procpeer` (`F-01` extracts bridge/measurement
   primitives; client `procpeer` connect lands in `J-04`'s coordinated shared-file
   slot per `foundation/v4` `## Shared process and peer contract`) before writing
   any request bytes, preserving `unix_peer_transport.py:114-136` (`process` mode
   checks **exact** `peer_pid == target.pid` + `(start, boot)` with no UID check
   unless `expected_uid` is passed; `uid` mode checks `peer_uid == os.geteuid()` and
   re-measures the recorded target `ProcessRef` `(start, boot)`) and reading/writing
   bounded frames **under each consumer's deadline**:
   - **Claude channel (`coordination.py:1031-1047`, `claude_channel.py`, `claudechannel.go`, `internal/procpeer/{frame.go,process.go}`):**
     watcher reads `<claude_channel_dir>/<claude_pid>.json` (`cairn.claude-channel/1`),
     verifies `process_identity(record.parent) == process_identity(session_process)`
     and Python `process_alive(record.process)` (excludes `Z`/`X`), and connects in
     `process` mode requiring **exact** `peer_pid == record.process.pid` + `(start, boot)`
     with **`4s` connect + `4s` reply** client timeouts (`claude_channel.py:33, 61-84`).
     Bridge (`0700` dir, `0600` socket; server-side `SO_PEERCRED` is optional
     hardening) verifies `request.parent == bridge.parent`, `os.Getppid() == parent.pid`,
     and Go `liveProcess` / `procpeer.Live` (`claudechannel.go:49-51`, `internal/procpeer/process.go:20-39, 51-54` — `(pid, start, boot)` without `Z`/`X` exclusion, pinned
     separately per consumer), frame limit `<= 8192` bytes **excluding** `\n`, and
     **`socketDeadline = 2s` on request read and reply write plus `writeTimeout = 2s`
     on MCP notification emit** (`claudechannel.go:39-43, 297, 301, 337-354`, `internal/procpeer/frame.go:13-34, 37-41` — server
     bounds MUST stay strictly below the client's `4s` reply timeout so bridge
     verdicts arrive before client `"uncertain; do not resend"`). **Only
     `server/discover` is refused (`MethodNotFound`)**;
     Claude's fallback legacy MCP **`initialize` MUST succeed** for readiness and
     registry publication (`claudechannel.go:31-37, 123-135, 188-195`).
   - **OpenCode queue & cancel (`opencode_queue.py`, `/tmp/cairn-opencode-<pid>.sock`):**
     `process` mode requiring **exact** `peer_pid == session_process.pid` + `(start, boot)`
     (descendants MUST be refused to defeat `/tmp` squatting); **`4s`** deadline on
     `prompt_idle`, `capabilities`, and `status`, and **`35s`** deadline on
     `cancel_request` (mapping accepted / `CancelRefused(reason)` / `CancelUncertain`).
   - **Hermes queue & abort (`hermes_queue.py`, `/tmp/cairn-hermes-<pid>.sock`):**
     `process` mode requiring **exact** `peer_pid == session_process.pid` + `(start, boot)`
     (descendants refused); **`4s`** deadline on `queue_message`, `status`, and
     `tools_status`, and **`30s`** deadline on `abort`.
   - **Codex queue (`coordination.py:1099-1131`, `codex_queue.py`):**
     accepts **only** absolute `unix://` `--listen`/`--remote` addresses from
     `/proc/<pid>/cmdline` (**no TCP loopback**; non-`unix://` endpoints refuse).
     Direct `app-server --listen unix://PATH` uses `process` mode (`peer_pid == target.pid`);
     interactive TUI `--remote unix://PATH` uses `uid` mode (`peer_uid == geteuid()` +
     recorded TUI `ProcessRef` `(start, boot)` check; state stores no launcher
     `ProcessRef`, so any stronger peer ancestry rule requires a separate labelled
     schema/repair decision). Runs stdlib RFC 6455 upgrade (`GET / HTTP/1.1`,
     `Host: localhost`) over that verified Unix socket with a **`1 MiB` cumulative
     per-connection read cap** (covering handshake + all frames; `unix_peer_transport.py:234-255`,
     `codex_queue.py:59-62`) and **`4s`** RPC deadline. Busy requires `code == -32600`
     **and** exact message `"thread already has an active or pending turn"`;
     `start_outcome` maps `"queued submission not found: <id>"` to `missing` and
     other `-32600` messages to `refused` (`codex_queue.py:54-68, 245-250`).
   - **Herdr terminal wake (`coordination.py:261-300`, `wake_transport.py:378-406`):**
     requires (a) `agent_status in {"idle", "done"}` **and** `focused == false`
     (never prompting a focused owner pane), (b) identity via `agent_session` ID or
     Codex rollout `fd` + foreground `pgid`, (c) pre-prompt `herdr agent get`
     confirming unchanged `(pane_id, terminal_id, revision, state_change_seq)` plus
     `session-inbox-ready` recheck, and (d) `agent_prompted` confirmation on the
     same `pane_id` and `terminal_id`.
2. **Wake Envelope & Closing Protocol Constants:**
   - Producers emit `wake_message(wake)` with `WAKE_CLOSING`; all recognizers
     (`coordination.py:1159-1190, 1278-1295, 3084-3099`, `grant_ledger.py:25-28, 196-243`,
     `transcript_recovery.py:223-232, 594-605`, including `hermes_wake_binding` and
     `codex_queue_rollout_proof` `never_admitted` markers) accept **both**
     `WAKE_CLOSING` and `LEGACY_WAKE_CLOSING` (current first).
   - Claude channel envelopes MUST preserve `source="cairn-events"` and exact struct
     attribute order `native_session_id`, `agent_id`, `execution_id`, `delivery_id`
     (`claudechannel.go:27, 53-58, 315-318`).

## Offline transcript recovery and health modes (`J-06`)

Reference source: [`transcript_recovery.py`](../../../integrations/lifecycle/transcript_recovery.py)
and [`coordination.py`](../../../integrations/lifecycle/coordination.py) (`lines 523, 3394-3407, 4381-4464`).

1. **Path Derivation, File Safety, Bounded Scans, & JSON Decoding:**
   - **Never accept caller-supplied transcript paths or filename globs:** Claude
     derives `<transcript>` from binding `config_home` + held `workspace` +
     `native_session_id` (`lines 56-62`); Codex derives `<rollout>` from the **live**
     owner's single `/proc/<pid>/fd` `rollout-*-<native>.jsonl` under
     `<config_home>/sessions` with zero symlink components, `(dev, ino)` equality
     between `fd` and path, and `process_alive` verified before and after
     (`lines 283-310` — live-owner proof only, not post-death recovery).
   - **FIFO & non-regular file safety:** Python's `O_RDONLY | O_NOFOLLOW` blocks on
     a FIFO before `fstat` (`line 66`, baseline discrepancy); Go `J-06` MUST open
     with `O_RDONLY | O_NOFOLLOW | O_NONBLOCK` (clearing `O_NONBLOCK` after regular
     file + `uid == os.Getuid()` `fstat` succeeds) so FIFOs, symlinks, sockets, and
     devices refuse immediately without hanging.
   - **Pinned `OFFSET:LENGTH:SHA256`, per-path JSONL strictness & bounded scan limits:**
     pinned locators enforce `\n`-framed JSONL boundaries, exact raw-byte SHA-256,
     and valid UTF-8 (`lines 65-98`). JSONL duplicate-key strictness MUST match
     each path's exact source policy (unless an explicit repair is labelled):
     - **Plain `json.loads`:** `last_jsonl_record` (`lines 101-118`),
       `daemon_start_claim_evidence` (`lines 133-141`), and
       `find_codex_refusal_locators` (including the explicitly labelled
       [CAIRN-423 repair](../../native-inbox.md#end-and-recovery) below).
     - **`strict_native_line` skipping failing lines (`strict-skip`):**
       `latest_codex_turn_end` (`lines 313-352`, `335-338`).
     - **`strict_native_line` raising on duplicate/invalid keys (`strict-raise`):**
       pinned `OFFSET:LENGTH:SHA256` records (`lines 65-98`) and the
       `codex_ended_turn_proof` bounded region (`lines 447-463`, `line 461`).
     Bounded scan caps: Claude transcript `<= 256 MiB` (`lines 121-152`), Codex
     locator search historically `<= 16 MiB` (`f15fbc91:integrations/lifecycle/transcript_recovery.py:611-643`), Codex latest turn end `<= 8 MiB`
     (`lines 313-352`), and Codex ended-turn region `<= 8 MiB` + tail `<= 64 MiB`
     (`lines 447-458`).
     **Labelled J-06 repair, CAIRN-423:** automatic refusal locator discovery
     scans the whole captured finite snapshot with bounded reads (at most
     65537 bytes) and at most three retained matching locators, without a file-size
     ceiling. A one-second cooperative monotonic budget returns unknown on
     exhaustion; it is not a hard filesystem-I/O deadline. Keep the 65536-byte
     eligible-record cap, plain-JSON policy, physical offsets/adjacency and
     full-snapshot uniqueness among parseable eligible records, including
     duplicates outside any retained tail. Preserve skipping unrelated malformed
     or oversized physical lines; never parse an oversized line's suffix as a
     new record. Partial EOF or changed snapshot size/timestamps/inode/owner
     returns unknown. This repair preserves exact-record, no-grant/tools/journal
     and no-replay settlement gates; it does not resolve the separate opaque
     bridge ValueError or accept the Go replacement/runtime.
   - **Token-level numeric & UTF-8 parity:** validate raw UTF-8 before decode and
     use token-level number parsing (`UseNumber`) so `1.0` vs integer `1`
     (`type(x) is int`), large integers, lone-surrogate escapes, and `NaN`/`Infinity`
     match Python's exact rejection/acceptance rules (`lines 367-373, 393-396, 467-468, 554-559`).
   - **Inode & size rechecks:** open Codex rollouts recheck `(dev, ino)` across reads
     (`lines 305-306, 398-400, 502-504, 567-569`); note `codex_queue_rollout_proof`
     (`lines 551-608`) decides `never_admitted` from `stat_before.st_size` and
     rechecks only `(dev, ino)` (characterize baseline race; adding `st_size`
     recheck is a labelled hardening).
2. **Exact Recovery & Health Mode Grouping:**
   - **Host-diagnostic mode (no transcript read):** `recover-refused-wake` settles
     from host-recorded `wake_refusal_diagnostic` (`lines 187-211`).
   - **Claude transcript & worker-rebind modes:** `recover-native-refused-wake` binds
     wake envelope, `--config` path, `<= 15s` chronology, and single leased claim
     (no `native_turn_id`, `lines 214-257`). `recover-daemon-start-claim`
     (`transcript_recovery.py:155-184`, `coordination.py:1447-1455, 3271-3288`)
     verifies the bound Claude `daemon` is live (`supervisor_role == "daemon"`),
     locates **exactly one** worker (`find_daemon_worker` / `daemon_worker_of`: `exe`
     under `claude_exe_dirs`, `argv` `--resume <transcript>`, running under a
     `bg-pty-host` parented by that exact `daemon`), scans the bounded Claude
     transcript (`<= 256 MiB`), and **rebinds `state["process"] = worker`**
     (`line 181`) before settling.
   - **Codex rollout, RPC & inspection modes:** `recover-codex-refused-wake`,
     `recover-codex-ended-turn`, and `reconcile-codex-queue-wake` verify live-owner
     rollout records and `CODEX_QUEUE_OUTCOMES`. Normal `reconcile-codex-queue-wake`
     (`coordination.py:3558-3576`) **persists wake custody state** under
     `session_lock` (records `native_health`; for `never_admitted`, `completed`, or
     `cancelled` pops `idle_wake`, `wake_refusal`, and `wake_reconciliation` and sets
     `wake_reconciled`; for `admitted_active` clears `idle_wake` or re-marks it
     queued; otherwise marks `idle_wake` refused with `reconciliation_required`).
     `check-codex-native-health` is a live RPC probe persisting `native_health` under
     `session_lock` (`coordination.py:523, 3394-3407`), not a transcript proof.
     `--check-only` is a global `argparse` flag (`coordination.py:4402`), **honored only
     by four modes**: `reconcile-codex-queue-wake` (`4418`, `3556-3557`),
     `recover-daemon-start-claim` (`4431`), `recover-codex-ended-turn` (`4440`), and
     `recover-codex-refused-wake` (`4449`). `inspect-held-refusal` (`4421-4426`) is
     read-only by design (ignoring `--check-only`). In `/2`, **`recover-native-refused-wake`
     (`4452-4458`), `recover-refused-wake` (`4459-4463`), and `check-codex-native-health`
     (`4405-4410`) accept `--check-only` and ignore it**, settling through
     `settle_refused_claude_wake` or persisting `native_health` (never safe as `/2` dry
     runs); any `/3` Go change (rejecting `--check-only` on those 3 modes or honoring it
     for the two Claude refusal modes) requires an explicitly labelled, reviewer-approved
     `J-06` repair. Only the four honoring `--check-only` forms and `inspect-held-refusal`
     never persist state or call store reconciliation, **but** they acquire the `0s`
     `session_lock` (`coordination.py:3452+, 3711-3714`, so concurrent hooks can observe
     `SESSION_BUSY`), and `recover-codex-ended-turn --check-only`
     (`transcript_recovery.py:526`) and `reconcile-codex-queue-wake --check-only`
     perform live Codex RPC probes under that lock.

## Source-backed fixture matrix and verification gates

**Labelled diagnostic extension, CAIRN-424:** new post-claim bridge refusals carry
closed `refusal_stage`/`refusal_reason` pairs from explicit validation guards;
unexpected exceptions retain a known stage with reason `unknown`. The definition
and consumer filter are `INBOX_REFUSAL_REASONS` and `inbox_refusal_details` in
`grant_ledger.py`. Pinned-loader validation annotates locally without importing a
not-yet-validated member. Keep existing exception classes/chained causes, recall
metadata, public exit/refusal, grant/hold and no-replay semantics. Unknown metadata
is not authority. The selected synthetic fixture is
`scripts/test_native_refusal_diagnostics.py`, using temporary pinned bundles and
a fake CLI; it does not establish a historical refusal's cause, Go parity, native
runtime qualification or deployment acceptance. See the
[diagnostic fields and byte limits](../../native-inbox.md#end-and-recovery).

| Fixture family | Reference source & tests | Required positive, negative, and crash cases |
| --- | --- | --- |
| `J-F1`: Journal `Execute`, CLI, streams & compact | `coordination.py`, `test_inbox_recovery.py`, `test_inbox_drain.py` | Exit/stream table (`4` vs `7` `IDEMPOTENCY_CONFLICT`, `finish-reply` both committed + `STALE_SESSION` replay -> exit `4` (`Q9`), `1 MiB` chars vs bytes, `C3`/`C1` `CompatibilityPreflightError` exit `1` with zero journal/lock/lease/refusal-record/API effects across `hook`, `inbox --context`, `inbox drain`, and `inbox compact` vs `NATIVE_PROMPT_REFUSED` exit `2`, plus joined Claude channel wake preflight exit `1` leaving no claim, pending delivery, and explicit wake custody state); explicit `respond` (`--version 01` vs `1` -> `INVALID_REQUEST`/2 (`Q7`), missing `"version"` -> `INVALID_HOST`/7 (`Q8`), no `expected_ref`) vs `finish-reply` (`expected_ref` + `SCHEMA_MISMATCH`); `/2` bare `cairn-inbox` launcher + `C1` `L`/`V` shell-executed compact `read\|inspect\|settle\|advance` (including non-default root with `CAIRN_INBOX_ROOT` unset succeeding via `V`'s `--config` grandparent, mismatched `CAIRN_INBOX_ROOT` / wrong-config / post-validation config-swap refusing before `load_config`/effects, out-of-tree caller and in-tree mismatched `CODEX_THREAD_ID`/`CLAUDE_SESSION_ID` refusing with `NOT_THIS_SESSION` exit `7` before effects, `inspect` lease renewal, and `advance` `next`-cue rendering) + `STALE_LEASE` re-raise on missing/corrupt settle journal + `CONFIRMATION_MISMATCH` + inner `1\|2\|3\|4\|7`; `drain-<lease>` (no `renew` in file, direct `cairn renew` in stdout); `inbox_recall_wake` raw `U+2028`/`U+2029` + `<>&` + float + key order; real explicit `30s` lock/`30s` send vs watcher `0s` lock/`10s` send deadlines (no hook timeout imposed on `inbox` modes) |
| `J-F2`: `Recover`, `Inspect` (7 readers), `JournalBoundary` & discrepancy | `coordination.py:2255-2496, 3184-3199, 3724-3829`, `native_session.py:376-485` | `0s` lock (`Busy` -> unsettled), `agent_id`-only seeding adopting final `notice`/`response` then pruning on flush, all 7 `Inspect` reader missing/malformed/non-object/sub-field/extension-key policies (`Q1` missing -> settled `True`, `Q4`/`R2` non-object / non-dict `session` / ownerless non-dict slot / missing `attempt_id` `KeyError` seeding escapes, `Q5`/`R1` falsy vs truthy non-dict `completion`, `Q6` extension key `ClaimOnly==false`, `Q10` indexed non-object `watch_once`/hook exit `1`), `Flush("attempt"\|"all")`, `finish_reply` exact-result exception, 3-way `settle` taxonomy across `strict=true\|false`, and both `settled_journal_index` call sites + persistent `None` ref |
| `J-F3`: Peer verification & wake adapters | `unix_peer_transport.py`, `wake_transport.py`, `codex_queue.py`, `claude_channel.py`, `opencode_queue.py`, `hermes_queue.py`, `claudechannel.go`, `internal/procpeer/{frame.go,process.go}` | Exact PID equality on OpenCode/Hermes (`4s` wake/capabilities/status, `35s`/`30s` cancel/abort; child squatter refused); Claude `<pid>.json` (`4s` connect + `4s` reply client vs `2s` read / `2s` emit / `2s` reply-write bridge server) + `server/discover` refused + `initialize` succeeds + `channelMeta` order; Codex `unix://`-only, `uid` vs `process` mode, `Host: localhost`, `1 MiB` cumulative cap / `4s` RPC, exact `-32600` strings; Herdr `focused==false` + `revision`/`state_change_seq` re-get; `WAKE_CLOSING` + `LEGACY_WAKE_CLOSING` across all recognizers (`hermes_wake_binding`, `codex_queue_rollout_proof`) |
| `J-F4`: Presence watcher, 5 BSD `flock` families, `owner_process` & cancel scan | `native_session.py`, `grant_ledger.py`, `process_scan.py`, `coordination.py:1315-1813, 4093-4516` | `watch` without `--config-dir` exits `2`; `.watch.lock` `SESSION_BUSY` & malformed binding exit `1`; `/2`<->`/3` `syscall.Flock` contention (`0644`/`0664` & symlinked locks) + nested `1.5s` ledger wait (held `< 1.5s` wake succeeds, `> 1.5s` wake refuses); bounded probes under `session_lock` vs submission/cancel outside `session_lock` (concurrent hook + `kill -9` between send & persist); `owner_process` (non-ancestor `host_pid`, `< 2`, supervisor skip, `claude_exe_dirs`, `128` bound, mid-walk exit); `process_scan.markers` (`setsid`/double-fork escapee, non-dumpable `unknown`, zombie, process death -> `retire` + `CLEANUP_UNCONFIRMED`) |
| `J-F5`: Offline transcript recovery & health | `transcript_recovery.py`, `coordination.py:3271-3576, 4381-4464` | FIFO non-blocking refusal (`O_NONBLOCK`), symlink (`O_NOFOLLOW`), wrong UID, `1.0` float vs int, `NaN`, lone surrogate, invalid UTF-8, 3-mode per-path JSONL strictness (`plain`: `101-118, 133-141` and refusal discovery; `strict-skip`: `335-338`; `strict-raise`: `65-98` & `461`), `recover-daemon-start-claim` (`0` or `2` workers, outside daemon's `bg-pty-host`, valid worker `state["process"]` rebind), historical bounded scan caps (`256/16/8/64 MiB`) plus labelled CAIRN-423 whole-snapshot refusal discovery with bounded buffers/eligible records, one-second cooperative budget and unknown-on-exhaustion/change/partial-EOF, live-owner `/proc/<pid>/fd` Codex derivation, normal `reconcile-codex-queue-wake` wake-custody mutations vs 4 honoring `--check-only` modes + `inspect-held-refusal` non-persistence (`0s` `session_lock`), and `/2` `recover-refused-wake --check-only` / `recover-native-refused-wake --check-only` / `check-codex-native-health --check-only` ignoring `--check-only` and settling/persisting vs selected `/3` parity or labelled repair |

Required local validation before any Coordination slice is marked `Done` (under owner
decisions `98d46758-c61c-42b4-9912-76cf9dfb0dc0` version `1`, `C2`, `FIRST`, and
source-only test holds are replaced by running tests inside the shared isolated
namespace `scripts/test_hybrid_runtime_operations.py` with no production home, no
production sockets, no network, and no view of host processes, while production
changes still require an explicit grant):
- `python3 -B -m unittest scripts/test_inbox_recovery.py scripts/test_inbox_drain.py integrations/lifecycle/test_native_session.py`
  and `python3 -B -m unittest discover -s scripts -p test_installed_lifecycle_bundle.py`
  (plus relevant `test_wake_refusal.py` / lifecycle / cancellation suites).
- `go test -race ./internal/lifecycle/...` (once packages exist), `make check`, and
  `make test-integration` on disposable PostgreSQL clusters only (never touching an
  operational database; no GitHub Actions workflows).
- Independent candidate commit review by both `J-R1` and `J-R2` (Opus 5.5 Max),
  clean rebase/fast-forward push to `origin/main`, and removal of author worktrees.

## Cross-team handoffs and reconciled `X-01` agreements (`CAIRN-302`)

1. **Shared CLI, `Makefile` & Paired/Sequenced Slots (`Overall` / `agent-1249`, `shared/1` [`README.md`](README.md), [`f04-consumer-decision.md`](f04-consumer-decision.md) `32ca633` + `cca6fb78`, ownership `4a6d3110` v1 + `8a4baa32` v1):**
   - `F-B1` (`F-01`) owns initial lifecycle command registration in `cmd/cairn/main.go`
     and `cmd/cairn/commands.go`; `S-B2` owns initial lifecycle `Makefile` targets.
   - **`F-04` (`F-B3`) compatibility-only caller & issued-command closure slot (`C1`–`C3`):**
     after `F-01` hands over the binding/CLI boundary, `F-B3` owns
     `integrations/lifecycle/inbox_binding.py`, original-event + checked-config-snapshot
     activation plumbing in `memory.py` and `coordination.py`
     (`cairn lifecycle binding activation --entrypoint memory|coordination --config <abs> --config-sha256 ... --binding-sha256 ... --receipt ... --receipt-sha256 ...`,
     child Go alone acquiring activation lock, `C3` code-trust preflight before source
     admission and `/3` binding-data reads, activation-prefix skip continuing ordinary
     retained frontend policy, and enabled hooks continuing through the independently
     pinned compatibility receipt closure), **`inbox_handoff` wrapper construction**
     (preserving unchanged direct `context.read` `cairn agent ... history`, `drain`
     `cairn renew`, and inner `commands.{complete,ack,respond}` Store CLI argv;
     `inbox --context` stays outside `F-B3`'s slot with `/2` owner-only parity after `V`
     revalidation), **`inbox_cue_text` drain rendering**, **`inbox_context`
     compact/fallback/next-cue rendering** (`L inbox compact read|inspect|settle|advance`,
     with `L` `exec`-only under an `ENV`/`BASH_ENV`-qualified shell and `V` preserving
     caller `/proc` ancestry + `CODEX_THREAD_ID`/`CLAUDE_SESSION_ID`, compact `inspect`
     qualified as a session-issued lease-renewing operation distinct from `J-06` offline
     inspection, no hook timeout imposed on `inbox` modes, and fresh `L`/`V` preflight
     failures exiting `1` with fixed `CompatibilityPreflightError` and zero effects), and
     **the minimum checked-selection guard at `compact_inbox_command` config resolution**
     (deriving lookup `root` from `V`'s selected `--config` grandparent when
     `CAIRN_INBOX_ROOT` is unset, requiring `CAIRN_INBOX_ROOT` when set to resolve to
     that same `root`, binding `root`/`config_path`/context to `V`'s selected `--config`
     before `load_config` or effects, and consuming checked config bytes). Coordination
     explicitly agrees to this exact consumer boundary, retains sole ownership of
     command grammar, claim, journal, native-session, and watcher/recovery transitions,
     and defers overlapping Python frontend / `J-05` rewrites until `F-04`'s reviewed
     caller lands.
   - **`J-04` paired `procpeer`/wake-consumer slot:** `F-B1` owns the
     `internal/procpeer` client connect addition and `J-B2` owns its first actual
     wake-transport consumer (`internal/lifecycle/wake`) in separate non-overlapping
     files, naming exact files and one combined candidate/test boundary before coding,
     reviewed independently by **`F-R1` and `J-R2`** (plus `J-R1` on Coordination
     CLI/refusal rules), and integrated together (no unused helper-only `main` commit).
2. **Foundation `F-01` & `F-04` agreements (`agent-1267`, [`foundation.md`](foundation.md) `foundation/v4` `8b768649`, sources `89e8c1d6` v1 + `5d50aa56` v1):**
   Coordination sent explicit Cairn agreements (`deb6c59a`, `e7977c58`, `14f74fd1`,
   `9639d71b` v1, plus the `coordination/v6` consumer-boundary closure `e9e81fc2`, carried forward unchanged in `coordination/v7`) covering: `F-04`
   real activation/shared-state caller preceding `F-02`/`F-03` and `J-02` (no `J-05`
   dependency); `J-05` delivering native-session orchestration together with the
   compiled `session.JournalBoundary` and `journal.SessionJournalAdapter` (`HC-04`
   accepting transition policy, `J-05` defining external `ctx` cancellation before
   any cancellable `Flush` caller runs, and Coordination/`recall_dispatch.py`
   retaining owned fixed error-family mappings); per-mode `process` vs Codex
   `--remote unix://` `uid` peer checks; the 5 coordination lifecycle `syscall.Flock`
   paths (`0644`/`0664` and symlink lock preservation per `foundation/v4`
   `### Mixed-language lock coexistence`); and `C1`–`C3` compatibility launch (`V` /
   short immutable launcher `L` expanding byte-for-byte to `V + ["--", *tail]`, subject
   to Foundation `F-R1` + Release `R-R1` staging/readback agreement on `L`).
3. **Release `/2`–`/3` coexistence, immutable `C2` refresh & drain seam (`agent-1270`, `release/2` `aa0c26f` [`release.md`](release.md)):**
   Notified via Cairn (`3ea3e78e` + `6f480aa6` v1): (a) existing immutable `/2` copies,
   already-issued `/2` `cairn.session-inbox/1` / drain argv, bare `PATH` `cairn-inbox`
   launchers, and `cairn.compact-inbox-handle/1` handles (`TTL 3600s`) remain untouched
   and usable alongside `V`/`L`-launched compatibility cues until qualified retirement
   (`R-08`); (b) under `C2`, compatibility binding/receipt/`L` selections are immutable
   (no in-place disable; publishing disabled `B` does not disable cached `A`; supported
   per-session refresh/readback of `B` precedes `D1`/`D6`, and retained per-turn `true`
   activation grants still require ordinary owning-session settlement); and (c) `J-03`
   `Inspect()` (`JournalStatus`) supplies the side-effect-free read-only fact seam
   (`PendingReplay`, `ReplyPossible`, `UnmaterializedFinishReply`,
   `HasUsableFinishReplyRef`, `ReadErr`, `ParseErr`, `NonObject`) for Release `D1`–`D7`
   drain verification without invoking `Flush`. `X-01` (`CAIRN-302`) remains open and
   all dependent runtime implementation remains gated.
4. **Discrepancy disposition (`J-01` -> `J-03`/`J-05`/`J-06`):** `J-01` characterizes
   unmodified Python (`settled_journal_index` across both call sites + persistent
   `None` ref, `watch --config-dir`, `agent_id`-only seeding, indexed non-object
   journal shared-watcher crash-loop, `--check-only` accepted and ignored by
   `recover-refused-wake` / `recover-native-refused-wake` / `check-codex-native-health`,
   and FIFO open hang); any behavior change in Go (such as per-session non-object
   containment in `J-05` or rejecting/honoring `--check-only` on those 3 modes in
   `J-06`) requires an explicitly labelled, reviewer-approved repair with its own
   regression test (keeping `CAIRN-276`'s deliberate compatibility-limitation option
   open where unbounded replay would wedge wakes).
