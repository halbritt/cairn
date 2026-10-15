# Original-weekly request drafts

`scripts/trial_weekly_draft.py` produces review inputs for the existing original
weekly request interface. It does not create reviews, preparation holds, accounts,
provider calls or a launch capability. Existing dispatch validators are unchanged.

```text
python3 scripts/trial_weekly_draft.py draft PROPOSAL.json --draft-dir /absolute/new/draft
```

The API is `draft(proposal, draft_dir, *, now)`. `now` is an aware UTC datetime.
The CLI uses current UTC and prints only the receipt or a fixed refusal code;
its exit code is 0 for a returned draft receipt and 2 for refusal.

The closed proposal has exactly these fields:

```text
schema request_id campaign_id occurrence_id scheduled_at expires_at
weekly_input capacity grading output reply supervisor_source permission_profile
```

`schema` is `cairn.weekly-eval.proposal/1`. All other fields retain their existing
[weekly request](weekly-request-v1.md) meanings and are explicitly supplied.
`output` is the future preparation destination; it is separate from `draft_dir`.
The proposal cannot supply a plan, schedule, limits, dispositions or companion.
No identity, source provenance, grading policy, model or timestamp is invented.

The producer uses `load_weekly_input()` to rebuild the original seven-field plan
and complete 126-row schedule. It copies `ORIGINAL_LIMITS` unchanged. It checks
identities, ordered/unexpired times, reply IDs, supervisor and grading pins,
permission profile and the selected capacity/workload declarations through existing
helpers. The capacity check supplies its existing account/route/reservation and
workload-basis validation and arithmetic; the draft adds no provider calculation.
Capacity must be currently valid and cover the complete proposed interval.
A future scheduled start is permitted for drafting; existing `check/prepare`
still require their own time-window and review/companion checks when called.
Reviewer-specific independence cannot be checked until reviews are supplied.

## Files and hashes

Exactly three files are written, with mode 0600 in a new mode-0700 directory:

| File | Contents |
| --- | --- |
| `schedule.json` | The exact rebuilt weekly schedule, serialized with existing `selected_bytes()`. |
| `request-core.json` | `cairn.weekly-eval.request/1` fields except `dispositions`; derived plan, schedule pin and limits replace the proposal schema. This incomplete core is not a valid request yet. |
| `review-subjects.json` | `cairn.weekly-review-subjects/1`, `request_core_sha256`, and `subjects` mapping the six existing review names to `review_subjects(core, bundle)` hashes. |

The core's `schedule.path` is the absolute `draft_dir/schedule.json` path.
Its `schedule.sha256` hashes the exact serialized file bytes, including spaces,
the internal schedule hash and trailing newline. It is distinct from the
schedule's internal `sha256`, which hashes canonical JSON with that field omitted.
The plan retains its existing internal canonical hash too.

`request_core_sha256` hashes canonical JSON for the complete core. The existing
six domain-separated review subjects remain authoritative. Changing the schedule
path changes the integration subject even when schedule bytes are identical.
Moving a draft therefore requires revising the core and obtaining applicable
reviews again; copying files does not preserve the original path binding.

The returned `cairn.weekly-eval.draft/1` receipt has `status=drafted_for_review`,
the canonical proposal/core hashes and exact byte sizes/file hashes for all three
files. It is not stored as a fourth file. The sum of all three serialized files
plus the serialized returned receipt must fit the existing 8 MiB retention ceiling
before any output creation. `runtime_admitted`, `witness_valid` and `admission`
remain false. A receipt/hash establishes correspondence, not authorship or custody.

## Protection and failure behavior

Both destinations must be absent, use safe absolute paths without symlinks, and
not overlap each other or selected source/control paths in either direction.
The producer reuses dispatch's path-protection helper without creating a registry.
It includes structured capacity, workload, account, route, reservation and permission
documents to protect transitive selected pins such as workload bases. The CLI also
protects its proposal file. The in-memory API has no proposal source-file identity.
Opaque supervisor, grading and basis artifacts are verified file pins, not
recursively interpreted manifests. No arbitrary artifact graph is discovered.

All applicable source/control checks, serialization and aggregate-size checks
finish before output mutation. Exclusive, fsynced file writes retain existing
partial-publication semantics: an I/O failure after directory creation can leave
a consumed partial draft. No receipt returns on that failure; the directory is
not removed, repaired or retried. An existing destination refuses. No registry
markers are written, and draft existence does not authorize execution.

Source/control files must remain trusted and quiescent throughout inspection and
publication. Repeated reads are not an atomic snapshot, and this code does not
authenticate source provenance, sessions, reservations or the selected policies.
Actual evaluator-side access remains root-owned under privacy/capture HOLD.
Case/corpus/oracle bodies and opaque artifact bytes are not copied into the draft.
Source metadata, case IDs and file hashes are retained: this is an evaluator-side
preparation artifact, not a blinded grader packet or a public release.

## Review sequence and remaining work

Independent reviewers supply the six disposition records for the emitted subjects.
Add those records as `dispositions` to the unchanged core to form the full request.
Then select a companion bound to that full request and an independent review of
the companion. Existing `check/prepare` validate that complete graph. The draft
does not select or manufacture any of those accepted records.

Synthetic tests cover API/CLI behavior, exact original-sized coverage, 1900
distractor identity, review/path mutations, separate review/companion round-trip,
private-content omission, stale/unsafe controls, retention and consumed failures.
They establish no actual campaign, fresh grading, account qualification or
operational candidate self-service. Trusted runtime integration, sufficient blind
packets, fresh independent grading and two consecutive actual weekly campaigns
remain full-goal work; original limits and engineer grading exclusions are unchanged.
