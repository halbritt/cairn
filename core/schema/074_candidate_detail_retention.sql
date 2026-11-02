-- Candidate detail is diagnostic history with an operator-selected lifetime. A
-- non-null marker means its evaluated-set rows were removed by retention, so
-- explanation and recompilation must refuse rather than report an empty set.
-- Receipt identity, seal, package bytes and record_use are not affected.
--
-- Statement order matters on a live store. Ordinary transactions lock the admission
-- row and then reach retrieval_receipt, so this takes no lock on restore_admission,
-- and takes its only exclusive lock last.

-- A storage rewrite writes the admission row without changing it, so that a
-- transaction whose snapshot predates the rewrite fails instead of reading the new
-- relation. Only a real transition out of a restore pause needs the resume record.
CREATE OR REPLACE FUNCTION cairn.require_restore_resume() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF OLD.paused IS NOT DISTINCT FROM NEW.paused AND OLD.session_id IS NOT DISTINCT FROM NEW.session_id THEN
  RETURN NEW;
 END IF;
 IF NOT NEW.paused AND NEW.session_id IS NOT NULL AND NOT EXISTS(
 SELECT 1 FROM cairn.restore_resume r JOIN cairn.authority_event e USING(event_id)
 WHERE r.session_id=NEW.session_id AND e.transaction_id=pg_current_xact_id())
 THEN RAISE EXCEPTION 'unpausing requires a new verified resume in this transaction'; END IF;
 RETURN NEW;
END;
$$;
-- Expired rows are deleted in small batches; vacuum early enough that their space
-- is reused before the table extends. The default 20% is too late at this size.
ALTER TABLE cairn.retrieval_candidate SET (autovacuum_vacuum_scale_factor=0.02, autovacuum_analyze_scale_factor=0.02);
ALTER TABLE cairn.retrieval_receipt ADD COLUMN candidate_detail_expired_at timestamptz;
CREATE INDEX retrieval_receipt_unexpired_detail ON cairn.retrieval_receipt(created_at)
 WHERE candidate_detail_expired_at IS NULL;
