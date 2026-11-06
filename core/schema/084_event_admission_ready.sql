-- Drain old transactions before exposing the completed writer/rate contract.
-- The migration ledger itself is the compatibility marker: partial binaries
-- ending at 083 reject this schema, and enforcement requires this floor.
SELECT singleton FROM cairn.restore_admission WHERE singleton FOR UPDATE;
