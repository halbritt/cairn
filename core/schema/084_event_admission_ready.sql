-- Drain admitted transactions and invalidate blocked old RR/Serializable snapshots.
-- Rewrite the tuple without changing restore state; locking alone is insufficient.
-- The migration ledger itself is the compatibility marker: partial binaries
-- ending at 083 reject this schema, and enforcement requires this floor.
UPDATE cairn.restore_admission SET paused=paused WHERE singleton;
