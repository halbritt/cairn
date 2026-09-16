ALTER TABLE cairn.agent_wake_attempt
 ADD COLUMN agent_id uuid REFERENCES cairn.agent_session(agent_id),
 ADD COLUMN execution_id uuid,
 ADD CONSTRAINT agent_wake_session_pair CHECK((agent_id IS NULL) = (execution_id IS NULL));
CREATE UNIQUE INDEX agent_wake_session_active ON cairn.agent_wake_attempt(agent_id) WHERE finished_at IS NULL AND agent_id IS NOT NULL;
