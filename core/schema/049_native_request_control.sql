-- Per-request native cancellation. Operator intent is durable on the session
-- attempt, the fence applies immediately, and the hold survives until a host
-- report positively confirms the exact native turn stopped, every captured
-- tool process is terminal, and either the capture stream covered the turn
-- or a schema-valid terminal scan observed the thread clear. Tool ownership
-- is captured from native notifications because historical item reads omit
-- running tools.

ALTER TABLE cairn.agent_session_attempt
 ADD COLUMN repo text NOT NULL DEFAULT '',
 ADD COLUMN cancel_requested_at timestamptz,
 ADD COLUMN cancel_confirmed_at timestamptz,
 ADD COLUMN cancel_by text,
 ADD COLUMN cancel_reason text,
 ADD COLUMN turn_stop_state text NOT NULL DEFAULT '' CHECK (turn_stop_state IN ('','interrupted','ended','ambiguous')),
 ADD COLUMN capture_state text NOT NULL DEFAULT '' CHECK (capture_state IN ('','attached','lost','complete')),
 ADD COLUMN terminal_scan text NOT NULL DEFAULT '' CHECK (terminal_scan IN ('','clear','unknown_remaining')),
 ADD COLUMN turn_exclusive boolean NOT NULL DEFAULT false,
 ADD COLUMN capture_gap boolean NOT NULL DEFAULT false,
 ADD CONSTRAINT agent_session_attempt_cancel CHECK(
  (cancel_requested_at IS NULL AND cancel_confirmed_at IS NULL AND cancel_by IS NULL AND cancel_reason IS NULL) OR
  (cancel_requested_at IS NOT NULL AND cancel_by IS NOT NULL AND cancel_reason IS NOT NULL
   AND (cancel_confirmed_at IS NULL OR cancel_confirmed_at>=cancel_requested_at)));
-- Exclusivity implies a pinned turn; a pinned turn (048 bindings and
-- shared observations) remains valid without attesting exclusivity.
ALTER TABLE cairn.agent_session_attempt
 ADD CONSTRAINT agent_session_turn_exclusive_binding CHECK(NOT turn_exclusive OR native_turn_id<>'');

-- An exclusively owned native turn belongs to one request in the collection:
-- a joined or shared prompt (for example a Claude channel wake attaching to
-- an active owner prompt) must never carry per-request ownership twice.
CREATE UNIQUE INDEX agent_session_one_exclusive_turn ON cairn.agent_session_attempt(repo,native_turn_id)
 WHERE finished_at IS NULL AND turn_exclusive;

CREATE TABLE cairn.agent_session_tool(
 tool_id uuid PRIMARY KEY,
 attempt_id uuid NOT NULL REFERENCES cairn.agent_session_attempt(attempt_id),
 delivery_id uuid NOT NULL REFERENCES cairn.agent_delivery(delivery_id),
 agent_id uuid NOT NULL,
 native_turn_id text NOT NULL DEFAULT '' CHECK (octet_length(native_turn_id)<=256),
 item_id text NOT NULL CHECK (octet_length(item_id)>0 AND octet_length(item_id)<=256),
 process_id text NOT NULL CHECK (octet_length(process_id)>0 AND octet_length(process_id)<=256),
 command text NOT NULL DEFAULT '' CHECK (octet_length(command)<=1024),
 captured_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 stop_state text NOT NULL DEFAULT 'captured' CHECK (stop_state IN ('captured','stop_issued','terminated','unavailable')),
 stop_at timestamptz,
-- stop_at records when a terminal state was proven; intermediate progress
-- (captured, stop_issued) keeps it unset.
 CONSTRAINT agent_session_tool_stop_timing CHECK((stop_state IN ('terminated','unavailable')) = (stop_at IS NOT NULL)));
-- 'unavailable' records a handle positively verified absent by a terminal
-- scan; a transport-level stop failure stays 'stop_issued' and holds cleanup.
CREATE UNIQUE INDEX agent_session_tool_item ON cairn.agent_session_tool(attempt_id,item_id);
CREATE INDEX agent_session_tool_open ON cairn.agent_session_tool(attempt_id) WHERE stop_state NOT IN ('terminated','unavailable');
