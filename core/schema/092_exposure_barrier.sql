-- Decouple routine retrieval/expansion exposure tracking from memory_record row updates (CAIRN-274).
-- Concurrent searches and pulls acquire compatible FOR SHARE locks on cairn.memory_record and
-- cairn.record_exposure_barrier and append to cairn.record_exposure without updating memory_record.
-- Lifecycle operations that validate retraction/deletion previews drain visible exposures into
-- memory_record.use_generation and advance exposure_epoch under FOR UPDATE + ON UPDATE RESTRICT,
-- preserving stale-preview invalidation and strict serialization against concurrent exposures.

UPDATE cairn.restore_admission SET paused=paused WHERE singleton;

CREATE TABLE cairn.record_exposure_barrier (
    record_id uuid PRIMARY KEY,
    exposure_epoch bigint NOT NULL DEFAULT 0 CHECK (exposure_epoch >= 0),
    UNIQUE (record_id, exposure_epoch)
);

CREATE TABLE cairn.record_exposure (
    record_id uuid NOT NULL,
    exposure_epoch bigint NOT NULL,
    CONSTRAINT record_exposure_barrier_fkey
        FOREIGN KEY (record_id, exposure_epoch)
        REFERENCES cairn.record_exposure_barrier(record_id, exposure_epoch)
        ON UPDATE RESTRICT ON DELETE CASCADE
) WITH (autovacuum_vacuum_scale_factor = 0.02, autovacuum_analyze_scale_factor = 0.02);

CREATE INDEX record_exposure_record_idx ON cairn.record_exposure(record_id);

CREATE OR REPLACE FUNCTION cairn.record_exposure_generation(target_id uuid)
RETURNS bigint
LANGUAGE sql
STABLE
AS $$
    SELECT m.use_generation + (SELECT count(*) FROM cairn.record_exposure e WHERE e.record_id = m.record_id)
    FROM cairn.memory_record m
    WHERE m.record_id = target_id;
$$;

CREATE OR REPLACE FUNCTION cairn.init_record_exposure_barrier() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    INSERT INTO cairn.record_exposure_barrier(record_id, exposure_epoch)
    VALUES (NEW.record_id, 0)
    ON CONFLICT (record_id) DO NOTHING;
    RETURN NEW;
END;
$$;

CREATE TRIGGER init_record_exposure_barrier
    AFTER INSERT ON cairn.memory_record
    FOR EACH ROW EXECUTE FUNCTION cairn.init_record_exposure_barrier();

INSERT INTO cairn.record_exposure_barrier(record_id, exposure_epoch)
SELECT record_id, 0 FROM cairn.memory_record
ON CONFLICT (record_id) DO NOTHING;

CREATE OR REPLACE FUNCTION cairn.advance_use_generation() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    ep bigint;
BEGIN
    PERFORM 1 FROM cairn.memory_record WHERE record_id = NEW.record_id FOR SHARE;
    SELECT b.exposure_epoch INTO ep
    FROM cairn.record_exposure_barrier b
    WHERE b.record_id = NEW.record_id
    FOR SHARE;
    IF ep IS NULL THEN
        INSERT INTO cairn.record_exposure_barrier(record_id, exposure_epoch)
        VALUES (NEW.record_id, 0)
        ON CONFLICT (record_id) DO UPDATE SET exposure_epoch = cairn.record_exposure_barrier.exposure_epoch
        RETURNING exposure_epoch INTO ep;
    END IF;
    INSERT INTO cairn.record_exposure(record_id, exposure_epoch)
    VALUES (NEW.record_id, ep);
    RETURN NEW;
END;
$$;
