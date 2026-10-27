-- Extend the recovery ledger from its two endpoint actions to every
-- Store-mediated stop of a delivery: operator cancellation (manual, unclaimed
-- or confirmed native), retirement of a stopped session's inbox, and the
-- request-control sweep that fails admission-expired or deadline-passed work.
-- Sweeps run inside polling paths that carry no request id, so request_id is
-- required only for the request-driven actions. The ledger still records
-- Store-mediated actions only; manual or off-band work stays outside it.
ALTER TABLE cairn.agent_recovery_transition
 ALTER COLUMN request_id DROP NOT NULL,
 DROP CONSTRAINT agent_recovery_transition_action_kind_check,
 DROP CONSTRAINT agent_recovery_transition_observed_outcome_check,
 DROP CONSTRAINT agent_recovery_transition_check,
 ADD CONSTRAINT agent_recovery_transition_action_kind_check CHECK (action_kind IN ('native_hold_release','event_reissue','work_cancel','stopped_inbox_expiry','request_control_sweep')),
 ADD CONSTRAINT agent_recovery_transition_observed_outcome_check CHECK (observed_outcome IN ('hold_released','reissue_created','cancelled','consumer_retired','admission_expired','task_deadline')),
 ADD CONSTRAINT agent_recovery_transition_check CHECK (
  (action_kind='native_hold_release' AND observed_outcome='hold_released' AND related_event_id IS NULL AND attempt_id IS NOT NULL AND request_id IS NOT NULL) OR
  (action_kind='event_reissue' AND observed_outcome='reissue_created' AND related_event_id IS NOT NULL AND request_id IS NOT NULL) OR
  (action_kind='work_cancel' AND observed_outcome='cancelled' AND related_event_id IS NULL AND request_id IS NOT NULL AND state_after='failed') OR
  (action_kind='stopped_inbox_expiry' AND observed_outcome='consumer_retired' AND related_event_id IS NULL AND attempt_id IS NULL AND request_id IS NOT NULL AND state_after='failed') OR
  (action_kind='request_control_sweep' AND observed_outcome IN ('admission_expired','task_deadline') AND related_event_id IS NULL AND attempt_id IS NULL AND state_after='failed'));
