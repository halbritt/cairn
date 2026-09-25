-- A native claim retry must use the same exclusivity attestation even when
-- the first poll found no delivery. Historical empty polls cannot recover the
-- caller's attestation, so leave them unknown and refuse their retries.
ALTER TABLE cairn.agent_session_poll
 ADD COLUMN turn_exclusive boolean,
 ADD CONSTRAINT native_poll_exclusive_turn CHECK
  (turn_exclusive IS NULL OR NOT turn_exclusive OR native_turn_id<>'');

UPDATE cairn.agent_session_poll AS poll
SET turn_exclusive = attempt.turn_exclusive
FROM cairn.agent_session_attempt AS attempt
WHERE poll.attempt_id = attempt.attempt_id;
