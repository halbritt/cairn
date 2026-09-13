-- Link prepared memory receipts to already observed host attempts. Launch
-- reservation is checked under the attempt row lock, not consumed by binding.
ALTER TABLE cairn.run_binding
 ADD COLUMN attempt_id uuid REFERENCES cairn.delegation_attempt(attempt_id);
CREATE INDEX run_binding_attempt ON cairn.run_binding(attempt_id)
 WHERE attempt_id IS NOT NULL;
