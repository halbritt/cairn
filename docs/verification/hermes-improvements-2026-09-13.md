# Hermes memory improvements, 2026-09-13

The five improvements in the [owner-authorized plan](../plans/hermes-improvements.md)
are implemented, pushed and deployed at clean code revision
`a6ff10be36aa389460fb41d46c543844bac5c007`. The ordered commits are
`ab5b976`, `c7967df`, `15651cf`, `e83a159` and `a6ff10b`.
The [decision receipt](hermes-improvements-decision.json) records evidence and limits.

## Changes and observations

Confirmed courtesy exchanges skip selection. The synthetic baseline took
3.481–3.530 seconds; the final shortcut took 0.674 milliseconds. This is a
whole-`capture()` measurement with memory reads/writes replaced by fixture
boundaries. Substantive captures still use the selector. Haiku was slower in the
comparison; low effort gave no consistent advantage. The installed model remains
`claude-fable-5-1[1m]`.

CLI `/cairn` and Slack `!cairn` now provide context, status and retry commands.
Explicit project/workstream choices survive gateway restart and do not change
the terminal cwd or shared collection identity. Status reports actual engine
outcomes without a model call. A cold retry reads at most 64 active native
history rows and requires an exact normalized digest; changed history is refused.
`discard` explicitly abandons an unavailable pending retry.

Hermes enables one semantic search after an optional lexical miss, then pulls
and checks one complete candidate. Required context from both searches and the
body share the 12000-byte ceiling. The relevance model has an eight-second
deadline. Real selector checks passed paraphrase, unrelated and embedded
instruction cases twice, taking 3.15–7.17 seconds. These inputs do not establish
general relevance accuracy.

Checkpoint replacements stay under 3000 UTF-8 bytes. Real selection retained an
open security scan while removing completed next steps, recorded explicit full
completion and did not reopen completed work. Native API verification confirmed
the same record ID, a new version and exact prior wording through history.
No existing collection was bulk rewritten.

## Verification

All 101 Python tests, `make check` and `make test-integration` passed. The
integration run used disposable PostgreSQL and exercised native Claude/OpenCode
against the changed shared engine. The final focused lifecycle run passed 53
tests, including the correction that expired error hints must not permanently
disable semantic fallback.

The native Hermes fixture used installed Hermes `13f4cfebfafbce8ac9d1bf29f66731858ed638b5`,
real Cairn/PG and the installed embedding worker. It exercised CLI and the real
Slack parser/GatewayRunner with captured outbound transport, context isolation,
cold status, restart retry, mismatch refusal, semantic paraphrase/rejection,
checkpoint history and existing compaction/interruption/timeout checks.
Its model responses are synthetic; the real selector checks above are separate.
The final six-case capture regression also passed.

Optional local artifacts are `/tmp/cairn-hermes-improvements-final`,
`/tmp/cairn-hermes-improvements-integration.log`,
`/tmp/cairn-hermes-relevance-real.log`,
`/tmp/cairn-hermes-checkpoint-final-real.log` and
`/tmp/cairn-hermes-final-capture.json`. Checked-in scripts and this report retain
the tested claims without raw dialogue or operational memory.

## Deployment

The default Hermes profile has the new provider and `cairn-controls` plugin.
Installed manifest hashes match source; comparing configuration values found no
unrelated changes. Both installed Claude engines and the OpenCode engine have
SHA-256 `8952ce0676c8f081dcd893df32cf1b06372201447536e644c7db7e78f8774f70`,
matching Hermes. Existing Cairn CLI/API remain `d9c0884`; no store migration or
API restart was needed.

The gateway restarted and connected Slack Socket Mode at 16:37:36 PDT, with zero
restart retries. Native command discovery loaded `/cairn status`; the installed
provider performed ordinary recall and reported the retrieved record/version.
No external Slack test messages were sent. Rollback copies of changed installed
files and configuration are in `/tmp/cairn-hermes-improvements-rollback`.
Fresh CLI processes load the new adapter.

These checks establish the implemented and deployed CLI/Slack slice. They do not
establish full Cairn design acceptance, actual external message delivery, general
selector accuracy or sustained task benefit. Selection remains fallible; abrupt
death can lose pending text, cold retry can refuse compacted history, and
multi-note writes are not atomic.

CI passed on the deployed code revision:
[PostgreSQL and OpenCode checks](https://github.com/halbritt/cairn/actions/runs/34790213078).

The decision receipt uses validated Pincite release `d3e0c0d`, corpus
`corpus-2026-07-12-a11702cc9217`, doctrine `doctrine-f6bbb5196a3f8bf9`
and retriever `retriever-ec995ecdd083b2c8`. Final packet
`pkt-04746f2ffaeac3c6` has content SHA-256
`04746f2ffaeac3c6a647a8935d75b3dd111440fd8972e6df59e7f392e740f0e0`.
One evidence reassembly left twelve nonmaterial obligations, each retained with
its reason in the receipt. The claims exclude a new dependency-inversion design,
internal bottleneck attribution, exact instrumentation overhead and long-term
resource-retention improvement. Receipt schema validation and citation trace
closure passed.
