ALTER TABLE cairn.authority_event DROP CONSTRAINT authority_event_event_type_check;
ALTER TABLE cairn.authority_event ADD CHECK (event_type IN ('bootstrap','grant','revoke_grant','promote','issue','correct','supersede','authorize_scope','retract','dispute','resolve','redact','forget'));
CREATE OR REPLACE FUNCTION cairn.match_authority() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NOT EXISTS (SELECT 1 FROM cairn.authority_event e JOIN cairn.record_version v ON v.record_id=NEW.record_id AND v.version=NEW.version
 WHERE e.event_id=NEW.event_id AND e.subject_id=NEW.record_id AND e.resulting_version=NEW.version AND e.transaction_id=pg_current_xact_id()
 AND ((e.event_type IN ('promote','correct','supersede') AND v.version_class='B') OR (e.event_type='issue' AND v.version_class='C') OR (e.event_type='authorize_scope' AND v.version_class IN ('B','C')) OR e.event_type IN ('retract','redact','forget')))
 THEN RAISE EXCEPTION 'authority event does not match record transition'; END IF;
 RETURN NEW;
END;
$$;
-- Latest version supplies current scope authority across subsequent same-scope
-- edits. Earlier rows preserve history without a full ancestry requirement.
CREATE TABLE cairn.scope_authorization (
 record_id uuid NOT NULL,
 version integer NOT NULL,
 previous_version integer NOT NULL,
 event_id uuid NOT NULL UNIQUE REFERENCES cairn.authority_event(event_id),
 grant_id uuid NOT NULL REFERENCES cairn.authority_grant(grant_id),
 PRIMARY KEY(record_id,version),
 FOREIGN KEY(record_id,version) REFERENCES cairn.record_version(record_id,version),
 FOREIGN KEY(record_id,previous_version) REFERENCES cairn.record_version(record_id,version),
 CHECK(version=previous_version+1)
);
CREATE FUNCTION cairn.match_scope_authorization() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NOT EXISTS(SELECT 1 FROM cairn.authority_event e JOIN cairn.memory_record m ON m.record_id=e.subject_id
 WHERE e.event_id=NEW.event_id AND e.event_type='authorize_scope' AND e.subject_id=NEW.record_id
 AND e.previous_version=NEW.previous_version AND e.resulting_version=NEW.version
 AND e.transaction_id=pg_current_xact_id() AND m.current_version=NEW.version AND m.lifecycle='active')
 THEN RAISE EXCEPTION 'scope authorization requires matching atomic transition'; END IF;
 RETURN NEW;
END;
$$;
CREATE CONSTRAINT TRIGGER match_scope_authorization AFTER INSERT ON cairn.scope_authorization
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION cairn.match_scope_authorization();
