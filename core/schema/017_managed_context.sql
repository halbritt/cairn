CREATE TABLE cairn.managed_context (
 receipt_id uuid PRIMARY KEY REFERENCES cairn.retrieval_receipt(receipt_id),
 directory text NOT NULL CHECK(length(directory) BETWEEN 1 AND 4096),
 directory_device text NOT NULL CHECK(directory_device ~ '^[0-9]{1,20}$'),
 ownership_id uuid NOT NULL,
 directory_inode text NOT NULL CHECK(directory_inode ~ '^[0-9]{1,20}$'),
 body_sha256 text NOT NULL CHECK(body_sha256 ~ '^[0-9a-f]{64}$'),
 registered_by text NOT NULL DEFAULT current_setting('cairn.caller'),
 registered_at timestamptz NOT NULL DEFAULT transaction_timestamp()
);
CREATE TRIGGER immutable_managed_context BEFORE UPDATE OR DELETE ON cairn.managed_context
 FOR EACH ROW EXECUTE FUNCTION cairn.immutable_audit();
ALTER TABLE cairn.deletion_effect DROP CONSTRAINT deletion_effect_target_type_check;
ALTER TABLE cairn.deletion_effect ADD CHECK(target_type IN ('db_record_bodies','db_mutation_responses','db_retrieval_package','storage_and_backups','unmanaged_copies','retained_metadata','run_artifacts','provider_delivery','supporting_evidence','related_record','managed_context'));
