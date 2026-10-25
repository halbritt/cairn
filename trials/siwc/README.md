# Sign in with ChatGPT trial scaffold

This package is an offline-tested scaffold for a direct Sign in with ChatGPT
(SIWC) Responses route. It contains a loopback PKCE authorization flow,
dynamic-registration callback handling, token/ID-token validation, an
account-scoped model catalog check, and streamed Responses function calling
with only Cairn search and pull. Tests use local stub transports and generated
test keys; they do not contact OpenAI or register an OAuth client. The
`cmd/auth_probe` command is offline by default. This task did not run its
authorization or inference options.

`TrialStoreTools` adapts one explicitly identified disposable task-eval store to
the package's search/pull interface. It checks the hosted trial identity and
destination, binds pulls to exact arguments and record/version/source digests
returned by that store's search, refuses ineligible or local-only sources,
strips executable pull commands, and retains body-free extent/digest
receipts. `scripts/test_siwc_trial_tools.py` runs that adapter against a
synthetic store through `scripts/trial-task-eval.sh`; Go unit tests also use a
fake CLI.

To exercise the adapter with the real local CLI and a disposable PostgreSQL
cluster, build the CLI outside the managed store and run:

```sh
mkdir -p "$HOME/.cache/cairn-source-fit"
go build -o "$HOME/.cache/cairn-source-fit/cairn" ./cmd/cairn
scripts/trial-task-eval.sh -- python3 -B scripts/test_siwc_trial_tools.py \
  "$HOME/.cache/cairn-source-fit/cairn"
```

`DirectAttemptRunner` is a separate direct-arm-only orchestration slice. It
accepts only the pinned source-fit v1 or v2 manifest hashes, an explicit case,
`arm=direct`, model `gpt-6-luna`, and medium reasoning. It builds the prompt
from that case's eligible `source_labels` in manifest order; the inaccessible
case has no source labels, so its context is task-only. Direct arms expose no
Cairn tools. The runner applies a 180-second context deadline and checks
completed answer/model/response count and reported usage afterward; SIWC preview
does not accept `max_output_tokens`, so these are local post-run checks, not a
provider-side token cap.

The public constructor still uses a permanently unavailable admission gate.
Without independently verified mapping from the SIWC subject to the selected
subscription account, quota/capacity evidence, and direct-route proof, `Run`
records `not_attempted_unscored` and does not invoke Responses. Package-local
offline tests substitute a private fake gate and loopback transport; they do
not establish live route readiness. Before an admitted attempt, the runner
creates an owner-only exclusive claim under an output directory outside any
Git checkout, rejecting symlink ancestors. The claim blocks a second run even
after a restart. Its receipt retains only the selected answer and body-free
source/usage metadata at owner-only permissions; it never records source text,
raw provider errors, or credentials. Successful synthetic responses remain
unscored pending independent task review.

`RetrievalAttemptRunner` covers the `current`, `hints`, and `author_split`
arms. It accepts the same frozen v1/v2 manifests, enables only Cairn search and
pull through the concrete `TrialStoreTools` adapter, and checks every delivered
source against the selected fixture's digest and whole-source byte extent.
Partial, unknown, or local-only delivery, missing selected sources, provider or
tool errors, invalid usage, and an answer containing the local-only sentinel
fail the one attempt closed. Receipts retain source record IDs, versions,
extents, digests and byte counts, plus the selected answer; source bodies and
raw provider errors are not retained. Public construction keeps admission
unavailable, so even a valid disposable-store setup cannot invoke Responses.
The offline tests inject fake tools, transport, and admission only inside the
package test suite.

`cmd/auth_probe` performs no OAuth, browser, or provider request unless invoked
with `--authorize --alias NAME`. That explicit option runs the loopback consent
flow and account model-catalog check. Adding `--probe-entitlement` makes one
bounded no-tool Luna Responses request after authorization. The command stores
only an owner-only alias-to-SIWC-subject/client metadata binding; credentials
remain in memory. These capabilities were not run here, and no live consent or
entitlement proof was completed. Even a successful probe does not map an SIWC
subject to a selected Codex subscription account or establish route quota or
campaign capacity. The package still has no admitted source-fit run or task
grading. Model-catalog visibility alone does not establish Luna entitlement
or quota.

Credentials are held in memory only. The access token is rejected after its
expiry; this scaffold has no refresh flow or credential persistence. Responses
redirects are rejected to prevent resending authorization codes, prompts, or
source history. No provider or model fallback is implemented.

Official references:

- [Sign in with ChatGPT](https://developers.openai.com/siwc/token-sharing-open-source/sign-in)
- [SIWC models and inference](https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference)
- [SIWC preview limitations](https://developers.openai.com/siwc/token-sharing-open-source/preview-limitations)
- [Responses function calling](https://developers.openai.com/api/docs/guides/function-calling)
- [GPT-6 Luna model](https://developers.openai.com/api/docs/models/gpt-6-luna)
