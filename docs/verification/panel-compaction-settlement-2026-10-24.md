# CAIRN-227 two-notice compaction settlement probe

The [disposable probe](../../scripts/check_panel_compaction_settlement.py)
materializes the frozen `compaction_before_after_settlement` notice bodies in a
fresh PostgreSQL database. It uses the actual authenticated CLI/API, pinned
coordination and memory hooks, and compact inbox helper. A synthetic owner
process and Store claim stand in for host queue admission.

The first notice is leased to one native turn. After synthetic memory
`PreCompact` and compact `SessionStart` callbacks, the coordinator's
`SessionStart` returns its exact source and scoped handle while the delivery
remains leased. The helper reads the whole source and commits one
handled settlement. An identical helper retry returns the same result. After
the first turn closes, the second notice is published and leased to a distinct
turn. After the second synthetic compact callback, the coordinator restores
only that pending notice;
the settled first notice is absent. Its exact whole-source read and settlement
also succeed with a stable retry. Final Store reads require two handled
deliveries, one attempt each, distinct native attempts and committed per-item
journals. The probe fails if compaction loses the active notice or reinjects the
settled one.

This establishes hook, Store, source identity and settlement wiring under
synthetic callbacks. It does not observe a Codex model compaction, queue wake,
actor decision, provider usage, or accepted task outcome. The four paired arms
and rollout gate remain unrun and deferred.
