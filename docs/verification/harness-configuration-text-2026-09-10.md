# Harness configuration text — 2026-09-10

MCP startup and harness setup now refuse malformed text and overlong scope
identifiers before producing a configuration or changing installed files.
This repairs two reproduced setup defects:

- All three MCP configuration generators accepted task IDs longer than the
  store's 256-byte limit, producing configurations whose searches would fail scope validation.
- The OpenCode generator and native installer converted invalid UTF-8 to U+FFFD,
  changing revision values and credential paths. Codex and Claude generators
  already rejected those invalid byte strings.

The installed baseline was `797aafe`. Reproductions used synthetic flags, missing
credentials and an isolated installation directory. The JSON encoder escaped the
replacement character as `\ufffd`: a first scan of raw output found no literal
replacement byte sequence; decoding the configuration exposed the changed value.
The [manifest](harness-configuration-text-2026-09-10.json) records both observations.

## Repair and preservation

The existing MCP `Config.Validate` now enforces the store's scope byte limit and
checks scope/context text before server creation. The CLI checks credential/socket
path text before opening the client. Shared command construction validates the
emitted argument vector before JSON/TOML encoding. The native installer checks
selected values and resolved paths before file changes. Existing Codex/Claude
UTF-8 checks moved to their shared command path; Claude's environment-expansion
restriction remains separate.

Configuration text must be well-formed UTF-8 and contain no NUL. Explicit scope
identifiers retain their existing nonblank/non-wildcard rules and now enforce
1–256 bytes at setup. Native Codex thread metadata keeps its existing limits;
its task/run settings remain omitted. Valid Japanese text, emoji, intentional
U+FFFD, literal backslash escapes, spaces and case are preserved. The API still
validates the semantic meaning of context pins when they are used.

No schema, API request, stored note, sensitivity rule or capture encoding changed.
Earlier altered configurations cannot be identified from a replacement character
alone; this repair does not rewrite them. Upgrade the CLI used for setup and
restart an MCP process when adopting new startup settings. Existing valid settings
and the running API remain usable.

## Verification and guidance

Focused Go tests first failed on malformed OpenCode output, native installation,
and startup scope/context, then passed with the repair. Boundary tests cover
256-byte Unicode labels, invalid text across setup fields and no installation
effects on refusal. An actual offline CLI check decodes all three generated
formats, verifies exact valid values, refuses unusable settings before credential
access, and preserves an existing native installation under invalid `--replace`.
Static checks and 43 Python tests passed. The standard disposable PostgreSQL
integration result is recorded in the manifest.

Three runs with optional native checks timed out after 60 seconds: first in the
large-note session, then twice in the recent-file session. The unchanged
large-note session passed in isolation; a separate isolated run passed all five
recent-file cases, including permission refusals and retries. The diagnostic full
run retained its temporary artifacts after stopping its own PostgreSQL cluster.
Its failed baseline emitted no tool events and received only a title-generation
request; the last runtime log entry concerned snapshot tracking. No remaining Git
process was found on follow-up. Attaching a syscall tracer was refused by the
host. These observations do not identify the cause or establish a full native
suite pass. No timeout was increased and no automatic retry was added. The native
adapter source is unchanged by this repair. These are scripted transport checks,
with no answering-model inference.

Saved setup procedures were consulted before source review. Direct comparison of
the generators exposed the inconsistency. Retrieved Unicode lessons then supplied
the warning that an API cannot recover text already changed by a serializer, and
that intentional U+FFFD must remain valid. Source, existing conversation and the
investigator's own knowledge also contributed. This is a verified setup repair;
no independent or incremental memory-value claim is made.

Pincite informed error ownership and preservation boundaries. Its validated
packet, typed evidence, decision receipt and residual obligations are retained
with the verification metadata. Scratch evidence is under
`/tmp/cairn-mcp-setup-20260910`; it is not a durable archive.


## Installation

Clean CLI `208c1c8` is installed after both CI jobs passed for that exact commit
([run 34554012895](https://github.com/halbritt/cairn/actions/runs/34554012895)).
The installed binary passed the offline configuration check again. Native adapter
bytes and configuration stayed unchanged; its source remains `797aafe`. The API
remains `b171a8b`, with no service restart or schema change. The installation
preserved every stored note version; a subsequent selected append extended the
existing Unicode lesson from version 3 to 4 and verified its prior body exactly.
The manifest records installed identities and that deliberate note update.
