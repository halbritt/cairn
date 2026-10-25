# CAIRN-223 source-fit panel

`FROZEN.json` fixes six synthetic source tasks and 15 one-replicate arms. It is
separate from the paused 72-slot memory-utility campaign. The frozen file's
SHA-256 is `d1acec4b07892215831a7e46330afa04c5e70c9132d15ca4060b41a6a399f909`.
All source bodies are generated exactly from `body_parts` (UTF-8 text repeated
the specified count); `check_source_fit_panel.py` reports their sizes and hashes.
The two conflicting records are issued as mandatory class C positions in their
own disposable database and disputed there. The local-only record is never
provided to the hosted profile. The author-maintained extraction is a separate
record in a separate database; the original mixed body remains intact in
history. The direct arm supplies full eligible bodies as a positive control.

The predeclared task checks are `required_answer`, `forbidden_conclusion`, and
`source_labels` for each case. Literal presence is only a screen; a reviewer
must judge the answer against complete source, qualifications, source extent,
corrections and conflict. Failed, unscored and unrun arms remain in the
denominator. One synthetic replicate cannot establish causal savings or
general usefulness.

Local validation and no-provider preflight:

```sh
python3 scripts/check_source_fit_panel.py
python3 -m unittest discover -s scripts -p test_source_fit_trial_guard.py
mkdir -p "$HOME/.cache/cairn-source-fit"
go build -o "$HOME/.cache/cairn-source-fit/cairn" ./cmd/cairn
bash scripts/trial-task-eval.sh -- python3 -B scripts/preflight_source_fit.py \
  --binary "$HOME/.cache/cairn-source-fit/cairn"
```

`trial-task-eval.sh` creates and destroys a disposable PostgreSQL cluster. The
preflight runs the actual local lifecycle hook and reports source extents,
without a provider call. `run_source_fit.py` is the separate native Claude Code
subscription driver. It requires the exact frozen SHA and writes selected
answers, usage, and sanitized tool-result status/size/digests outside the
repository; it does not retain native streams or tool-result content.
Supported Cairn search/pull results also retain validated record IDs/versions,
whole versus partial extent, source/delivered digests, and byte counts.
Unsupported shapes are marked unknown; failed calls carry no source items.
Its sanitized child environment removes API, OpenRouter and other provider
route variables. The driver verifies `claude.ai` subscription authentication,
records the resolved model ID and usage on every run, and stops on a route,
model, usage, runtime or admission problem. The provider did not offer a hard
per-run total-token cap. The corrected driver now reserves at least 32,000
reported tokens before each admission, raising that reserve above any larger
observed run. This prospective reserve guards the next admission; it cannot
guarantee a single run will stay below the cumulative ceiling.

The 2026-10-24 bounded trial has stopped. See
`docs/verification/source-fit-2026-10-24.md` for the observed outcomes and
remaining acceptance work. Do not replay it as a continuation or treat its
unrun arms as passed.

The proposed native Codex/Luna route is separately assessed in
`docs/verification/source-fit-codex-route-2026-10-24.md`. Existing Codex usage
and source-delivery readers are reusable, but no Codex source-fit runner or
model-verified native tool stream was established, so no Codex panel copy was
frozen and no provider was contacted.

The landed Codex/Luna panel v1 is documented in
`docs/verification/source-fit-codex-luna-plan-2026-10-25.md`. Its tool
preflight claims were corrected in the distinct v2 manifest at
`trials/source-fit/codex-luna-2026-10-25-v2/FROZEN.json`; v1 remains unchanged.
V2 preserves the same 15 arms and remains
`admission_pending_runner_preflight`. A provider-free fake-auth loopback
request declares generic exec, while its attempted fake call fails closed with
`code_mode_host=false`; that result does not prove subscribed-provider tool
offers. See `docs/verification/source-fit-codex-luna-v2-2026-10-25.md` for the
revised effective-feature, bwrap, and per-attempt tool gates.
