CREATE TABLE cairn.record_relation (
 from_id uuid NOT NULL,
 from_version integer NOT NULL,
 to_id uuid NOT NULL,
 to_version integer NOT NULL,
 relation text NOT NULL CHECK(relation IN ('derived_from','specializes','contradicts')),
 created_by text NOT NULL DEFAULT current_setting('cairn.caller'),
 created_at timestamptz NOT NULL DEFAULT transaction_timestamp(),
 PRIMARY KEY(from_id,from_version,to_id,to_version,relation),
 FOREIGN KEY(from_id,from_version) REFERENCES cairn.record_version(record_id,version),
 FOREIGN KEY(to_id,to_version) REFERENCES cairn.record_version(record_id,version)
);
CREATE INDEX relation_reverse ON cairn.record_relation(to_id,to_version);
CREATE FUNCTION cairn.advance_relation_generation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 UPDATE cairn.memory_record SET use_generation=use_generation+1 WHERE record_id=NEW.to_id;
 RETURN NEW;
END;
$$;
CREATE TRIGGER advance_relation_generation AFTER INSERT ON cairn.record_relation
 FOR EACH ROW EXECUTE FUNCTION cairn.advance_relation_generation();
ALTER TABLE cairn.retraction_preview ADD COLUMN dependency_digest bytea;
