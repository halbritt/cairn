-- A missing JSON session_id compares as NULL under <>, so the original
-- trigger accepted it. Require a present, matching value at the database edge.
CREATE OR REPLACE FUNCTION cairn.match_restore_resume() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NOT EXISTS(SELECT 1 FROM cairn.authority_event e WHERE e.event_id=NEW.event_id
 AND e.event_type='resume_restore' AND e.subject_id=NEW.resume_id
 AND e.previous_version=0 AND e.resulting_version=1 AND e.transaction_id=pg_current_xact_id())
 OR (NEW.verification->>'session_id') IS DISTINCT FROM NEW.session_id::text
 THEN RAISE EXCEPTION 'restore resume requires matching atomic authority and verification'; END IF;
 RETURN NEW;
END;
$$;
