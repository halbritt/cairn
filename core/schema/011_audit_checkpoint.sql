-- Restore integrity metadata only. No ordinary content or audit reasons are
-- copied into this catalog. Membership is explicit, never a sequence maximum.
CREATE TABLE cairn.audit_checkpoint (
 checkpoint_id uuid PRIMARY KEY,
 export_id text NOT NULL,
 manifest jsonb NOT NULL,
 created_at timestamptz NOT NULL DEFAULT transaction_timestamp()
);
CREATE TRIGGER immutable_checkpoint BEFORE UPDATE OR DELETE ON cairn.audit_checkpoint
 FOR EACH ROW EXECUTE FUNCTION cairn.immutable_audit();
