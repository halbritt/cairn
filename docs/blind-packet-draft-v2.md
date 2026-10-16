# Offline blind sample and comparison drafts

`scripts/trial_blind_packet_draft.py` implements the selected source contract /2
(7390 bytes, SHA256
`6233080c5c445c490a1165fdc75f6776ecac71eb2108fdf8e1018654dc552de3`).
This is a synthetic-tested source producer, not an evidence collector, redactor,
grader, authentication check or release adapter. Actual policy pins, evaluator
selection, semantic sufficiency, fresh sessions and custody remain unqualified.
Capture HOLD remains. Engineers and reviewers remain excluded from fresh grading.
Original 126 attempts/63 pairs, 1900 distractors and two complete consecutive
weekly campaigns remain full-goal work; this module neither selects changed
pairs nor verifies original schedule coverage.

## Source API and output

The input directory must be private and quiescent throughout each invocation.
Call `approval_subject(selection_pin)` to compute a **private** acyclic subject.
It writes nothing and creates no approval. A separately supplied declaration
must bind SHA256 of `canonical_bytes(subject)` and an opaque issuer-role pin.
Then call `draft(selection_pin, approval_pin, absent_output_path, now=utc_datetime)`.
All pins and computed bytes are checked again. An explicit time is source input,
not a trusted clock. Neither successful API call permits actual release.

The exclusive output contains:

* `public/samples/<sample_id>.json`: C of the complete public sample.
* `public/comparisons/<comparison_id>.json`: C of the approved-order comparison,
  with two complete embedded samples and their separate sample hashes.
* `private/crosswalk.json`: selected source/policy/approval pins, the subject and
  its digest. The subject retains private attempt/pair/order mappings and output
  byte pins; these are not public packet fields.
* `private/receipt.json`: the returned private diagnostic receipt, in C format.

Directories are new 0700; files are exclusive 0600. The receipt and subject are
private API results: do not print them to graders or operational CLI logs.
`deliver(...)` refuses without inspecting arguments. Running the module as a
command prints only the fixed `blind_delivery_unavailable` refusal and exits 2,
before opening selected inputs. There is no delivery or draft CLI bypass.

## Exact schemas

All structured documents are closed. Objects below admit only the listed fields;
all version strings are exact. A pin is `{path,bytes,sha256}` with a canonical
absolute, nonsymlink regular-file path, exact nonnegative integer length (not
boolean) and lowercase SHA256 of the file bytes. Explicit pins alone are read.
Opaque bodies are never parsed or followed as graphs.

| Document | Fields and values |
| --- | --- |
| Selection | `schema=cairn.blind-selection/2`, `namespace`, `policy`, `policy_ref`, `case_contracts` (pins), `samples`, `comparisons`, `review_measurement` |
| Private policy | `schema=cairn.blind-policy/2`; each of `rubric`, `changed_pair_definition`, `blinding_policy`, `release_specification`, `review_budget` is `{version,pin}` |
| Public policy reference | `schema=cairn.blind-policy-ref/1`, `id`; an explicit public token, never a path or private policy hash |
| Case contract | `schema=cairn.blind-case-contract/2`, `case_key`, `case_class`, ordered `required_channels`, ordered `artifact_inventory` |
| Sample selection | `source`, `sample_id`, `case_contract_sha256`, ordered `channels` |
| Source | `schema=cairn.blind-source/2`, `request_id`, `request_sha256`, `campaign_id`, `occurrence_id`, `schedule_sha256`, `case`, `seed`, `wording=task`, `arm=CURRENT\|CAND`, `run_id` |
| Comparison selection | `comparison_id`, `pair_key`, ordered `samples` (two sample IDs) |
| Pair key | Source fields other than `schema`, `arm`, `run_id` |
| Selected channel | `name` (registry channel), ordered `entries`, `observation` (pin or null) |
| Entry | `name` (public logical name), `kind`, `representation`, `media_type`, `input` (pin), `transform`, `output={bytes,sha256}` |
| Observation | `schema=cairn.blind-evaluator-observation/2`, exact `source`, `channel`, ordered `evidence` input pins, `role=evaluator_observation`, `subject` (opaque pin) |
| Approval declaration | `schema=cairn.blind-declared-approval/2`, `state=approved`, `subject_sha256`, `observed_at`, `valid_until`, `issuer_role` (opaque pin) |

