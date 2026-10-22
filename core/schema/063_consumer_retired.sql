-- An operator may fail unresolved deliveries to an explicitly stopped session
-- after reviewing them. The decision is recorded like other stop controls, with
-- its own code, so publishers can tell it apart from cancellation or expiry.
ALTER TABLE cairn.agent_delivery DROP CONSTRAINT agent_delivery_control;
ALTER TABLE cairn.agent_delivery ADD CONSTRAINT agent_delivery_control CHECK(
 (control_at IS NULL AND control_by IS NULL AND control_reason IS NULL) OR
 (control_at IS NOT NULL AND control_by IS NOT NULL AND control_reason IS NOT NULL AND state='failed' AND code IN ('admission_expired','task_deadline','operator_cancelled','operator_released','consumer_retired')));
