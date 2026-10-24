-- Hook testimony about one capture invocation. This is independent of the
-- latest capture marker and contains no dialogue, selected note or model output.
CREATE TABLE cairn.capture_observation (
 observation_id uuid PRIMARY KEY,
 sequence bigint GENERATED ALWAYS AS IDENTITY,
 caller text NOT NULL DEFAULT current_setting('cairn.caller'),
 repo text NOT NULL CHECK (octet_length(repo) BETWEEN 1 AND 1024),
 harness text NOT NULL CHECK (harness IN ('claude','codex','opencode','hermes')),
 hook_event text NOT NULL CHECK (hook_event IN ('Stop','PreCompact','SessionEnd')),
 method text NOT NULL CHECK (method ~ '^[A-Za-z0-9][A-Za-z0-9._/-]{0,255}$'),
 session_sha256 text NOT NULL CHECK (session_sha256 ~ '^[0-9a-f]{64}$'),
 snapshot_sha256 text CHECK (snapshot_sha256 ~ '^[0-9a-f]{64}$'),
 outcome text NOT NULL CHECK (outcome IN ('empty','unchanged','courtesy','too_little_dialogue','saved','nothing_selected','timeout','error','write_error','state_commit_error')),
 selector_outcome text CHECK (selector_outcome IN ('completed','timeout','error')),
 requested_model text CHECK (octet_length(requested_model) BETWEEN 1 AND 256),
 reported_model text CHECK (octet_length(reported_model) BETWEEN 1 AND 256),
 duration_ms bigint CHECK (duration_ms BETWEEN 0 AND 3600000),
 selector_duration_ms bigint CHECK (selector_duration_ms BETWEEN 0 AND 3600000),
 input_tokens bigint CHECK (input_tokens BETWEEN 0 AND 1099511627776),
 output_tokens bigint CHECK (output_tokens BETWEEN 0 AND 1099511627776),
 cache_creation_input_tokens bigint CHECK (cache_creation_input_tokens BETWEEN 0 AND 1099511627776),
 cache_read_input_tokens bigint CHECK (cache_read_input_tokens BETWEEN 0 AND 1099511627776),
 cost_usd numeric(14,8) CHECK (cost_usd >= 0 AND cost_usd < 1000),
 observed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 CONSTRAINT capture_selector_status CHECK (selector_outcome IS NOT NULL OR
  (selector_duration_ms IS NULL AND requested_model IS NULL AND reported_model IS NULL
   AND input_tokens IS NULL AND output_tokens IS NULL AND cache_creation_input_tokens IS NULL
   AND cache_read_input_tokens IS NULL AND cost_usd IS NULL))
);
CREATE INDEX capture_observation_report ON cairn.capture_observation(repo,observed_at,sequence);
CREATE INDEX capture_observation_session ON cairn.capture_observation(repo,session_sha256,sequence);
