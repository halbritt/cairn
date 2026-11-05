# Native owner input for quiet notices

Cairn's quiet notices require a positive observation of a new owner input. The
supported contract is the carried Codex 0.166.0 patch in
`patches/codex-owner-origin/`. An ordinary user-role message, `turn_trigger`,
client name, or caller-selected origin field cannot establish that observation.
Unpatched Codex and the current Claude, OpenCode, Hermes, and Agy integrations
have no supported organic-turn capability. They retain ordinary inbox delivery.

The patch is based on upstream `rust-v0.166.0`, commit
`6ff1e1ca3b30fc8b914720681fcf5fe62e95f856`. It preserves the existing launcher:
one private Unix app-server, the TUI connected with `--remote`, and independent
ordinary queue connections. This repository carries source and reproduction
instructions. It does not install a binary or change a running session.

## Trust and input identity

With an explicit verified manifest, `launch-codex-coordination.py` generates a
random 32-byte connection proof. Separate inherited Unix socket descriptors
carry it to the app-server and TUI; only descriptor numbers appear in argv.
The bounded frame contains the contract magic, proof, and exact private socket
path. The native startup reader applies an absolute 250 ms read deadline, closes the descriptor,
and limits the proof to that endpoint. The proof is never an environment value,
a hook field, an on-disk credential, or a tool-subprocess descriptor.

The TUI presents the proof during its Unix WebSocket handshake. The app-server
marks the resulting connection internally. Missing proof means an ordinary
connection; incorrect proof is refused. Both handshake futures suppress tracing
for their complete poll lifetime, including failure, because the pinned
WebSocket dependency traces serialized headers. The header is also marked
sensitive. A public `ownerInputId` field is discarded on ordinary connections.

A UUID is minted only by the actual composer key handlers when they submit or
queue input. Ordinary internal submissions and automatic startup draft replay
stay unknown. The source UUID is separate from the existing client message
acknowledgement ID, and remains attached to queued input, steer retries, and
reconnect. Different source UUIDs are never merged into one privileged input.
Editing a draft and physically submitting it is a new owner action and receives
a new UUID.

Core records the disposition in its accepted-input branch before enqueuing input
or starting hooks. The hook retains its existing exact native `turn_id` and adds:

```json
{
  "owner_input_origin": {
    "version": 1,
    "source": "owner_composer",
    "admission": "fresh_start",
    "input_id": "a canonical nonempty UUID"
  }
}
```

`source` is `owner_composer` or `unknown`. `admission` is `fresh_start`, `joined`,
`recovery`, or `unknown`. Only the positive `owner_composer` + `fresh_start` pair
with a nonempty input ID can qualify. An owner input accepted into an already
active turn is `joined`. Process-wide source UUID history prevents reconnect,
replay, or thread changes from making an accepted input fresh again; duplicates
become `recovery`. At 65,536 accepted identities the process keeps its existing
history and declines new positive observations. Serialized input metadata omits
trust, so restoration cannot confer it.

Each native `UserPromptSubmit` also carries `owner_input_attestation`. Its
`payload` is the exact JSON encoded with unpadded base64url; `signature` is an
Ed25519 signature over the ASCII domain `cairn.owner-input-attestation/1\n`
followed by those decoded bytes. The payload binds the launch UUID, Store
restore generation, `issued_at_unix_ms`, native session and turn, and every
origin field. The timestamp comes from the native clock at hook observation. It contains
no prompt text. Core creates the envelope from accepted metadata before hook
dispatch; the generic hook runner never signs caller-supplied JSON.

A separate inherited socket gives only the app-server a fresh signing seed.
The app-server returns its public key. The launcher registers that public key
through `cairn native-owner-launch-register`, using the trusted operator channel,
and returns the Store's generation over the same private socket. Native startup
requires that acknowledgement and closes the socket before hooks or tools run.
The handshake has a 15-second deadline. The launcher starts the TUI only after
registration succeeds. A failed handshake stops both startup and its child
process; an already committed public-key registration can remain unused.

Cairn still owns admission and delivery. Operator registration fixes a launch's
public key, profile, binding, and generation. Lifecycle registration reserves
`(launch_id, native_session_id)` for one exact agent and execution before any
signed input arrives. The reservation cannot move to a replacement execution;
resuming that native session in a new execution needs a new launch key. A new
native session in the same server can reserve its own binding. The first valid
signed observation activates capability; capability is unsupported before then.
All signed dispositions may activate the reservation, but only fresh owner input
can admit a quiet batch. Store checks signature, generation, and exact identity
again at admission, and input UUID reuse cannot admit another batch. A new root
requires a signature at most 30 seconds old and at most 5 seconds in the future.
An exact existing-root retry retains its original decision and cutoff after that
window; it cannot admit notices that arrived later.

