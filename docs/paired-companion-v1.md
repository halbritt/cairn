# Weekly request companion and independent review selection

`trial_paired_request.check` and `prepare` require two explicit file pins for
`weekly-frozen-cases`: a closed companion and a separately selected source-review
document. Both validate the complete request, review and provider mapping before
any registry marker or output write. Success remains `runtime_admitted: false`.
This is source declaration validation, not provider authentication or admission.

The closed request/2 shape is unchanged. `supplemental-fresh-tasks` follows its
existing request/2 and capacity/2 path without this weekly companion. Supplying
either companion argument for supplemental work refuses with
`supplemental_companion_unsupported`; it is not silently ignored, and the weekly
21-case requirement is not applied to supplemental tasks. `validate` remains the
base request validator, not a substitute for the public `check` boundary.

## API and CLI

```python
checked = check(request, now=utc_datetime,
                companion={"path": "/control/companion.json", "sha256": companion_file_sha},
                review={"path": "/control/selected-review.json", "sha256": review_file_sha})
prepared = prepare(request, now=utc_datetime, registry="/control/private-registry",
                   companion=companion_pin, review=independently_selected_review_pin)
```

The operator must select both pins explicitly. No review path, reviewer ID or
approval comes from the companion. An omitted weekly pin refuses; there is no
fallback, generated approval, latest-file lookup or automatic digest selection.

```sh
python3 scripts/trial_paired_request.py check /control/request.json \
  --companion /control/companion.json --companion-sha256 COMPANION_FILE_SHA256 \
  --review /control/selected-review.json --review-sha256 REVIEW_FILE_SHA256
python3 scripts/trial_paired_request.py prepare /control/request.json \
  --registry /control/private-registry \
  --companion /control/companion.json --companion-sha256 COMPANION_FILE_SHA256 \
  --review /control/selected-review.json --review-sha256 REVIEW_FILE_SHA256
```

Replace the uppercase placeholders with the exact lowercase 64-hex file digests
selected for this request. The Python API takes the same `{path, sha256}` pins.
Paths must satisfy the existing absolute, non-symlink, regular-file rules.
All JSON files use the existing bounded duplicate-key/nonfinite-rejecting parser.

## Closed documents and noncyclic subject

The companion has exactly these fields:

```json
{
  "schema": "cairn.paired-companion/1",
  "request_sha256": "FULL_CANONICAL_REQUEST_SHA256",
  "spend_request": {"path": "/control/spend.json", "sha256": "SPEND_FILE_SHA256"},
  "mapping": {"path": "/control/mapping.json", "sha256": "MAPPING_FILE_SHA256"},
  "observed_at": "2026-10-14T20:00:00Z",
  "valid_until": "2026-10-14T20:05:00Z"
}
```

`request_sha256` is `trial_paired_request.canonical_sha(request)` over the whole
closed request value, including grading identities/models, frozen input pin,
interfaces, capacity, output, reply, limits and dispositions. It is not just the
plan hash. `spend_request` and `mapping` are the existing source contracts in
[provider mapping v1](provider-mapping-v1.md); arithmetic is not reimplemented.

The independently selected review document has exactly:

```json
{
  "schema": "cairn.paired-companion-review/1",
  "subject_sha256": "CANONICAL_WHOLE_COMPANION_SHA256",
  "artifact": {"path": "/control/source-review.txt", "sha256": "ARTIFACT_FILE_SHA256"},
  "reviewer_id": "00000000-0000-4000-8000-000000000001",
  "state": "accepted",
  "observed_at": "2026-10-14T20:00:00Z",
  "valid_until": "2026-10-14T20:05:00Z"
}
```

The UUID is illustrative. `subject_sha256` is the canonical hash of the **entire
companion**, including schema and validity metadata. The companion contains no
review field, so this introduces no hash cycle. Review selection pins exact review
file bytes; the review pins an opaque artifact. Artifact bytes are checked and
retained, not interpreted as a signature or proof that a review occurred.

The selected reviewer must be a canonical UUID different from both primary and
secondary graders. Existing grade/disposition exclusions remain in force.
`grading.excluded_agents` excludes people from fresh grading, not source review;
the source reviewer may be on that list and may also be `dispute_owner` or an
existing source-disposition reviewer. No operational engineer IDs are hardcoded.
This module adds no source-author blacklist or claim of authenticated independent
authorship. Root must independently select an eligible real reviewer, establish
their authority and authorship independence for the subject, and supply the
complete source-exposure exclusions for fresh graders. Two different UUID strings
or an `accepted` declaration do not establish different independent people.

## One complete interval and exact model routes

The chosen start is `request.scheduled_at`; the chosen end is the pinned spend
request's `valid_until`. Request validation still requires
`scheduled_at <= now < expires_at`. The spend end must be **at least** request
expiry; a longer conservative projection is allowed.

Companion, selected review, capacity, account/quote mapping, spend binding and
every price quote must have `observed_at <= scheduled_at` and remain valid through
the complete chosen end. Observations first made after the start cannot
retroactively cover it. All existing request dispositions must also remain valid
through the chosen end; their existing schema has no observation-start field.
The existing capacity validator requires reservation validity through capacity
validity, which the mapper requires to cover spend validity. No extra fictitious
timestamp is added to the account/route declarations that lack one.

