# Native Claude hook selection fixture

`scripts/check_r04_native_claude.py` checks staged lifecycle selections in an
explicitly selected Claude executable. It runs in the existing hybrid-runtime
isolation wrapper, with disposable homes, a local scripted Messages provider
and a synthetic Cairn API. It uses no model account or production installation.

The fixture stages immutable selections A and B with the real R-02 installer
and R-04 hook renderer. For unbound selections the current renderer uses checked Go
for recall and tool observation with H13, and `memory-capture` for PreCompact and
SessionEnd with H150. Select `--profile bound-agent-tools` to use the existing
enabled binding with `agent_tools` recall: SessionStart and UserPromptSubmit
select checked Go `memory` with H13 and the same eight flags/stdin. The producer
also selects Go for bound `agent_tools` PostToolUse/PostToolUseFailure; other
bound tool modes retain Python. The fixture now preserves the original eleven
start/prompt cases and adds two actual native Read cases per profile: one
successful read and one missing-file failure. Earlier eleven-case observations
do not qualify these new tool cases. The default `unbound` profile stays unbound.
Historical eleven-case observations used Python memory; current route claims
require a fresh run of the selected profile. Coordination reaches the receipt-checked
private Go image. `strace` binds each
startup, prompt and tool hook's launcher to the program and literal argv it executes in the same PID,
and checks that process's exit status. Native settings and hook-listing
readback must match the selected generation. Unexpected generation launchers,
startup hook errors and SessionEnd failures fail the fixture.

The eleven cases cover fresh and existing A, a separate control conversation,
cached A after staging B, fresh B, resumed B, the unchanged A control, missing
and corrupt receipts, cached B before reversal, and resumed A. Receipt refusals
must leave Cairn state and API calls unchanged. A native model response can
still succeed after a hook fails; the child process's refusal is checked
separately from that response.

The five original startup controls retain empty tools and plain-text provider
responses. An additional isolated conversation offers only Read, with permission
rules for two exact synthetic workspace files. The local provider emits each
Read once and requires its matching native `tool_result` before ending the turn.
The existing strace transport additionally captures reads, writes and thread
creation for this conversation. Payload dumps are restricted to stdin, stdout
and stderr; regular binary reads retain syscall metadata without their contents.
Selected stdio hex dumps must have complete byte counts and contiguous offsets.
The joined hook process's stdin must contain
the native session, Read input, tool-use ID and success/error event; stdout,
stderr and exit status come from that process. A successful assistant message
alone cannot qualify tool execution. Native hook or context errors fail the run.

For each captured tool callback, the fixture restores its exact pre-tool memory
state and runs the retained Source launcher with the observed stdin. It keeps
both raw results and uses the existing full state/file/trace comparator without
widening its normalization. A canonical UTF8 ledger containing Japanese, emoji
and integer `900719925474099312345` must retain every byte. Tool callbacks must
make no recall API calls. Before each native process starts, the fixture records
the selected receipt, all Source closure members and actual program/client pins.

Refresh closes and joins the target process, then invokes native `--resume`
with the same conversation UUID, account and working directory, plus the exact
selected `--settings` file. The fixture checks the conversation UUID and prior
prompt history after both refresh and reversal. Its control process stays on
A. This establishes a process restart with conversation continuity for the
selected client version. In-process settings patches, filesystem watchers,
interactive trust and MCP reconnection remain outside this check. The Go image
is unchanged between selections; this is not a code-image upgrade or a full
watcher-unit drain and activation proof. Full R-04/R-06 and production
qualification remain separate.

Select an executable and its expected SHA-256, and create an evidence directory
outside the checkout. For example, with those paths in task-specific variables:

```sh
mise run host:exec -- --timeout 10m --weight 1 -- \
  python3 scripts/test_hybrid_runtime_operations.py \
  --timeout 480 \
  --receipt "$CAIRN_R04_EVIDENCE/isolation.json" \
  --ro-bind "$CAIRN_R04_CLAUDE:/opt/r04-native/claude" \
  -- python3 -B scripts/check_r04_native_claude.py \
  --claude /opt/r04-native/claude \
  --sha256 "$CAIRN_R04_CLAUDE_SHA256" \
  --profile bound-agent-tools \
  --archive-stdout > "$CAIRN_R04_EVIDENCE/run.log" 2>&1
```

With `--archive-stdout`, the output includes a JSON line containing
`archive_base64`: a gzip tar archive of selected traces, protocol events,
settings readbacks, synthetic requests and observations. Decode it outside
Git, including on failure. A successful run includes `report.json` with the
fixture hash, client version/hash, executed program hashes, selections,
refusals, refresh transitions and `cleanup_passed`. The wrapper's separate
receipt binds the command and source checkout to its isolation self-checks.
Homes, tokens, databases and executable binaries are excluded from the archive.
The native executable must be frozen before use and read-only bound into the
wrapper; its hash is checked before and after execution. The archive retains
built-image pins, selected invocation traces and separate startup observations.
Disabled-bound `agent_tools` Source parity and owner/repeat/wake assertions stay
in `test_r04_bound_provider_wiring.py`; a disabled manifest does not establish an
enabled native binding.
