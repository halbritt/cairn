-- Imported expectations remain separate from locally observed audit history.
ALTER TABLE cairn.authority_event DROP CONSTRAINT authority_event_event_type_check;
ALTER TABLE cairn.authority_event ADD CHECK (event_type IN ('bootstrap','grant','revoke_grant','promote','issue','correct','supersede','authorize_scope','policy_revise','retract','dispute','resolve','redact','forget','reapply_recovery'));
CREATE TABLE cairn.recovery_application (
 application_id uuid PRIMARY KEY,
 event_id uuid NOT NULL UNIQUE REFERENCES cairn.authority_event(event_id),
 source_root uuid NOT NULL REFERENCES cairn.authority_grant(grant_id),
 source_sha256 text NOT NULL CHECK(source_sha256 ~ '^[0-9a-f]{64}$'),
 source_record jsonb NOT NULL CHECK(jsonb_typeof(source_record)='object'),
 actions jsonb NOT NULL CHECK(jsonb_typeof(actions)='array'),
 CHECK(source_record->>'sha256'=source_sha256 AND source_record->>'root_grant_id'=source_root::text)
);
CREATE TRIGGER immutable_recovery_application BEFORE UPDATE OR DELETE ON cairn.recovery_application
 FOR EACH ROW EXECUTE FUNCTION cairn.immutable_audit();
CREATE FUNCTION cairn.match_recovery_application() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NOT EXISTS(SELECT 1 FROM cairn.authority_event e WHERE e.event_id=NEW.event_id
 AND e.event_type='reapply_recovery' AND e.subject_id=NEW.application_id
 AND e.previous_version=0 AND e.resulting_version=1 AND e.transaction_id=pg_current_xact_id())
 THEN RAISE EXCEPTION 'recovery application requires matching atomic authority event'; END IF;
 RETURN NEW;
END;
$$;
CREATE CONSTRAINT TRIGGER match_recovery_application AFTER INSERT ON cairn.recovery_application
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION cairn.match_recovery_application();

-- A source export can know a reserved file whose receipt is absent from this
-- backup. This records imported custody, never an invented launch observation.
CREATE TABLE cairn.recovery_context (
 deletion_id uuid NOT NULL REFERENCES cairn.deletion_request(deletion_id),
 receipt_id uuid NOT NULL,
 application_id uuid NOT NULL REFERENCES cairn.recovery_application(application_id),
 directory text NOT NULL CHECK(length(directory) BETWEEN 1 AND 4096),
 directory_device text NOT NULL CHECK(directory_device ~ '^[0-9]{1,20}$'),
 directory_inode text NOT NULL CHECK(directory_inode ~ '^[0-9]{1,20}$'),
 ownership_id uuid NOT NULL,
 body_sha256 text NOT NULL CHECK(body_sha256 ~ '^[0-9a-f]{64}$'),
 PRIMARY KEY(deletion_id,receipt_id)
);
CREATE TRIGGER immutable_recovery_context BEFORE UPDATE OR DELETE ON cairn.recovery_context
 FOR EACH ROW EXECUTE FUNCTION cairn.immutable_audit();
