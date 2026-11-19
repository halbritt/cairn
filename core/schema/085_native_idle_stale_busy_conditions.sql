-- Distinguish native-idle sessions stranded behind stale busy metadata and
-- native clients whose required Cairn lifecycle hooks are untrusted, disabled,
-- or missing from genuinely active busy turns (CAIRN-363).
ALTER TABLE cairn.agent_session_delivery_observation
 DROP CONSTRAINT agent_session_delivery_observation_condition_check,
 DROP CONSTRAINT observation_delivery;
ALTER TABLE cairn.agent_session_delivery_observation
 ADD CONSTRAINT agent_session_delivery_observation_condition_check CHECK (condition IN (
  'wake_retained','native_admission_retained','automatic_available',
  'channel_not_launched','channel_not_selected','channel_unconfigured',
  'native_transport_unavailable','native_idle_stale_busy','lifecycle_hooks_untrusted',
  'terminal_host_unavailable','terminal_target_ambiguous',
  'terminal_target_missing','terminal_target_not_idle','terminal_target_focused',
  'owner_turn_required','busy','unknown','none')),
 ADD CONSTRAINT observation_delivery CHECK (
  delivery_id IS NOT NULL OR condition IN (
   'owner_turn_required','native_idle_stale_busy','lifecycle_hooks_untrusted','busy','unknown','none'));
