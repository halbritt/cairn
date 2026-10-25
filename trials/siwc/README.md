# Sign in with ChatGPT trial scaffold

This package is an offline-tested scaffold for a direct Sign in with ChatGPT
(SIWC) Responses route. It contains a loopback PKCE authorization flow,
dynamic-registration callback handling, token/ID-token validation, an
account-scoped model catalog check, and streamed Responses function calling
with only Cairn search and pull. Tests use local stub transports and generated
test keys; they do not contact OpenAI or register an OAuth client.

`TrialStoreTools` adapts one explicitly identified disposable task-eval store to
the package's search/pull interface. It checks the hosted trial identity and
destination, binds pulls to exact arguments and record/version/source digests
returned by that store's search, refuses ineligible pulled or mandatory-selected
sources, strips executable pull commands, and retains body-free extent/digest
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

This package still does not perform real registration or owner consent,
establish a mapping between an SIWC `sub` and any Codex subscription
profile/account, read quota status, provide a complete or admitted source-fit
campaign, or grade the panel. An SIWC subject is an opaque OAuth identity.
Until an owner-authorized identity mapping and a trustworthy direct-route
capacity source exist, capacity is unknown and the panel must not run. Model
catalog visibility alone does not establish Luna entitlement or quota.

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
