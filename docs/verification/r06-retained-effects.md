# Retained issued effects fixture

The fixture closes the effects join between installed retained Python /2 and
Go /3 on one disposable API/PostgreSQL installation. It starts actual emitted
/2 callbacks beneath the existing observed helper, obtains store-issued session
and attempt identities, and keeps the resulting contexts, request UUIDs, profile,
credential selector and state directory unchanged. It writes no session state,
journal, API response or authentication substitute.

The focused gate runs with four independently pinned images built from the
candidate: `api` (`cmd/cairn`), `control` (`cmd/cairn-lifecycle`), `helper`
(`go test -c ./cmd/cairn-lifecycle`, named `helper`) and `stager`
(`cmd/cairn-stage-lifecycle`). The JSON image input uses the existing
`cairn.r06-unit-images/1` schema with `images.ROLE.path` and `.sha256`.

```sh
mise run host:exec -- --timeout 10m --weight 2 -- \
  python3 -B scripts/check_r06_retained_effects.py \
  --images /absolute/private/images.json --evidence /absolute/private/new-evidence
mise run test:unit -- scripts/test_r06_retained_effects.py
mise run check
```

The driver stops and joins only its disposable API to produce a real pending
completion through the retained issued command. After restarting that same API,
Go's existing private `coordination inbox` dispatcher retries the exact issued
tail, commits its result, retries it, and publishes the exact causal reply.
Pending, completed, settled and `reply_possible` are recorded separately:
a committed completion can be settled while its reply remains possible.
An explicit inspection of the original /2 installation selects the retained
executor again. The original issued completion and reply then execute unchanged.
Store delivery/result/reply rows must prove one result version and one reply.

A staged /3 resume/Stop callback pair consumes the retained session state;
the agent and execution must remain identical. After explicit /2 selection and
resume, a subsequent actual /2 notice supplies a compact handle. Go reads and inspects
it, retained Python reads and inspects it after reverse selection, a foreign
native-session selector receives `NOT_THIS_SESSION`, Go settles it, and retained
Python retries the same settlement. The original bare `cairn-inbox` cue and its
supported private CLI expansion are retained separately; this fixture does not
install a global launcher or replace `HOME`. The application-specific
`CAIRN_INBOX_ROOT` selects the fixture's normal compact lookup layout. Its
binding configuration is copied from the installed /2 profile before callbacks.

Before/after hashes and metadata protect the installed generations, original
contexts, profiles, settings (including opaque large integer and Unicode data),
credential file, transport selector and native state. A second real /2 session
holds its own notice; its delivery and state must remain unchanged. Compact
readers may renew the selected delivery lease but may not change other effects.
Every command's vector, stdin contract, output, exit and joined PID are retained.
The fixture retains its own product-written journals and API-call receipts.
It stops its owned API and PostgreSQL on success or failure.

Existing R02 installation, immutable reverse, issued-shell and shared-journal
qualification receipts remain prerequisites. This gate adds the real retained
effects join and one cross-reader refusal, rather than repeating those matrices.
It launches no model or native client. It proves neither native refresh nor
whole-unit activation acceptance, and spends no global native attempt.
Production hold `98d46758/v1` remains in force.