Origin JSON or matching process ancestry cannot authenticate a direct bearer
claim or manually invoked hook. Store requires a valid signature for the exact
observation and applies the replay checks above. Public metadata does not replace
that signature. This boundary assumes the host operator
channel and selected runtime are trusted. A hostile process with the same UID
that steals operator database access or reads native process memory is outside
this contract. Native observations do not acknowledge inbox items or prove task
success.

## Artifact verification and lifecycle binding

Supported launches require both `--owner-origin-manifest PATH` and
`--owner-origin-host-config PATH`. The manifest
is an owner-controlled regular file, with exact schema
`cairn.codex-owner-origin/1`, `contract: codex.owner-input/1`, integer `version: 1`,
`attestation_contract: cairn.owner-input-attestation/1`, the pinned upstream
commit, final patch SHA-256, absolute native binary path,
and native binary SHA-256. It also requires `code_mode_host` with the absolute
`path` and `sha256` of the adjacent `codex-code-mode-host` executable. Both
executables are packaged from the same pinned source build. The package directory
and helper must belong to the current user and prohibit group/other writes;
the helper must be a regular executable with the expected digest. A missing,
moved, writable, or mismatched helper refuses startup. The manifest selects an operator-approved executable
by path and digest. Use
the direct native artifact produced by the reproduction helper for the supported
deployment; a wrapper does not preserve the native process identity required by
the lifecycle binding. During coordinated interactive startup, an explicit
mismatch is fatal before children launch. Without
both arguments the existing launcher operates normally with no organic capability.
Direct CLI commands and explicit `--remote` endpoints preserve their ordinary
route and receive no proof or launch metadata. When owner-origin options are
selected, these direct invocations also discard operator connection settings.

The launcher holds the selected executable inode open through probe and both child execs,
so symlink or atomic path replacement cannot substitute another executable.
That descriptor remains only in the launcher. It hashes the artifact and compares its native
`--cairn-owner-origin-capabilities` response against the manifest. That response
has schema `codex.owner-input-capabilities/1` and reports contract, version,
upstream commit, attestation contract, and a compile-time final patch digest. The reproduction helper
hashes the final patch before compilation and supplies its stamp through the
build environment, avoiding a self-referential source hash.

Nonsecret child metadata identifies the selection:

- `CAIRN_CODEX_OWNER_ORIGIN_MANIFEST`
- `CAIRN_CODEX_OWNER_ORIGIN_RUNTIME_SHA256`
- `CAIRN_CODEX_OWNER_ORIGIN_CONTRACT`
- `CAIRN_CODEX_OWNER_ORIGIN_LAUNCH_ID`

The host config is an owner-controlled regular JSON file, at most 16 KiB, with
exact schema `cairn.codex-owner-origin-host/1` and fields `cairn_path` (absolute
selected Cairn executable), `repo` (absolute configured repository),
`profile_principal` (existing profile principal), and `binding` (the lifecycle
binding name). It contains no credentials. The operator helper receives only
public registration fields on stdin and inherits the launcher's operator
connection environment. The native probe, app-server, and TUI do not inherit
`CAIRN_DATABASE_URL`, `CAIRN_TEST_DATABASE_URL`, `CAIRN_HOME`, `CAIRN_OPERATOR_*`,
or `PG*` variables. The launcher does not read profile token files or provision
credentials.

These are discovery hints. The launcher verifies the artifacts; Store verifies
the signed observation against the operator-registered key and exact lifecycle
reservation. The lifecycle adapter passes bounded signed observations without
conferring authority from environment values or process ancestry. Deploy to a new protected
artifact directory and retain it for the session lifetime. Do not replace a
selected executable in place. A replaced process, incompatible contract, lost
binding, restored execution, or missing origin observation must fail admission
closed and leave unadmitted quiet notices queued.

## Reproduction and verification

Use an isolated clean clone under the cache, checked out at the pinned commit.
The helper refuses a dirty or different source and an existing output directory:

```sh
python3 patches/codex-owner-origin/build.py \
  --source "$HOME/.cache/agent-native-sources/codex-owner-origin" \
  --output "$HOME/.cache/cairn-native-builds/codex-owner-origin"
```

The helper applies the verified patch and builds both executables with Rust
1.99.1. It follows the pinned upstream `setup-rusty-v8` recipe: the V8 release
checksum manifest must match its hash in the pinned source, then both the
archive and generated bindings must match that verified manifest. The helper
records those hashes in the build receipt. It checks the native capability,
strips debug information from the packaged copies, and writes both executables, the manifest, and
build receipt outside the repository. The upstream release tag's lockfile still
names workspace packages `0.0.0`; the carried patch normalizes those names to the
release's `0.166.0` workspace version. It does not update external dependencies.

Focused checks cover the launcher lifecycle and native composer, queue, Core
admission/recovery, transport authentication, and tracing behavior. Native checks
and their final executed results are recorded in the patch bundle's README.
An API-driven inbox fixture alone does not validate actual composer origin.
