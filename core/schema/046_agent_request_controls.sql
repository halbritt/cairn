ALTER TABLE cairn.agent_event ADD COLUMN admission_expires_at timestamptz;
ALTER TABLE cairn.agent_event ADD COLUMN task_deadline timestamptz;
ALTER TABLE cairn.agent_event ADD CONSTRAINT agent_request_timing CHECK(kind='request' OR (admission_expires_at IS NULL AND task_deadline IS NULL));

-- A terminal decision can coexist with a held, not-yet-stopped execution.
ALTER TABLE cairn.agent_delivery ADD COLUMN control_at timestamptz;
ALTER TABLE cairn.agent_delivery ADD COLUMN control_by text;
ALTER TABLE cairn.agent_delivery ADD COLUMN control_reason text;
ALTER TABLE cairn.agent_delivery ADD CONSTRAINT agent_delivery_control CHECK(
 (control_at IS NULL AND control_by IS NULL AND control_reason IS NULL) OR
 (control_at IS NOT NULL AND control_by IS NOT NULL AND control_reason IS NOT NULL AND state='failed' AND code IN ('admission_expired','task_deadline','operator_cancelled')));

ALTER TABLE cairn.agent_pool_request ADD COLUMN closed_at timestamptz;
ALTER TABLE cairn.agent_pool_request ADD COLUMN closed_code text;
ALTER TABLE cairn.agent_pool_request ADD COLUMN closed_by text;
ALTER TABLE cairn.agent_pool_request ADD COLUMN closed_reason text;
ALTER TABLE cairn.agent_pool_request ADD CONSTRAINT agent_pool_request_closed CHECK(
 (closed_at IS NULL AND closed_code IS NULL AND closed_by IS NULL AND closed_reason IS NULL) OR
 (closed_at IS NOT NULL AND closed_code IN ('admission_expired','task_deadline','operator_cancelled') AND closed_by IS NOT NULL AND closed_reason IS NOT NULL AND delivery_id IS NULL));
