-- Normal operations take a shared lock on this row before reading or writing.
-- Restore transitions take it exclusively; administrative recovery uses an
-- explicit internal path and never turns an agent request into an operator.
CREATE TABLE cairn.restore_session (
 session_id uuid PRIMARY KEY,
 fence_id uuid NOT NULL UNIQUE REFERENCES cairn.restore_fence(fence_id),
 root_grant_id uuid NOT NULL REFERENCES cairn.authority_grant(grant_id),
 target text NOT NULL CHECK(length(btrim(target)) BETWEEN 1 AND 512),
 reason text NOT NULL CHECK(length(btrim(reason)) BETWEEN 8 AND 4000),
 begun_by text NOT NULL DEFAULT current_setting('cairn.caller'),
 begun_at timestamptz NOT NULL DEFAULT transaction_timestamp()
);
CREATE TRIGGER immutable_restore_session BEFORE UPDATE OR DELETE ON cairn.restore_session
 FOR EACH ROW EXECUTE FUNCTION cairn.immutable_audit();
CREATE TABLE cairn.restore_admission (
 singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton),
 session_id uuid REFERENCES cairn.restore_session(session_id),
 paused boolean NOT NULL DEFAULT false,
 CHECK(NOT paused OR session_id IS NOT NULL)
);
INSERT INTO cairn.restore_admission(singleton) VALUES(true);

ALTER TABLE cairn.authority_event DROP CONSTRAINT authority_event_event_type_check;
ALTER TABLE cairn.authority_event ADD CHECK (event_type IN ('bootstrap','grant','revoke_grant','promote','issue','correct','supersede','authorize_scope','policy_revise','retract','dispute','resolve','redact','forget','reapply_recovery','resume_restore'));
CREATE TABLE cairn.restore_resume (
 resume_id uuid PRIMARY KEY,
 session_id uuid NOT NULL UNIQUE REFERENCES cairn.restore_session(session_id),
 event_id uuid NOT NULL UNIQUE REFERENCES cairn.authority_event(event_id),
 policy text NOT NULL CHECK(policy='local-restore/1'),
 verification jsonb NOT NULL CHECK(jsonb_typeof(verification)='object' AND verification->>'ready'='true')
);
CREATE TRIGGER immutable_restore_resume BEFORE UPDATE OR DELETE ON cairn.restore_resume
 FOR EACH ROW EXECUTE FUNCTION cairn.immutable_audit();
CREATE FUNCTION cairn.match_restore_resume() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NOT EXISTS(SELECT 1 FROM cairn.authority_event e WHERE e.event_id=NEW.event_id
 AND e.event_type='resume_restore' AND e.subject_id=NEW.resume_id
 AND e.previous_version=0 AND e.resulting_version=1 AND e.transaction_id=pg_current_xact_id())
 OR NEW.verification->>'session_id'<>NEW.session_id::text
 THEN RAISE EXCEPTION 'restore resume requires matching atomic authority and verification'; END IF;
 RETURN NEW;
END;
$$;
CREATE CONSTRAINT TRIGGER match_restore_resume AFTER INSERT ON cairn.restore_resume
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION cairn.match_restore_resume();
CREATE FUNCTION cairn.require_restore_resume() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NOT NEW.paused AND NEW.session_id IS NOT NULL AND NOT EXISTS(
 SELECT 1 FROM cairn.restore_resume r JOIN cairn.authority_event e USING(event_id)
 WHERE r.session_id=NEW.session_id AND e.transaction_id=pg_current_xact_id())
 THEN RAISE EXCEPTION 'unpausing requires a new verified resume in this transaction'; END IF;
 RETURN NEW;
END;
$$;
CREATE CONSTRAINT TRIGGER require_restore_resume AFTER UPDATE ON cairn.restore_admission
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION cairn.require_restore_resume();
