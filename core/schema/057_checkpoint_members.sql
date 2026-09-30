-- Storage representations are explicit. Inline checkpoint/1 manifests remain
-- readable. Member-backed manifests keep checkpoint/1 digest semantics but put
-- no members in a members/1 stored header; pre-057 readers refuse that schema.
ALTER TABLE cairn.audit_checkpoint ADD COLUMN storage_schema text NOT NULL
 DEFAULT 'cairn.audit-checkpoint-inline/1'
 CHECK (storage_schema IN ('cairn.audit-checkpoint-inline/1','cairn.audit-checkpoint-members/1'));
CREATE TABLE cairn.audit_checkpoint_member (
 checkpoint_id uuid NOT NULL REFERENCES cairn.audit_checkpoint(checkpoint_id),
 event_id uuid NOT NULL,
 digest text NOT NULL CHECK(digest ~ '^[0-9a-f]{64}$'),
 PRIMARY KEY (checkpoint_id,event_id)
);
CREATE TRIGGER immutable_checkpoint_member BEFORE UPDATE OR DELETE ON cairn.audit_checkpoint_member
 FOR EACH ROW EXECUTE FUNCTION cairn.immutable_audit();
