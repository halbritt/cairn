-- Distinguish terminal wake refusals: no pane identifies the session, only
-- non-idle or focused panes identify it, or several eligible panes match.
-- 'terminal_target_ambiguous' keeps its meaning for multiple eligible panes
-- and remains valid, so rows written by older hosts stay readable.
ALTER TABLE cairn.agent_session_delivery_observation
 DROP CONSTRAINT agent_session_delivery_observation_condition_check;
ALTER TABLE cairn.agent_session_delivery_observation
 ADD CONSTRAINT agent_session_delivery_observation_condition_check CHECK (condition IN (
  'wake_retained','native_admission_retained','automatic_available',
  'channel_not_launched','channel_not_selected','channel_unconfigured',
  'native_transport_unavailable','terminal_host_unavailable','terminal_target_ambiguous',
  'terminal_target_missing','terminal_target_not_idle','terminal_target_focused',
  'owner_turn_required','busy','unknown','none'));
