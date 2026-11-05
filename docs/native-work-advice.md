# Native work advice adapter

This adapter carries an independent patch to Codex `rust-v0.166.0`, commit
`6ff1e1ca3b30fc8b914720681fcf5fe62e95f856`. It does not include the owner-input
attestation patch. Its command sends one generation request through an existing
ChatGPT subscription login. It does not start a Codex agent session.

Deterministic checks exercise local HTTP fixtures and the packaged executable.
They do not establish live subscription availability or proposal usefulness.
Registration requires the packaged executable and its verified build receipt;
an ordinary installed Codex executable does not provide this contract. Cairn's
input eligibility, proposal validation and application remain separate boundaries.

## Invocation and identity

Register the exact executable, its SHA-256, its manifest, an existing `CODEX_HOME`,
and a fixed full model name. Run from a private empty working directory with the
Cairn runner's allowlisted environment. Pass arguments as an array:

```text
codex cairn-advice --model FULL_MODEL --timeout-ms 90000 --max-output-bytes 65536
```

No profile flag, root configuration overrides, remote server, or provider selector
is supported. The existing configuration home selects native credential storage
and administrator policy. Project configuration and execution rules are excluded.
Cairn credentials are never passed to this child. Native login refresh may update
its existing credential store; this command does not log in or provision a profile.

`codex cairn-advice --capabilities` reads no prompt, profile or credentials and
makes no network requests. It returns one object:

```json
{"schema":"cairn.codex-advice-capabilities/1","request_schema":"cairn.codex-advice-request/1","result_schema":"cairn.codex-advice-result/1","provider":"openai","auth_method":"chatgpt_subscription","one_request":true,"tools":false,"retries":false,"upstream_commit":"6ff1e1ca3b30fc8b914720681fcf5fe62e95f856","patch_sha256":"64 lowercase hexadecimal characters"}
```

A development build reports `unverified` for its patch digest and cannot satisfy
artifact registration. Capability output is checked against the independently
selected manifest and executable hash; self-reported capability alone is not trust.

## Request and result

Stdin contains exactly one JSON object, followed by EOF. Unknown fields and trailing
objects are rejected. The request ID is a canonical nonnil UUID.

```json
{"schema":"cairn.codex-advice-request/1","request_id":"431bda54-eb71-41ac-8412-2c47617cc851","prompt":"Selected, authorized advice input"}
```

Success requires exit status zero and exactly one stdout object with a trailing
newline:

```json
{"schema":"cairn.codex-advice-result/1","request_id":"431bda54-eb71-41ac-8412-2c47617cc851","ok":true,"model":"FULL_MODEL","provider":"openai","auth_method":"chatgpt_subscription","result":"model-produced text"}
```

The native command returns terminal text without interpreting it as a Cairn
proposal. Cairn independently validates the exact envelope and parses `result` as
one closed proposal object. There is no repair prompt or retry after refusal.

Failure exits nonzero and emits only a bounded error envelope. A request ID may be
null when input could not be admitted:

```json
{"schema":"cairn.codex-advice-result/1","request_id":null,"ok":false,"error":{"code":"INVALID_REQUEST","message":"The advice request is invalid."}}
```

Error codes are `INVALID_REQUEST`, `AUTH_UNAVAILABLE`, `AUTH_UNSUPPORTED`,
`POLICY_REFUSED`, `QUOTA_EXHAUSTED`, `PROVIDER_ERROR`, `TIMEOUT`, `OUTPUT_LIMIT`,
and `CANCELLED`. Messages are fixed strings. Provider diagnostics, configuration,
credential values and response headers are not copied into the envelope or logs.
Argument-parser failures occur before this request protocol; Cairn must reject
any unsuccessful exit or unexpected output.

| Limit | Contract |
| --- | --- |
| Total stdin bytes | 262,144 |
| Deadline | Default 90,000 ms; 10,000–180,000 ms |
| Total serialized stdout | Default 65,536 bytes; 1,024–262,144 bytes, including newline |
| Raw provider response | 2,097,152 bytes, including SSE framing and non-text content |
| Generation requests | Exactly one on the admitted path; zero on preflight refusal |

The deadline includes input, configuration, authentication, routing and response
consumption. SIGINT/SIGTERM cancel the request. Cairn still owns process-group
termination and verifies cleanup when its deadline, output cap or cancellation
fires. The runner must not infer success from partial output or restart an
uncertain operation.

## Authentication, policy and transport

Admission requires the exact native `CodexAuth::Chatgpt` variant and the built-in
OpenAI provider. API keys, external token modes, workload identity, personal access
tokens, provider overrides, custom ChatGPT destinations and alternative OAuth
issuers are rejected. Environment overrides for credentials, OAuth endpoints and
provider routing are rejected even when another stored login exists.

The command retains native managed configuration, credential-store selection,
workspace restrictions, residency, account binding and network policy. Native
OAuth refresh is constrained to `https://auth.openai.com/oauth/token`; this
restriction intersects administrator policy and remains effective if stored
provenance changes. A failed proactive refresh cannot silently reuse a stale token
for generation. The final request captures credentials and their account-bound
network factory together. Native workspace routing can select an authenticated
ChatGPT regional origin; the request endpoint must remain HTTPS on port 443 under
`chatgpt.com`, with the exact `/backend-api/codex` path and no URL credentials,
query or fragment. Redirects are refused.

The single Responses POST contains only the supplied user prompt, the registered
model, an empty tools array and `tool_choice: none`. Request and stream retries,
tools, hooks, MCP, session persistence, fallback models and schema repair are
unavailable. A transport guard rejects a second generation request even if a
lower-level retry path changes. Error bodies and streaming bodies are bounded
before parsing. A different reported server model, tool call, incomplete response
or oversized serialized result refuses without a second request.

The packaged patch, source receipt and build helper live under
`patches/codex-advice`. The helper operates on an explicit clean scratch checkout
and creates a new artifact directory; it never replaces the installed Codex CLI.

## Reproduce and check

Use a clean checkout at the pinned upstream commit, Rust `1.99.1`, Python 3.11 or
newer and GNU `strip`. Run from this Cairn checkout, selecting scratch paths:

```sh
python3 -B patches/codex-advice/build.py \
  --source /path/to/clean-codex-checkout \
  --output /path/to/new-advice-artifact
python3 -B patches/codex-advice/check.py \
  --manifest /path/to/new-advice-artifact/manifest.json
```

The builder applies the saved patch and verifies source fingerprints before and
after compilation and capability probes. It writes the executable, `manifest.json`
and `build.json` outside the source checkout. The command check uses a disposable
configuration home on Linux. It checks capability isolation, input refusal, route
refusal, cancellation, timeout and process cleanup without reading the existing
login or making a model request. Local Responses fixtures and the authentication
policy regression are carried in the native patch's Rust tests.
