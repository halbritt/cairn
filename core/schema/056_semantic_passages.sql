-- Rebuildable, local derivations. Current versions and eligibility remain owned
-- by memory_record/record_version and the compiler, never by this index.
CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public;

CREATE TABLE cairn.semantic_document (
 record_id uuid PRIMARY KEY REFERENCES cairn.memory_record(record_id) ON DELETE CASCADE,
 version integer NOT NULL,
 body_sha256 text NOT NULL CHECK (body_sha256 ~ '^[0-9a-f]{64}$'),
 model_sha256 text NOT NULL CHECK (model_sha256 ~ '^[0-9a-f]{64}$'),
 indexed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 FOREIGN KEY(record_id,version) REFERENCES cairn.record_version(record_id,version) ON DELETE CASCADE
);
CREATE TABLE cairn.semantic_passage (
 record_id uuid NOT NULL REFERENCES cairn.semantic_document(record_id) ON DELETE CASCADE,
 ordinal integer NOT NULL CHECK (ordinal BETWEEN 0 AND 255),
 byte_offset integer NOT NULL CHECK (byte_offset >= 0),
 byte_length integer NOT NULL CHECK (byte_length > 0),
 embedding public.vector(384) NOT NULL,
 PRIMARY KEY(record_id,ordinal)
);
CREATE TABLE cairn.semantic_job (
 record_id uuid PRIMARY KEY REFERENCES cairn.memory_record(record_id) ON DELETE CASCADE,
 attempts integer NOT NULL DEFAULT 0 CHECK (attempts>=0),
 retry_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 lease_id uuid,
 lease_until timestamptz,
 CHECK ((lease_id IS NULL) = (lease_until IS NULL))
);
CREATE INDEX semantic_job_due ON cairn.semantic_job(retry_at);

CREATE FUNCTION cairn.queue_semantic_record() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 DELETE FROM cairn.semantic_document WHERE record_id=NEW.record_id;
 IF NEW.lifecycle='active' THEN
  INSERT INTO cairn.semantic_job(record_id) VALUES(NEW.record_id)
  ON CONFLICT(record_id) DO UPDATE SET attempts=0,retry_at=clock_timestamp(),lease_id=NULL,lease_until=NULL;
 ELSE
  DELETE FROM cairn.semantic_job WHERE record_id=NEW.record_id;
 END IF;
 RETURN NEW;
END;
$$;
CREATE TRIGGER queue_semantic_record AFTER INSERT OR UPDATE OF current_version,lifecycle
 ON cairn.memory_record FOR EACH ROW EXECUTE FUNCTION cairn.queue_semantic_record();

-- Forgetting excludes vectors immediately, even before asynchronous body purge.
CREATE FUNCTION cairn.exclude_semantic_payload() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NEW.payload_deleted_by IS NOT NULL THEN
  DELETE FROM cairn.semantic_document WHERE record_id=NEW.record_id;
  DELETE FROM cairn.semantic_job WHERE record_id=NEW.record_id;
 END IF;
 RETURN NEW;
END;
$$;
CREATE TRIGGER exclude_semantic_payload AFTER UPDATE OF payload_deleted_by ON cairn.record_version
 FOR EACH ROW EXECUTE FUNCTION cairn.exclude_semantic_payload();

INSERT INTO cairn.semantic_job(record_id)
 SELECT m.record_id FROM cairn.memory_record m JOIN cairn.record_version v
 ON v.record_id=m.record_id AND v.version=m.current_version
 WHERE m.lifecycle='active' AND v.payload_deleted_by IS NULL;
