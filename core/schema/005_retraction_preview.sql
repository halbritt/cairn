-- Consumption changes the row used to serialize lifecycle mutations, even when
-- the record's content version stays the same. A stale snapshot must retry.
ALTER TABLE cairn.memory_record ADD COLUMN use_generation bigint NOT NULL DEFAULT 0;
CREATE FUNCTION cairn.advance_use_generation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    UPDATE cairn.memory_record SET use_generation=use_generation+1 WHERE record_id=NEW.record_id;
    RETURN NEW;
END;
$$;
CREATE TRIGGER advance_use_generation AFTER INSERT ON cairn.record_use
    FOR EACH ROW EXECUTE FUNCTION cairn.advance_use_generation();
CREATE TABLE cairn.retraction_preview (
    preview_id uuid PRIMARY KEY,
    caller text NOT NULL DEFAULT current_setting('cairn.caller'),
    record_id uuid NOT NULL,
    version integer NOT NULL,
    use_generation bigint NOT NULL,
    expires_at timestamptz NOT NULL DEFAULT transaction_timestamp()+interval '1 hour',
    FOREIGN KEY(record_id,version) REFERENCES cairn.record_version(record_id,version)
);
