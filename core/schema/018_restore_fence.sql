CREATE TABLE cairn.retrieval_generation (
 singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton),
 generation bigint NOT NULL CHECK(generation>=0)
);
INSERT INTO cairn.retrieval_generation(singleton,generation) VALUES(true,0);
ALTER TABLE cairn.retrieval_receipt ADD COLUMN generation bigint NOT NULL DEFAULT 0 CHECK(generation>=0);
CREATE TABLE cairn.restore_fence (
 fence_id uuid PRIMARY KEY,
 generation bigint NOT NULL UNIQUE CHECK(generation>0),
 reason text NOT NULL CHECK(length(reason) BETWEEN 8 AND 4000),
 observed_by text NOT NULL DEFAULT current_setting('cairn.caller'),
 observed_at timestamptz NOT NULL DEFAULT transaction_timestamp()
);
CREATE TRIGGER immutable_restore_fence BEFORE UPDATE OR DELETE ON cairn.restore_fence
 FOR EACH ROW EXECUTE FUNCTION cairn.immutable_audit();
