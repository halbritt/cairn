# Instruction category limits

A governed repository policy can bound C instruction load by category. Issue an
instruction with `category` set to `security`, `workflow` or `preference`.
Omitting it assigns `workflow`, including for records created before migration
024. The assignment alone does not activate a limit. A repository must explicitly
adopt a policy containing `instruction_limits`.

The category is authority metadata for the issued version. Scope authorization
and retraction preserve it. Ordinary edits cannot recategorize a C instruction;
retract and issue a replacement under live authority. Inspect the current
assignment without retrieving its body:

```sh
cairn instruction-policy RECORD_UUID
```

Adopt or revise limits through the existing `cairn policy-revise` JSON command:

```json
{
  "request_id": "REQUEST_UUID",
  "repo": "/path/to/repository",
  "expected_revision_id": "CURRENT_REVISION_UUID",
  "grant_id": "ISSUE_GRANT_UUID",
  "rules": {
    "optional_percent": 10,
    "optional_max_tokens": 6000,
    "instruction_limits": {
      "security": {"max_count": 20, "max_tokens": 8000},
      "workflow": {"max_count": 20, "max_tokens": 8000},
      "preference": {"max_count": 10, "max_tokens": 2000}
    }
  },
  "reason": "Adopt reviewed instruction limits for this repository"
}
```

Replace the identifiers and limits for the repository. Use an empty expected
revision for first adoption. These numbers illustrate the request; they are not
installed defaults or measured recommendations. Each of the three category
objects is required. Counts range from 0 to 10,000; token bounds from 0 to
1,000,000. Missing numeric fields default to zero. Zero admits no instructions
for that dimension. Negative values, missing category objects and unknown
categories are refused.

Policy rules with category limits select `local-loop/3`. Rules without limits
retain `local-loop/2`; repositories without an explicit revision retain the
original `local-loop/1`. Rollback uses the same governed revision mechanism and
can restore either rule format. Migration does not adopt a policy or create a
grant. The fixed categories and numeric validation bounds are implementation
decisions under the operator's delegated O3 authority; per-repository limits
remain an explicit policy choice.

## Packing and delivery

Within each package, required C instructions consume their category quota first.
If their count or body cost exceeds it, compilation refuses with `BUDGET_REFUSED`.
Optional C instructions share the remaining quota; those that do not fit are
omitted with `CATEGORY_BUDGET`. A and B records do not consume instruction quotas.
No category changes a record's authority or permits an enforcement downgrade.

The category token measure is the UTF-8 byte length of the instruction body,
using Cairn's conservative token bound. For example, `界界` costs six bytes, not
two. Category accounting excludes authority and wrapper metadata; the overall
rendered context limit still includes that overhead. The optional-memory ceiling
also still applies. Existing duplicate and optional-budget exclusions run before
optional category admission, so an entry excluded by both may report
`OPTIONAL_BUDGET`. Final context fitting can remove optional entries afterward.

Index delivery reserves the full body cost for each admitted optional C pointer.
Only admitted pointers receive expansion handles. Pulls continue to consume the
existing per-session credits and remaining context bytes. Cheap index summaries
therefore cannot admit more distinct instruction bodies than the category allows.
Required C instructions are delivered in the bootstrap body in both modes.

Engine-3 packages label selected/indexed C entries with their category and freeze
that category in replay facts. Their policy snapshot includes the limits. Older
engines omit both the new label and census key, preserving their seals. Historical
recompilation uses frozen facts even after a later policy change. Fresh delivery
still checks the exact current revision and live authority, as described in
[governed policy](governed-policy.md).

Category caps implement one part of roadmap L3. Recorded C waivers and the full
unenforceability decision table remain open. Quotas do not prove that a model
obeys an instruction or that a selected limit improves task outcomes. See the
[verification report](verification/instruction-limits-2026-09-08.md).
