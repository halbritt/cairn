# Codex owner-input patch

This bundle supplies `codex.owner-input/1` for Cairn quiet notices on a local
Codex TUI served by the coordination launcher. The pinned upstream source is
`rust-v0.166.0`, commit `6ff1e1ca3b30fc8b914720681fcf5fe62e95f856`.

- `source.json` binds the upstream source, canonical patch, and build toolchain.
- `owner-origin.patch` contains the native implementation, focused tests, and
  generated protocol artifacts.
- `contract.json` records the proof frame and native hook contract.
- `runtime.json` records the selected validation build. Each reproduction emits
  its own artifact digest and deployment manifest; identical binary bytes across
  build directories are not promised.
- `build.py` verifies and applies the patch to an explicit clean source checkout,
then emits both required executables and a manifest outside the repository.

[Native owner input](../../docs/native-owner-origin.md) explains the launcher,
connection proof, input identity, and required Cairn lifecycle binding. Public
API and ordinary queue clients cannot establish owner origin. The launcher
requires an explicit manifest and host config before it creates a proof; omitting both
retains ordinary queue service with no organic-turn capability.

## Reproduce the selected source

Use a clean clone under the cache at the exact commit above. Do not use an
installed runtime checkout. Run from the Cairn repository:

```sh
python3 patches/codex-owner-origin/build.py \
  --source "$HOME/.cache/agent-native-sources/codex-owner-origin" \
  --output "$HOME/.cache/cairn-native-builds/codex-owner-origin"
```

The helper requires Rust 1.99.1 and a new output directory. It sets
`CARGO_TARGET_DIR` to the source checkout's `codex-rs/target`, applies the patch,
and uses Cargo's locked dependencies. The selected target is
`x86_64-unknown-linux-gnu`. Before compiling, it follows the pinned upstream
`.github/actions/setup-rusty-v8/action.yml` recipe: download the Codex-built V8
152.2.0 checksum manifest, verify it against the hash committed in the pinned
source, and verify the archive and generated binding against that manifest.
The build receipt retains this checksum chain. No dependency revision fallback
is used. GNU `strip --strip-debug` removes debug
information from the packaged copy; the source build retains it. The upstream tag names workspace packages
`0.0.0` in its lockfile while its workspace version is `0.166.0`; the patch
normalizes those workspace entries and adds dependency edges to existing locked
packages. External dependency versions stay unchanged.

The resulting `codex`, `codex-code-mode-host`, `manifest.json`, and `build.json`
stay outside Cairn. The helper is required by the runtime's default code mode;
an artifact containing only `codex` is insufficient for a complete workflow.
The capability probe binds the compiled artifact to the final patch digest
supplied at compilation. The helper refuses a dirty checkout, a different
upstream commit, a changed patch receipt, or an existing output directory.
It fingerprints tracked and untracked source inputs after applying the patch
and compares them after compilation and packaged probing. The build receipt
contains the full input manifest and its digest; source or patch drift prevents
manifest publication.
It leaves the applied source available for inspection. Remove that scratch
checkout after validation and review.

## Executed component checks

The focused checks below passed on the pinned source on 2026-11-05. Run them
from the patched source's `codex-rs` directory. The stack setting follows
upstream's test recipe.

```sh
export RUST_MIN_STACK=8388608
cargo +1.99.1 test --locked -p codex-app-server-transport --lib owner_ingress
cargo +1.99.1 test --locked -p codex-app-server-client --lib owner_ingress
cargo +1.99.1 test --locked -p codex-tui --lib chatwidget::tests::composer_submission
cargo +1.99.1 test --locked -p codex-core --lib owner_input
cargo +1.99.1 test --locked -p codex-core --test all owner_input_hook_records_fresh_replay_and_unknown_before_model_work
cargo +1.99.1 test --locked -p codex-app-server --test all owner_input_identity_cannot_be_forged_by_api_or_queue_text
cargo +1.99.1 test --locked -p codex-cli --bin codex owner_origin_descriptor_parses_for_owned_server_and_remote_tui
cargo +1.99.1 test --locked -p codex-hooks generated_hook_schemas_match_fixtures
cargo +1.99.1 test --locked -p codex-app-server-protocol --lib schema_fixtures_tests
```

| Check | Result | Behavior exercised |
| --- | --- | --- |
| Inherited proof | 4 passed | Exact frame, descriptor closure, malformed descriptor, stalled and trickled deadlines |
| Unix handshakes | 1 passed | Owned connection and reconnect, ordinary client, forged proof, successful and rejected handshake trace redaction |
| Composer submission | 67 passed | Physical Enter versus automatic same-text replay, queued identity, rejected-input separation, reconnect, existing composer behavior |
| Core admission and signing | 7 passed | Joined input stamping, duplicate recovery, saturation, serialized metadata losing trust, signed field integrity, generation acknowledgement and descriptor closure |
| Real Core hook | 1 passed | Fresh, replayed, and unknown origin signed before model work, with verified session, turn, generation, and timestamp |
| Real API and queue | 1 passed | Forged public identity and same-text automatic queue dispatch both remain unknown |
| CLI parser | 1 passed | Descriptor argument reaches private app-server and remote TUI |
| Hook schema fixture | 1 passed | Generated hook schema matches the carried fixture |
| Protocol schema fixtures | 4 passed | Stable JSON/TypeScript and precomputed stable/experimental exports match their generators |

The stable and experimental native protocol exports and Python SDK models were
regenerated with their upstream generators. From Cairn, these checks also passed:

```sh
python3 scripts/test_codex_launcher.py -q
make check
```

The launcher suite passed 20 tests, including paired proof delivery, explicit
manifest refusal, capability probe bounds and child cleanup, executable path
replacement during probing, operator registration refusal, operator environment
removal in coordinated and selected direct modes, unsupported mode, and the
existing launcher lifecycle.
These component results establish their listed seams. The complete native
workflow is checked separately with the staged Cairn lifecycle adapter and a
fresh disposable Store:

```sh
python3 scripts/check_codex_organic_native.py /path/to/candidate/cairn \
  --native-manifest /path/to/complete/package/manifest.json \
  --report-directory /path/to/new/proof-output
```

The fixture uses the actual terminal composer, the normal private Unix queue,
default code mode and its packaged helper, and a local scripted Responses
server. It enables the feature through the operator CLI. It records exact
source, delivery, lease and acknowledgement identities, checks the three-item
limit and frozen cutoff, and rejects forged base-profile and native tool-child
claims. A separate fault injection withdraws a source through the audited
operator transition after a duplicate-root claim commits; retirement must
recover the actual older attempt from Store control and fail its unfinished
delivery without inventing an acknowledgement.

Successful reports bind the candidate, fixture, native manifest, both native
executables and staged lifecycle bundle. They measure protocol behavior, not
model usefulness. No installed binary or running owner session is changed.

The seven-stage workflow passed on 2026-11-05 against the complete package
recorded in `runtime.json`, including exact older-attempt retirement after
source withdrawal. The run used 19 local scripted HTTP requests and removed
its disposable Store. The associated lifecycle suite passed 53 tests, launcher
suite 20 tests, and build-input verification suite 2 tests; `make check` passed.
Store integration coverage is recorded separately by the Cairn change that
supplies the admission and cached-disclosure guards.
