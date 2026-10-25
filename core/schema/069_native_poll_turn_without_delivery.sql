-- Ordinary native turns select the next eligible delivery at claim time, so
-- their turn identity cannot require a caller-preselected delivery UUID.
ALTER TABLE cairn.agent_session_poll
 DROP CONSTRAINT native_poll_turn_binding,
 DROP CONSTRAINT native_poll_exclusive_turn,
 ADD CONSTRAINT native_poll_turn_binding CHECK
  (requested_delivery_id IS NULL OR native_turn_id<>''),
 ADD CONSTRAINT native_poll_exclusive_turn CHECK
  (turn_exclusive IS NULL OR NOT turn_exclusive OR
   (native_turn_id<>'' AND requested_delivery_id IS NOT NULL));
