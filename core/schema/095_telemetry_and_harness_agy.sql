-- Telemetry attribution, agy harness support, delivery disposition counters,
-- reasoning token separation, and session longevity/window-growth support (CAIRN-816).

ALTER TABLE cairn.recall_observation
 DROP CONSTRAINT recall_observation_harness_check,
 ADD CONSTRAINT recall_observation_harness_check
  CHECK (harness IN ('claude','codex','opencode','hermes','agy')),
 ADD COLUMN attempt_id uuid,
 ADD COLUMN delivery_id uuid,
 ADD COLUMN native_session_id text
  CHECK (native_session_id IS NULL OR (octet_length(btrim(native_session_id)) BETWEEN 1 AND 256 AND native_session_id !~ '[\x00-\x1f\x7f]')),
 ADD COLUMN client_generated_at timestamptz,
 ADD COLUMN injected_unit text
  CHECK (injected_unit IS NULL OR injected_unit IN ('utf8_bytes','chars','tokens_cl100k','tokens_o200k','tokens_reported')),
 ADD COLUMN injected_tokenizer text
  CHECK (injected_tokenizer IS NULL OR (octet_length(btrim(injected_tokenizer)) BETWEEN 1 AND 64 AND injected_tokenizer !~ '[\x00-\x1f\x7f]')),
 ADD COLUMN dedup_skipped_count integer
  CHECK (dedup_skipped_count IS NULL OR dedup_skipped_count >= 0),
 ADD COLUMN expansion_reason text
  CHECK (expansion_reason IS NULL OR (octet_length(btrim(expansion_reason)) BETWEEN 1 AND 128 AND expansion_reason !~ '[\x00-\x1f\x7f]')),
 ADD COLUMN native_context_tokens bigint
  CHECK (native_context_tokens IS NULL OR native_context_tokens >= 0),
 ADD COLUMN native_message_count integer
  CHECK (native_message_count IS NULL OR native_message_count >= 0);

CREATE INDEX recall_observation_native_session_idx
 ON cairn.recall_observation(repo, native_session_id, observed_at)
 WHERE native_session_id IS NOT NULL;

CREATE INDEX recall_observation_attempt_idx
 ON cairn.recall_observation(attempt_id)
 WHERE attempt_id IS NOT NULL;

ALTER TABLE cairn.recall_selector_call
 ADD COLUMN reasoning_tokens bigint
  CHECK (reasoning_tokens IS NULL OR reasoning_tokens >= 0),
 ADD COLUMN partial_usage boolean;

ALTER TABLE cairn.capture_observation
 DROP CONSTRAINT capture_observation_harness_check,
 ADD CONSTRAINT capture_observation_harness_check
  CHECK (harness IN ('claude','codex','opencode','hermes','agy')),
 DROP CONSTRAINT capture_selector_status,
 ADD COLUMN attempt_id uuid,
 ADD COLUMN delivery_id uuid,
 ADD COLUMN native_session_id text
  CHECK (native_session_id IS NULL OR (octet_length(btrim(native_session_id)) BETWEEN 1 AND 256 AND native_session_id !~ '[\x00-\x1f\x7f]')),
 ADD COLUMN client_generated_at timestamptz,
 ADD COLUMN reasoning_tokens bigint
  CHECK (reasoning_tokens IS NULL OR reasoning_tokens >= 0),
 ADD COLUMN partial_usage boolean,
 ADD COLUMN dialogue_messages integer
  CHECK (dialogue_messages IS NULL OR dialogue_messages >= 0),
 ADD COLUMN dialogue_bytes bigint
  CHECK (dialogue_bytes IS NULL OR dialogue_bytes >= 0),
 ADD CONSTRAINT capture_selector_status CHECK (selector_outcome IS NOT NULL OR
  (selector_duration_ms IS NULL AND requested_model IS NULL AND reported_model IS NULL
   AND input_tokens IS NULL AND output_tokens IS NULL AND cache_creation_input_tokens IS NULL
   AND cache_read_input_tokens IS NULL AND reasoning_tokens IS NULL AND partial_usage IS NULL AND cost_usd IS NULL));

CREATE INDEX capture_observation_native_session_idx
 ON cairn.capture_observation(repo, native_session_id, sequence)
 WHERE native_session_id IS NOT NULL;

ALTER TABLE cairn.agent_session
 ADD COLUMN first_compacted_at timestamptz;

CREATE TABLE cairn.delivery_observation (
 observation_id uuid PRIMARY KEY,
 sequence bigint GENERATED ALWAYS AS IDENTITY,
 caller text NOT NULL DEFAULT current_setting('cairn.caller'),
 repo text NOT NULL CHECK (octet_length(repo) BETWEEN 1 AND 1024),
 harness text NOT NULL CHECK (harness IN ('claude','codex','opencode','hermes','agy')),
 hook_event text NOT NULL CHECK (octet_length(btrim(hook_event)) BETWEEN 1 AND 64 AND hook_event !~ '[\x00-\x1f\x7f]'),
 attempt_id uuid,
 delivery_id uuid,
 native_session_id text
  CHECK (native_session_id IS NULL OR (octet_length(btrim(native_session_id)) BETWEEN 1 AND 256 AND native_session_id !~ '[\x00-\x1f\x7f]')),
 client_generated_at timestamptz,
 disposition text NOT NULL CHECK (disposition IN ('full','compact','digest','auto_settled','superseded')),
 superseded_count integer NOT NULL DEFAULT 0 CHECK (superseded_count >= 0),
 injected_bytes bigint CHECK (injected_bytes BETWEEN 0 AND 1048576),
 injected_unit text CHECK (injected_unit IS NULL OR injected_unit IN ('utf8_bytes','chars','tokens_cl100k','tokens_o200k','tokens_reported')),
 injected_tokenizer text CHECK (injected_tokenizer IS NULL OR (octet_length(btrim(injected_tokenizer)) BETWEEN 1 AND 64 AND injected_tokenizer !~ '[\x00-\x1f\x7f]')),
 expansion_reason text CHECK (expansion_reason IS NULL OR (octet_length(btrim(expansion_reason)) BETWEEN 1 AND 128 AND expansion_reason !~ '[\x00-\x1f\x7f]')),
 observed_at timestamptz NOT NULL DEFAULT clock_timestamp()
);

CREATE INDEX delivery_observation_report_idx
 ON cairn.delivery_observation(repo, observed_at, harness);

CREATE INDEX delivery_observation_session_idx
 ON cairn.delivery_observation(repo, native_session_id, observed_at)
 WHERE native_session_id IS NOT NULL;

CREATE INDEX delivery_observation_attempt_idx
 ON cairn.delivery_observation(attempt_id)
 WHERE attempt_id IS NOT NULL;
