-- One lifecycle recall hook invocation, as reported by the hook. A hook invocation
-- may produce several retrieval receipts (lexical and semantic search), so the
-- receipts attach through a link table and the invocation is counted once.
-- Metrics only: no prompt, query, record body, selector response or error text.
-- Every measurement is nullable. NULL means the reporter did not observe it;
-- zero is stored only when the reporter observed zero. Reports must not turn an
-- absent value into zero. selector_calls is the number of selector calls the hook
-- reports it made (NULL: unreported, 0: observed none) and equals the number of
-- recall_selector_call rows. Each call's own model, tokens and cost stay unknown
-- unless the call reported them; cost is the provider's own reported total,
-- never an estimate. The values are testimony from the lifecycle hook under the
-- ordinary profile credential. The store stamps caller and received time.
CREATE TABLE cairn.recall_observation (
 observation_id uuid PRIMARY KEY,
 sequence bigint GENERATED ALWAYS AS IDENTITY,
 caller text NOT NULL DEFAULT current_setting('cairn.caller'),
 repo text NOT NULL CHECK (octet_length(repo) BETWEEN 1 AND 1024),
 harness text NOT NULL CHECK (harness IN ('claude','codex','opencode','hermes')),
 hook_event text NOT NULL CHECK (hook_event IN ('SessionStart','UserPromptSubmit')),
 method text NOT NULL CHECK (octet_length(method) BETWEEN 1 AND 256),
 status text NOT NULL CHECK (status IN ('completed','timeout','error')),
 error_class text CHECK (error_class ~ '^[A-Za-z][A-Za-z0-9_]{0,63}$'),
 elapsed_ms bigint CHECK (elapsed_ms BETWEEN 0 AND 3600000),
 search_ms bigint CHECK (search_ms BETWEEN 0 AND 3600000),
 selector_ms bigint CHECK (selector_ms BETWEEN 0 AND 3600000),
 pulls_ms bigint CHECK (pulls_ms BETWEEN 0 AND 3600000),
 search_calls integer CHECK (search_calls BETWEEN 0 AND 1000),
 pull_calls integer CHECK (pull_calls BETWEEN 0 AND 1000),
 selector_calls integer CHECK (selector_calls BETWEEN 0 AND 8),
 injected_bytes bigint CHECK (injected_bytes BETWEEN 0 AND 1048576),
 observed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 CONSTRAINT recall_error_class CHECK (error_class IS NULL OR status <> 'completed')
);
CREATE INDEX recall_observation_report ON cairn.recall_observation(repo,observed_at,harness);
CREATE TABLE cairn.recall_observation_receipt (
 observation_id uuid NOT NULL REFERENCES cairn.recall_observation(observation_id),
 receipt_id uuid NOT NULL REFERENCES cairn.retrieval_receipt(receipt_id),
 PRIMARY KEY (observation_id,receipt_id)
);
CREATE INDEX recall_observation_receipt_receipt ON cairn.recall_observation_receipt(receipt_id);
CREATE TABLE cairn.recall_selector_call (
 observation_id uuid NOT NULL REFERENCES cairn.recall_observation(observation_id),
 ordinal smallint NOT NULL CHECK (ordinal BETWEEN 1 AND 8),
 stage text NOT NULL CHECK (stage IN ('preview','recall')),
 outcome text NOT NULL CHECK (outcome IN ('completed','timeout','error')),
 elapsed_ms bigint CHECK (elapsed_ms BETWEEN 0 AND 3600000),
 model text CHECK (octet_length(model) BETWEEN 1 AND 256),
 model_source text CHECK (model_source IN ('reported','requested')),
 input_tokens bigint CHECK (input_tokens >= 0),
 output_tokens bigint CHECK (output_tokens >= 0),
 cache_creation_input_tokens bigint CHECK (cache_creation_input_tokens >= 0),
 cache_read_input_tokens bigint CHECK (cache_read_input_tokens >= 0),
 cost_usd numeric(14,8) CHECK (cost_usd >= 0 AND cost_usd < 1000),
 PRIMARY KEY (observation_id,ordinal),
 CONSTRAINT recall_selector_model CHECK ((model IS NULL) = (model_source IS NULL))
);
