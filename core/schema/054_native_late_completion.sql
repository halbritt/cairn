-- A native attempt's hold outlives its 90-second lease. Completion under that
-- exact unreleased hold, execution and generation is accepted late and says so;
-- it is never presented as completion within the lease.
ALTER TABLE cairn.agent_delivery
 ADD COLUMN completed_after_lease boolean NOT NULL DEFAULT false,
 ADD CONSTRAINT late_completion_is_terminal CHECK (NOT completed_after_lease OR state IN ('handled','ignored','failed'));
-- An operator may release the hold of a host that never returned. Offline
-- presence does not prove that host stopped; the release records who accepted
-- that uncertainty and why.
ALTER TABLE cairn.agent_session_attempt
 ADD COLUMN released_by text CHECK (released_by IS NULL OR octet_length(released_by) BETWEEN 1 AND 256),
 ADD COLUMN release_reason text CHECK (release_reason IS NULL OR octet_length(release_reason) BETWEEN 1 AND 16384),
 ADD CONSTRAINT operator_release_recorded CHECK ((reason='operator_released')=(released_by IS NOT NULL AND release_reason IS NOT NULL));
-- A released delivery records the operator decision like other stop controls.
ALTER TABLE cairn.agent_delivery DROP CONSTRAINT agent_delivery_control;
ALTER TABLE cairn.agent_delivery ADD CONSTRAINT agent_delivery_control CHECK(
 (control_at IS NULL AND control_by IS NULL AND control_reason IS NULL) OR
 (control_at IS NOT NULL AND control_by IS NOT NULL AND control_reason IS NOT NULL AND state='failed' AND code IN ('admission_expired','task_deadline','operator_cancelled','operator_released')));
