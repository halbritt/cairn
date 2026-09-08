# Versioned relations and B demotion

Drafts can carry up to 32 explicit `relations`. Each entry names `record_id`,
`version`, and one of `derived_from`, `specializes`, or `contradicts`. These are
retained version-to-version links, not instructions to change the target record.
They grant no authority and are not substitutes for independently captured B
evidence. Supersession and scope broadening still need separate governed paths.

A referencing version cannot broaden the target version's repository/task/run
scope, declared applicability or current sensitivity. These checks use the stored
sensitivity even when an edit omits that field. New links can refer to retained
historical versions; they do not pretend those versions are current. Later edits
preserve earlier links, and historical recompilation restores the original set.

`cairn demote` accepts JSON `request_id`, `record_id`, `expected_version`, and
`grant_id`. The existing `correct` capability authorizes lowering an active B
claim to A. The operation uses compare-and-swap and emits no authority audit event.
It creates a new ordinary version for future eligibility; consumed B versions,
promotion/correction events, evidence links and use history remain intact.

Demotion refuses while an active C record or open conflict cites the claim,
including through known transitive versioned relations. Traversal starts with all
retained versions of the logical record. It refuses beyond 1,000 reachable
versions instead of assuming a partial traversal is complete. These are explicit
links; Cairn does not infer undeclared derivations from similar text.

Retraction previews now list those affected versions and their retained uses.
The cap is 1,000 versions and 1,000 uses. A fingerprint of the dependency set,
current lifecycle/version and exposure generations rejects a preview after new
links, dependent edits or dependent exposures. Old preview tokens issued before
migration 013 must be refreshed. This does not certify that a person reviewed
the preview or accepted the impact.

The standalone `impact` command remains a paginated direct-use inspection.
`preview-retract` supplies the broader known-relation analysis required before a
retraction. [Ordinary deletion](ordinary-delete.md) now removes unreferenced A
notes; [D forgetting and purge effects](deletion.md) cover retained record bodies
and known managed copies. Evidence object dependencies, explicit supersession
and automatic dependent qualification remain unfinished. Retracting a source
does not automatically revoke every dependent
instruction; authority and affected consumers still require their own review.
