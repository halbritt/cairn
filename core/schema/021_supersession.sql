ALTER TABLE cairn.memory_record DROP CONSTRAINT memory_record_lifecycle_check;
ALTER TABLE cairn.memory_record ADD CHECK (lifecycle IN ('active','superseded','retracted','tombstoned'));
ALTER TABLE cairn.authority_event DROP CONSTRAINT authority_event_event_type_check;
ALTER TABLE cairn.authority_event ADD CHECK (event_type IN ('bootstrap','grant','revoke_grant','promote','issue','correct','supersede','retract','dispute','resolve','redact','forget'));
CREATE OR REPLACE FUNCTION cairn.match_authority() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NOT EXISTS (SELECT 1 FROM cairn.authority_event e JOIN cairn.record_version v ON v.record_id=NEW.record_id AND v.version=NEW.version
 WHERE e.event_id=NEW.event_id AND e.subject_id=NEW.record_id AND e.resulting_version=NEW.version AND e.transaction_id=pg_current_xact_id()
 AND ((e.event_type IN ('promote','correct','supersede') AND v.version_class='B') OR (e.event_type='issue' AND v.version_class='C') OR e.event_type IN ('retract','redact','forget')))
 THEN RAISE EXCEPTION 'authority event does not match record transition'; END IF;
 RETURN NEW;
END;
$$;

-- Replacement is history, not derivation or authority transfer.
CREATE TABLE cairn.record_supersession (
 record_id uuid PRIMARY KEY,
 previous_version integer NOT NULL,
 retired_version integer NOT NULL,
 replacement_id uuid NOT NULL,
 replacement_version integer NOT NULL,
 actor text NOT NULL DEFAULT current_setting('cairn.caller'),
 reason text NOT NULL CHECK(length(btrim(reason)) BETWEEN 8 AND 4000),
 created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
 event_id uuid REFERENCES cairn.authority_event(event_id),
 FOREIGN KEY(record_id,previous_version) REFERENCES cairn.record_version(record_id,version),
 FOREIGN KEY(record_id,retired_version) REFERENCES cairn.record_version(record_id,version),
 FOREIGN KEY(replacement_id,replacement_version) REFERENCES cairn.record_version(record_id,version),
 CHECK(record_id<>replacement_id AND retired_version=previous_version+1)
);
CREATE INDEX supersession_replacement ON cairn.record_supersession(replacement_id,replacement_version);
CREATE TABLE cairn.supersession_affected (
 superseded_id uuid NOT NULL REFERENCES cairn.record_supersession(record_id),
 record_id uuid NOT NULL,
 version integer NOT NULL,
 PRIMARY KEY(superseded_id,record_id,version),
 FOREIGN KEY(record_id,version) REFERENCES cairn.record_version(record_id,version)
);
CREATE INDEX supersession_affected_reverse ON cairn.supersession_affected(record_id,version);
CREATE FUNCTION cairn.match_supersession() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NOT EXISTS(SELECT 1 FROM cairn.memory_record m JOIN cairn.record_version v ON v.record_id=m.record_id AND v.version=m.current_version
 WHERE m.record_id=NEW.record_id AND m.lifecycle='superseded' AND m.current_version=NEW.retired_version
 AND ((v.version_class='A' AND NEW.event_id IS NULL) OR (v.version_class='B' AND EXISTS(
 SELECT 1 FROM cairn.authority_event e WHERE e.event_id=NEW.event_id AND e.event_type='supersede'
 AND e.subject_id=NEW.record_id AND e.previous_version=NEW.previous_version AND e.resulting_version=NEW.retired_version AND e.transaction_id=pg_current_xact_id()))))
 THEN RAISE EXCEPTION 'supersession requires matching atomic retirement'; END IF;
 RETURN NEW;
END;
$$;
CREATE CONSTRAINT TRIGGER match_supersession AFTER INSERT ON cairn.record_supersession
 DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION cairn.match_supersession();
