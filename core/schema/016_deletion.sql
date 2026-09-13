CREATE TABLE cairn.deletion_request (
 deletion_id uuid PRIMARY KEY,
 record_id uuid NOT NULL REFERENCES cairn.memory_record(record_id),
 event_id uuid NOT NULL REFERENCES cairn.authority_event(event_id),
 caller text NOT NULL DEFAULT current_setting('cairn.caller'),
 repo text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
 UNIQUE(record_id)
);
CREATE TABLE cairn.deletion_effect (
 deletion_id uuid NOT NULL REFERENCES cairn.deletion_request(deletion_id),
 target_type text NOT NULL CHECK(target_type IN ('db_record_bodies','db_mutation_responses','db_retrieval_package','storage_and_backups','unmanaged_copies','retained_metadata','run_artifacts','provider_delivery','supporting_evidence','related_record')),
 target_id text NOT NULL,
 status text NOT NULL CHECK(status IN ('pending','running','completed','failed','not_possible')),
 attempts integer NOT NULL DEFAULT 0 CHECK(attempts>=0),
 last_error text NOT NULL DEFAULT '',
 residual text NOT NULL DEFAULT '',
 completed_at timestamptz,
 PRIMARY KEY(deletion_id,target_type,target_id)
);
ALTER TABLE cairn.record_version ADD COLUMN payload_deleted_by uuid REFERENCES cairn.deletion_request(deletion_id);
ALTER TABLE cairn.record_version DROP CONSTRAINT record_version_body_check;
ALTER TABLE cairn.record_version ADD CHECK (octet_length(body) BETWEEN 1 AND 65536 OR (body='' AND payload_deleted_by IS NOT NULL));
ALTER TABLE cairn.retrieval_receipt ADD COLUMN payload_deleted_by uuid REFERENCES cairn.deletion_request(deletion_id);
ALTER TABLE cairn.retrieval_receipt ALTER COLUMN semantic_body DROP NOT NULL;
ALTER TABLE cairn.retrieval_receipt ADD CHECK (semantic_body IS NOT NULL OR payload_deleted_by IS NOT NULL);
ALTER TABLE cairn.mutation_request ADD COLUMN payload_deleted_by uuid REFERENCES cairn.deletion_request(deletion_id);
ALTER TABLE cairn.mutation_request ALTER COLUMN response DROP NOT NULL;
ALTER TABLE cairn.mutation_request ADD CHECK (response IS NOT NULL OR payload_deleted_by IS NOT NULL);
ALTER TABLE cairn.retraction_preview ADD COLUMN deletion_inventory_digest bytea;

-- Forgetting is a D event, independent of the former record's class.
ALTER TABLE cairn.authority_event DROP CONSTRAINT authority_event_event_type_check;
ALTER TABLE cairn.authority_event ADD CHECK (event_type IN ('bootstrap','grant','revoke_grant','promote','issue','correct','retract','dispute','resolve','redact','forget'));
CREATE OR REPLACE FUNCTION cairn.match_authority() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NOT EXISTS (SELECT 1 FROM cairn.authority_event e JOIN cairn.record_version v ON v.record_id=NEW.record_id AND v.version=NEW.version
 WHERE e.event_id=NEW.event_id AND e.subject_id=NEW.record_id AND e.resulting_version=NEW.version AND e.transaction_id=pg_current_xact_id()
 AND ((e.event_type IN ('promote','correct') AND v.version_class='B') OR (e.event_type='issue' AND v.version_class='C') OR e.event_type IN ('retract','redact','forget')))
 THEN RAISE EXCEPTION 'authority event does not match record transition'; END IF;
 RETURN NEW;
END;
$$;
CREATE TABLE cairn.deletion_dependency (
 record_id uuid NOT NULL,
 version integer NOT NULL,
 deletion_id uuid NOT NULL REFERENCES cairn.deletion_request(deletion_id),
 PRIMARY KEY(record_id,version,deletion_id),
 FOREIGN KEY(record_id,version) REFERENCES cairn.record_version(record_id,version)
);
CREATE TABLE cairn.deletion_effect_event (
 effect_event_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 deletion_id uuid NOT NULL,
 target_type text NOT NULL,
 target_id text NOT NULL,
 status text NOT NULL,
 attempts integer NOT NULL,
 error_code text NOT NULL,
 actor text NOT NULL DEFAULT current_setting('cairn.caller'),
 observed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 FOREIGN KEY(deletion_id,target_type,target_id) REFERENCES cairn.deletion_effect(deletion_id,target_type,target_id)
);
CREATE FUNCTION cairn.observe_deletion_effect() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 INSERT INTO cairn.deletion_effect_event(deletion_id,target_type,target_id,status,attempts,error_code)
 VALUES(NEW.deletion_id,NEW.target_type,NEW.target_id,NEW.status,NEW.attempts,NEW.last_error);
 RETURN NEW;
END;
$$;
CREATE TRIGGER observe_deletion_effect AFTER INSERT OR UPDATE ON cairn.deletion_effect
 FOR EACH ROW EXECUTE FUNCTION cairn.observe_deletion_effect();
CREATE TRIGGER immutable_effect_event BEFORE UPDATE OR DELETE ON cairn.deletion_effect_event
 FOR EACH ROW EXECUTE FUNCTION cairn.immutable_audit();
CREATE FUNCTION cairn.match_deletion() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NOT EXISTS(SELECT 1 FROM cairn.authority_event e JOIN cairn.memory_record m ON m.record_id=e.subject_id
 WHERE e.event_id=NEW.event_id AND e.event_type='forget' AND e.subject_id=NEW.record_id
 AND e.transaction_id=pg_current_xact_id() AND e.resulting_version=m.current_version AND m.lifecycle='tombstoned')
 THEN RAISE EXCEPTION 'deletion requires matching atomic forgetting event and tombstone'; END IF;
 RETURN NEW;
END;
$$;
CREATE CONSTRAINT TRIGGER match_deletion AFTER INSERT ON cairn.deletion_request
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION cairn.match_deletion();
