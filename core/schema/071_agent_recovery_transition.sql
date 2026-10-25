-- Record a narrow, Store-owned trace of selected Cairn-mediated recovery actions.
-- The revision counts rows in this ledger, not every delivery lifecycle change.
ALTER TABLE cairn.agent_delivery
 ADD COLUMN recovery_revision bigint NOT NULL DEFAULT 0 CHECK (recovery_revision >= 0);

CREATE TABLE cairn.agent_recovery_transition (
 delivery_id uuid NOT NULL REFERENCES cairn.agent_delivery(delivery_id),
 revision bigint NOT NULL CHECK (revision > 0),
 event_id uuid NOT NULL REFERENCES cairn.agent_event(event_id),
 related_event_id uuid REFERENCES cairn.agent_event(event_id),
 attempt_id uuid,
 request_id uuid NOT NULL,
 actor text NOT NULL,
 action_kind text NOT NULL CHECK (action_kind IN ('native_hold_release','event_reissue')),
 observed_outcome text NOT NULL CHECK (observed_outcome IN ('hold_released','reissue_created')),
 state_before text NOT NULL CHECK (state_before IN ('pending','leased','handled','ignored','failed')),
 state_after text NOT NULL CHECK (state_after IN ('pending','leased','handled','ignored','failed')),
 observed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY (delivery_id, revision),
 UNIQUE (delivery_id, action_kind),
 CHECK ((action_kind='native_hold_release' AND observed_outcome='hold_released' AND related_event_id IS NULL AND attempt_id IS NOT NULL) OR
        (action_kind='event_reissue' AND observed_outcome='reissue_created' AND related_event_id IS NOT NULL))
);
CREATE INDEX agent_recovery_transition_event ON cairn.agent_recovery_transition(event_id, delivery_id, revision);
CREATE TRIGGER immutable_agent_recovery_transition BEFORE UPDATE OR DELETE ON cairn.agent_recovery_transition
 FOR EACH ROW EXECUTE FUNCTION cairn.immutable_audit();
CREATE TRIGGER immutable_agent_recovery_transition_truncate BEFORE TRUNCATE ON cairn.agent_recovery_transition
 FOR EACH STATEMENT EXECUTE FUNCTION cairn.immutable_audit();
