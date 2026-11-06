# Builder assignment template

Model: Sol. Reasoning: max. The assigning lead fills every assignment field before dispatch.

## Assignment packet

- Plane item and plan key: [exact identifiers and links]
- Lead and return channel: [actual session identity and supported coordination route]
- Base commit: [SHA]
- Outcome and non-goals: [one bounded deliverable]
- Owned paths: [exclusive edit scope; name shared-file owner]
- Contracts and dependencies: [revisions and landed commits]
- Acceptance criteria: [observable successful and failing behavior]
- Required verification: [exact commands, fixtures and disposable environment]
- Documentation and rollback obligations: [affected surfaces]
- Reviewer: [independent Opus 5.5 Max session, assigned by lead]

## Instructions to the builder

Read AGENTS.md, the complete Plane item, the relevant source/tests, and docs/plans/hybrid-runtime-implementation.md. Confirm that dependencies are satisfied and the owned paths are free. Retrieve relevant Cairn decisions using the provisioned profile; verify them against current source. Report a conflicting contract or missing dependency to the lead before making dependent edits.

Implement the assigned behavior and its failure cases. Preserve store-owned identity, observation channels, persisted state and declared public contracts. Do not weaken tests, create production mock fallbacks, swallow errors, or silently broaden this assignment. Keep frozen trials intact. Ask the lead to allocate any shared-file edit or necessary scope extension.

Use an isolated worktree created through git-wt create. Run focused tests and required repository checks locally, with disposable PostgreSQL only. Update affected docs alongside the code. Record exact commands, results, skipped coverage, fixture inputs and candidate commit. A skipped model or integration test is missing coverage, not success.

Commit the candidate and request the assigned review through the lead. Remain responsible for fixes, re-review, landing the verified change, pushing main and removing your branch/worktree under the repository's integration procedure. Coordinate landing with the overall queue. Do not end your assignment with an unmerged branch or uncommitted files. Re-verify relevant behavior if trunk reconciliation changes the reviewed result.

Return the Plane item, integrated commit, changed behavior, tests and evidence, reviewer findings/closure, documentation updates and remaining limitations. Do not claim installed deployment unless you were assigned it and observed it. Do not recruit more builders or change model/provider routes; a routing/authentication failure goes to the lead with no OpenRouter fallback.