IDs, namespace, class and policy versions use `[A-Za-z0-9][A-Za-z0-9_.-]{0,95}`.
Source request/schedule hashes are declared join identities, not verification of
request or schedule bodies. Each source cell and run ID maps once to one sample
in the supplied namespace. Every selected sample occurs in exactly one explicit
comparison. Pair source keys must be identical, arms opposite, samples distinct;
duplicate comparison keys/IDs and dangling or unused contracts refuse. The
caller supplies order; no arm/path/case sort or randomizer exists here.
Identical evidence pins/content may serve different valid attempts/channels.

Channels are exactly the contract's ordered `task_statement`,
`grading_instructions` and nonempty selected combination of `final_answer`,
`artifact_bundle`, `action_trace`, `evaluator_observation`. Nonartifact channels
have one `content/bytes` entry. Action/observation channels require the separately
pinned observation document and subject; other channels require null observation.
These are declared role/subject correspondences, never proof that an action ran.
There are no raw stdin/stdout/stderr channels or implicit log discovery.

Artifact inventory entries have exactly `name,kind,representation,media_type`,
in selected entry order. Kinds `file`, `patch`, `index`, `binary` use `bytes`;
`deleted` uses `deletion_record`; `rename` uses `rename_record`. Empty explicit
inventory is allowed, without asserting semantic sufficiency. Deletion bytes
must decode as `{schema:"cairn.blind-deletion/1",name:<entry-name>}`. Rename bytes
must decode as `{schema:"cairn.blind-rename/1",from:<name>,to:<different-name>}`.
These records use `application/json`. Names are explicitly supplied relative
POSIX logical names: no absolute, empty, dot, parent, backslash or control
components. Spaces and Unicode are preserved. Other allowed media types are
`text/plain`, `application/octet-stream`, `text/x-diff`. Names are unique within
a sample; the producer never invents or rewrites them.

## Bytes, transforms and approval

C is UTF8 JSON with sorted object keys, `ensure_ascii=false`, compact separators
and exactly one final LF. No floats, nonfinite values, duplicate keys, unpaired
surrogates or integers outside ±(2^53−1). This differs from both existing request
`canonical_sha` (no LF) and `selected_bytes` (spaces): do not interchange hashes.
Input pins always hash exact supplied file bytes; input JSON need not already be C.

`{kind:"exact_copy"}` preserves all bytes, including binary and line endings.
`{kind:"closed_json_projection",paths:[["answer","text"],["ok"]]}` selects
literal object-key paths in supplied order. Every terminal is null, boolean,
string or safe integer. Arrays/objects as terminals, array traversal, missing
keys, wildcard components `*`/`**`, duplicates and prefix overlaps refuse.
For source `{"answer":{"text":"yes","private":"secret"},"ok":true}`, output
is exactly these bytes plus LF:

```json
[{"path":["answer","text"],"value":"yes"},{"path":["ok"],"value":true}]
```

The producer does not scan exact-copy content for secrets or infer semantic
blindness. Explicit approval of complete resulting bytes remains necessary.
Public channels flatten the selected entries in contract order into exactly
`{kind,name,media_type,encoding:"base64",bytes,sha256,data}`. `kind` is the channel
registry name. `data` is canonical padded RFC4648 base64; its decoded bytes define
the channel length/hash. Whole packet length/hash separately includes encoding.

Public sample: `{schema:"cairn.blind-sample-draft/1",sample_id,policy_ref,channels}`.
Public comparison: `{schema:"cairn.blind-comparison-draft/1",comparison_id,policy_ref,
samples:[{sample_id,sample_packet_sha256,packet},...]}`. Sample hashes always use
C of the full embedded sample, including LF, not its comparison substring. Both
graders must later receive the identical C(comparison). Only its **pair hash**
may be bound by a future trusted adapter to the existing grading join's
`packet_sha256`. No existing join code is changed here.