Current capacity freshness, declared reservation/resource checks, route coverage,
shared-pool accounting and all spend checks still run through the existing
validators. `now` is projected to UTC whole seconds for the existing spend API;
request/review comparisons use the supplied instant. This precision conversion
cannot extend the strict whole-second spend expiry.

Models are derived only from the already validated bundle's exact native model
for **both** CURRENT and CAND, and from request grading primary/secondary models.
The whole request pin also invalidates a companion when these inputs change.
Mapping then derives the full plan workload, checks exact job/account/quote/window
coverage and computes the existing conservative per-account/shared-pool results.
No alias resolution, one-request-per-turn assumption, zero cache demand or free
fee fallback is added.

## Retention, entry identity and failure behavior

`prepare` first owns a JSON snapshot of the request and completes the common
`checked_request` path. The companion boundary captures and hashes the same raw
bytes it parses for the companion, independently selected review, spend request,
mapping, capacity, workload, account/route/reservation declarations, spend binding
and prices. It also captures the opaque review artifact. Mapping validates their
existing pins using its unchanged validators. Callers must keep input objects and
operator-owned files quiescent; this is not an authenticated filesystem snapshot.

After all validation, preparation retains those raw bytes under fixed
`provider-*` filenames, along with `provider-projection.json`. The private
`preparation.json` includes `provider_evidence`:

- `inputs`: original verified `{path, sha256}` source pins and retained filenames.
  Each retained input is byte-identical to the checked source. Whitespace,
  non-ASCII encoding and trailing newlines are preserved.
- `subject_sha256`: canonical whole-companion hash; `reviewer_id`: selected UUID.
- `projection_sha256`: canonical projection **value** hash.
- `projection_file`: retained filename and SHA-256 of its actual serialized UTF-8
  bytes, including the final newline. This differs from the canonical value hash.
- `runtime_admission: false`.

`request.json` retains the request value using the existing selected JSON writer;
its existing `request_sha256` is canonical, not a claim about the original CLI
file's layout. Nested workload bases and quote-source/account-permission artifacts
are checked by the existing validators and their pins remain in the selected
documents; their opaque bytes are not copied. Their meaning remains unproved.
No additional frozen input tree or case/oracle/corpus copy is made by this helper;
the existing base request validator still reads its supplied frozen input bundle.

The entire provider retention set, **including the serialized projection**, must
fit the existing 8 MiB JSON-file ceiling before any writes. Each input also obeys
that ceiling, and the projection is therefore readable with the existing bounded
reader. This is a provider-artifact retention limit, not an expansion of native
request/capture budgets or a hard process-memory bound. Files use mode 0600 and
the output directory uses 0700. The two registry markers and the existing partial
preparation holds are preserved after genuine write/fsync failures. No cleanup
removes a hold to manufacture a retry.

The mapping module imports paired-request validators. The integration is lazy,
and the executable script delegates to the canonical imported module's `main`,
so `Refusal` has the same identity for direct API, imported CLI and `__main__`
execution. Spend refusals are converted to the fixed `provider_spend_refused`
code. Public CLI refusals, including argument errors, contain only selected
codes; paths, payloads and original exception text are not printed. `launch`
still returns `provider_execution_unavailable` before opening even an unreadable
request. Successful check metadata is a private operator work product containing
source locators; callers must not publish it as a sanitized refusal.

## Verification and limits

The tests use real tempfile pins and complete **synthetic** 21-case/3-seed/2-arm
126-attempt requests through real base validation, mapping and public check/prepare.
One declaration per core role has count63 job instances/attempts; all nine roles
are explicit. They cover source pins, whole subjects, role/model changes, every
interval, account/spend refusals before mutation, byte retention, the real 8 MiB
boundary including projection, CLI/import identity, supplemental compatibility,
launch refusal and actual writes with injected OS fsync failure. The subprocess
clock is the only time test double; no internal validator or arithmetic is mocked.

```sh
PYTHONPATH=scripts python3 -B -m unittest test_trial_paired_companion \
  test_trial_paired_request test_trial_paired_capacity test_trial_provider_mapping \
  test_trial_provider_spend
make check
```

Original CAIRN-107 remains unchanged: original1900 distractors, 21 frozen cases,
3 seeds, 2 arms, v12/current versus candidate on the same worker/disposable PG,
complete calls/tokens precheck and provider-limit backstop, second blind independent
grading for every potentially changed pair, delegated disputes, dated reports and
Cairn notes, two consecutive complete weekly campaigns without losses, candidate
self-service and a separate dedicated account. Supplemental fresh tasks do not
replace these requirements. Existing 8500/9500/8192/32000 and all other limits stay
unchanged.

This request/2 boundary continues to use prospective input. The separately
implemented [weekly request](weekly-request-v1.md) and
[weekly input](weekly-input-bundle-v1.md) boundaries handle the original weekly
shape without redirecting request/2. Their source checks do not establish actual
weekly freeze custody, corpus acceptance or campaign results. Actual provider
request/cache calibration, authenticated account routing/dedication,
qualified prices and fees, funded balances, enforceable reservation and cumulative
debit before forwarding remain root-owned obligations. Pinned source declarations
and a passing check do not prove them. This implementation has no runtime launch,
provider traffic, account operation or admission path.
