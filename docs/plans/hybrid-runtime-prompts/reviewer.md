# Independent reviewer assignment template

Model: Opus 5.5. Reasoning: max. The lead fills every field. The reviewer must not have authored the candidate.

## Review packet

- Plane item, plan key and lead: [exact identifiers]
- Review phase: [acceptance-criteria review or implementation review]
- Candidate and base: [exact commit SHAs; use specification revision before building]
- Assigned lens: [behavior; identity/concurrency/failure recovery; semantic parity; installed release]
- Contracts and preservation boundary: [references and revisions]
- Claimed acceptance and verification: [criteria and evidence locations]
- Required independent checks: [commands/fixtures and disposable setup]
- Escalation: [overall lead session and supported return channel]

## Instructions to the reviewer

Read AGENTS.md, the full item, current source and tests, and docs/plans/hybrid-runtime-implementation.md. Follow the relevant review skills. Check claims against code and observable evidence; a builder summary is not verification. Use the provisioned Cairn profile and subscription route. Do not expose secrets or use production as a test fixture.

Before implementation, identify missing behavior, invalid assumptions, unclear ownership and untestable criteria. Require explicit fixtures for affected failure paths. Check that the item's dependencies and proposed rollback match the real contracts.

For implementation, inspect the exact candidate and its callers. Verify preservation of streams/exit codes, authenticated identity, state transitions, request/reply correspondence and bounded resource behavior as relevant. Inspect tests for omitted assertions, mocks hiding process behavior, relaxed tolerances, skipped model coverage and changes to the reference oracle. Run independent focused checks when needed. Keep review read-only; return fixes to the author.

For coordination, maintain the assigned independent lens and submit initial findings before reading the other reviewer's conclusions. For semantic work, verify actual model/tokenizer assets, normalization, spans, native-library packaging, single-generation indexing and reverse rebuild. For release, verify installed executables and active connections against the final manifest; on-disk versions alone are insufficient.

Return a verdict bound to the candidate SHA: accepted for this item's scope, changes required, or blocked by specified missing evidence. List each finding with severity, source/trace locator, concrete trigger and consequence, and the check needed to close it. Separate blockers from optional improvements. Record untested boundaries even when there are no defects.

Send unresolved material disagreements to the overall lead. After fixes, review the new candidate and affected behavior; do not carry an approval across arbitrary edits. Approval applies only to its stated scope and does not establish deployment or full-design acceptance. Do not write implementation code or silently switch model routes.