The subject is `{schema:"cairn.blind-approval-subject/2",selection,sample_packets,
comparison_packets}`; each packet map keys supplied IDs to `{bytes,sha256}`.
The selection includes the complete policy tuple, public policy token, selected
instructions, contracts, sources, transformations, names, order and review
measurement. Approval is outside selection/subject/packets: there is no hash
cycle. Its state, issuer role and time interval are only checked declarations.
Success is `declared_correspondence_valid`; release authority, admission and
delivery remain false. Missing or inconsistent declarations refuse.

## Independent bounds and failure semantics

Existing actor evidence caps remain: task statement 9500 bytes, answer 8192,
action trace 32000, for both selected source and transformed content. They do not
cap grading instructions, comparison envelopes or grader capacity. Selected
instructions/observations/artifacts share the existing 64MiB work-product bound;
artifact inventory/entry count is 4096 and patch/index bytes at most 16MiB.
Each selected input file remains subject to the existing 8MiB file-read bound;
raw per-attempt channel totals are checked independently of transformed bytes.
The separate aggregate 8MiB emitted-retention bound can dominate output size. No actor
call, token, memory, search, time or provider allowance is added or verified.

Review measurement is closed `{schema:"cairn.blind-review-measurement/1",unit,
semantics,graders:[{slot:1,limit,control:<pin>},{slot:2,limit,control:<pin>}]}`.
Units are `bytes`, `tokens`, `unknown`; semantics `comparison_plus_control` or
`unknown`; limit is a nonnegative safe integer or null. Only bytes with the
supported semantics and a present limit are measured: **entire C(comparison)
plus that grader's selected control bytes**, separately for both graders.
Other combinations report `unknown`, never a token estimate. The subject binds
this declaration and controls to the opaque review-budget policy pin; it cannot
verify the opaque body's meaning. An over-budget draft may be retained with
`over_budget`; it is not a ready packet or a new allowance. Overall fit preserves
any over-budget result, otherwise unknown if either grader is unknown.

Retention counts all emitted sample files, all comparisons (including embedded
duplicate bytes), the private crosswalk, stored receipt and returned receipt
again. Existing selected source files are not emitted again and do not consume
that retention allowance. Selection metadata copied into the crosswalk does count.
Shared input pins are read once; `input_bytes` remains a diagnostic count of unique
selected source bytes, not a retention charge. There is no content dedup credit
for output copies. The subject-only API preflights prospective public files plus
its returned subject; success cannot reserve room for later approval/output.

For example, three selected artifact JSON files of 3MiB each can project to 102
bytes total under the unchanged source/work-product caps. Their raw bytes exceed
8MiB without making the emitted draft exceed its 8MiB retention cap. This corrects
the source/retention conflation in frozen candidate `37faea1`. Input caching and
JSON/serialization allocations are working memory, separate from emitted retention;
no aggregate heap bound is implemented or claimed. Per-file and per-attempt source
bounds do not establish one. Quiescent filesystem requirements are unchanged.

All pin, join, overlap, transform and full serialization checks precede mkdir.
Policy, approval, role, observation, control and evidence pins all participate in
path checks. Existing/symlink outputs refuse. Inputs and output parent must remain
private and quiescent; source checks are not runtime custody or race isolation.
After output creation, an I/O failure returns `blind_draft_io_consumed` and leaves
partial files untouched. A retry to that path refuses `target_exists`. No cleanup,
repair, registry marker, launch, database or provider operation occurs.

Focused synthetic verification: `python3 -m unittest discover -s scripts -p
'test_trial_blind_packet_draft.py'` (the retained review report also pins the
effect guard used during development). Actual policy selection, semantic release
approval, verified custody, runtime delivery and campaign success remain separate
work owned by root and the subsequently qualified private evaluator.
