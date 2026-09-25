-- Row-level UPDATE/DELETE triggers do not fire for TRUNCATE. Preserve the
-- append-only contract for accidental table-wide operations too.
CREATE TRIGGER immutable_truncate BEFORE TRUNCATE ON cairn.authority_event
 FOR EACH STATEMENT EXECUTE FUNCTION cairn.immutable_audit();
CREATE TRIGGER immutable_truncate BEFORE TRUNCATE ON cairn.audit_checkpoint
 FOR EACH STATEMENT EXECUTE FUNCTION cairn.immutable_audit();
CREATE TRIGGER immutable_truncate BEFORE TRUNCATE ON cairn.restore_fence
 FOR EACH STATEMENT EXECUTE FUNCTION cairn.immutable_audit();
CREATE TRIGGER immutable_truncate BEFORE TRUNCATE ON cairn.restore_session
 FOR EACH STATEMENT EXECUTE FUNCTION cairn.immutable_audit();
CREATE TRIGGER immutable_truncate BEFORE TRUNCATE ON cairn.restore_resume
 FOR EACH STATEMENT EXECUTE FUNCTION cairn.immutable_audit();
CREATE TRIGGER immutable_truncate BEFORE TRUNCATE ON cairn.managed_context
 FOR EACH STATEMENT EXECUTE FUNCTION cairn.immutable_audit();
CREATE TRIGGER immutable_truncate BEFORE TRUNCATE ON cairn.policy_revision
 FOR EACH STATEMENT EXECUTE FUNCTION cairn.immutable_audit();
CREATE TRIGGER immutable_truncate BEFORE TRUNCATE ON cairn.recovery_application
 FOR EACH STATEMENT EXECUTE FUNCTION cairn.immutable_audit();
CREATE TRIGGER immutable_truncate BEFORE TRUNCATE ON cairn.recovery_context
 FOR EACH STATEMENT EXECUTE FUNCTION cairn.immutable_audit();
CREATE TRIGGER immutable_truncate BEFORE TRUNCATE ON cairn.deletion_effect_event
 FOR EACH STATEMENT EXECUTE FUNCTION cairn.immutable_audit();
