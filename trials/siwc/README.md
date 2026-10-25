# Sign in with ChatGPT trial scaffold

This package is an offline-tested scaffold for a direct Sign in with ChatGPT
(SIWC) Responses route. It contains a loopback PKCE authorization flow,
dynamic-registration callback handling, token/ID-token validation, an
account-scoped model catalog check, and streamed Responses function calling
with only Cairn search and pull. Tests use local stub transports and generated
test keys; they do not contact OpenAI or register an OAuth client.

The scaffold does not perform real registration or owner consent, establish a
mapping between an SIWC `sub` and any Codex subscription profile/account, read
quota status, provide the full source-fit runner, provide a disposable Cairn
adapter, or grade a source-fit panel. An SIWC subject is an opaque OAuth
identity. Until an owner-authorized identity mapping and a trustworthy direct
route capacity source exist, capacity is unknown and the panel must not run.
Model catalog visibility alone does not establish Luna entitlement or quota.

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
