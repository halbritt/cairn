-- Candidate detail is diagnostic history with an operator-selected lifetime. A
-- non-null marker means its evaluated-set rows were removed by retention, so
-- explanation and recompilation must refuse rather than report an empty set.
-- Receipt identity, seal, package bytes and record_use are not affected.
ALTER TABLE cairn.retrieval_receipt ADD COLUMN candidate_detail_expired_at timestamptz;
CREATE INDEX retrieval_receipt_unexpired_detail ON cairn.retrieval_receipt(created_at)
 WHERE candidate_detail_expired_at IS NULL;
-- Expired rows are deleted in small batches; vacuum early enough that their space
-- is reused before the table extends. The default 20% is too late at this size.
ALTER TABLE cairn.retrieval_candidate SET (autovacuum_vacuum_scale_factor=0.02, autovacuum_analyze_scale_factor=0.02);
-- A storage rewrite writes this row without changing it, so that a transaction
-- whose snapshot predates the rewrite fails instead of reading the new relation.
-- Only a real transition out of a restore pause needs the resume record.
DROP TRIGGER require_restore_resume ON cairn.restore_admission;
CREATE CONSTRAINT TRIGGER require_restore_resume AFTER UPDATE ON cairn.restore_admission
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
 WHEN (OLD.paused IS DISTINCT FROM NEW.paused OR OLD.session_id IS DISTINCT FROM NEW.session_id)
 EXECUTE FUNCTION cairn.require_restore_resume();
