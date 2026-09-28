-- A host's current diagnosis of why a session's ready delivery has or lacks an
-- automatic wake. One row per session, replaced in place: disposable,
-- generation-fenced host state, not history, authority or a delivery guarantee.
-- It is outside recovery export; a restore fences it by generation. The store
-- stamps received_at and reporter; observed_at and the wake fields are host
-- testimony under the ordinary profile credential. 'none' is an ordering
-- tombstone so that a delayed retry cannot resurrect a cleared diagnosis.
CREATE TABLE cairn.agent_session_delivery_observation (
 agent_id uuid PRIMARY KEY REFERENCES cairn.agent_session(agent_id),
 execution_id uuid NOT NULL,
 database_generation bigint NOT NULL,
 reporter text NOT NULL CHECK (octet_length(reporter) BETWEEN 1 AND 256),
 delivery_id uuid,
 condition text NOT NULL CHECK (condition IN (
  'wake_retained','native_admission_retained','automatic_available',
  'channel_not_launched','channel_not_selected','channel_unconfigured',
  'native_transport_unavailable','terminal_host_unavailable','terminal_target_ambiguous',
  'owner_turn_required','busy','unknown','none')),
 scope text NOT NULL CHECK (scope IN ('delivery','session')),
 wake_transport text CHECK (wake_transport IN ('claude-channel','codex-queue','opencode-queue','hermes-queue','terminal')),
 wake_status text CHECK (wake_status IN ('uncertain','submitted','queued','refused')),
 wake_attempted_at timestamptz,
 observed_at timestamptz NOT NULL,
 received_at timestamptz NOT NULL,
 CONSTRAINT observation_delivery CHECK (delivery_id IS NOT NULL OR condition IN ('owner_turn_required','busy','unknown','none')),
 CONSTRAINT observation_wake CHECK (
  (wake_transport IS NULL) = (wake_status IS NULL)
  AND (wake_status IS NOT NULL OR wake_attempted_at IS NULL)
  AND (wake_status IS NULL OR condition='wake_retained'))
);
