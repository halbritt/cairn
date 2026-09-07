CREATE TABLE cairn.index_session (
 receipt_id uuid PRIMARY KEY REFERENCES cairn.retrieval_receipt(receipt_id),
 expires_at timestamptz NOT NULL DEFAULT transaction_timestamp()+interval '15 minutes',
 credits integer NOT NULL DEFAULT 4 CHECK(credits BETWEEN 0 AND 4),
 remaining_bytes integer NOT NULL CHECK(remaining_bytes>=0)
);
CREATE TABLE cairn.index_handle (
 handle uuid PRIMARY KEY,
 receipt_id uuid NOT NULL REFERENCES cairn.index_session(receipt_id),
 record_id uuid NOT NULL,
 version integer NOT NULL,
 UNIQUE(receipt_id,record_id,version),
 FOREIGN KEY(receipt_id,record_id,version) REFERENCES cairn.record_use(receipt_id,record_id,version)
);
ALTER TABLE cairn.record_use ADD COLUMN exposure_kind text NOT NULL DEFAULT 'body' CHECK(exposure_kind IN ('body','index'));
-- Preserve the original index exposure. A later body pull has its own usage
-- observation and invalidates any pre-pull lifecycle impact preview.
CREATE TRIGGER advance_expansion_generation AFTER INSERT ON cairn.usage_observation
 FOR EACH ROW WHEN(NEW.signal='expanded') EXECUTE FUNCTION cairn.advance_use_generation();
