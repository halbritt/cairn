-- Explicit operator-authorized successor access to selected predecessor events.
-- Grants never transfer inbox ownership, leases, or respondent identity.
CREATE TABLE cairn.agent_successor_grant (
 grant_id uuid PRIMARY KEY,
 request_id uuid NOT NULL UNIQUE,
 repo text NOT NULL CHECK (octet_length(btrim(repo)) BETWEEN 1 AND 256 AND repo <> '*'),
 predecessor_agent_id uuid NOT NULL REFERENCES cairn.agent_session(agent_id),
 predecessor_execution_id uuid NOT NULL,
 successor_agent_id uuid NOT NULL REFERENCES cairn.agent_session(agent_id),
 successor_execution_id uuid NOT NULL,
 database_generation bigint NOT NULL,
 event_ids uuid[] NOT NULL CHECK (cardinality(event_ids) BETWEEN 1 AND 64),
 granted_by text NOT NULL DEFAULT current_setting('cairn.caller') CHECK (octet_length(btrim(granted_by)) BETWEEN 1 AND 256),
 granted_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 expires_at timestamptz NOT NULL,
 reason text NOT NULL CHECK (octet_length(btrim(reason)) BETWEEN 1 AND 1024),
 revoked_at timestamptz,
 revoked_by text,
 revoke_reason text,
 CONSTRAINT agent_successor_distinct_sessions CHECK (predecessor_agent_id <> successor_agent_id),
 CONSTRAINT agent_successor_expiry_order CHECK (expires_at > granted_at),
 CONSTRAINT agent_successor_revocation CHECK (
  (revoked_at IS NULL AND revoked_by IS NULL AND revoke_reason IS NULL) OR
  (revoked_at IS NOT NULL AND revoked_by IS NOT NULL AND octet_length(btrim(revoked_by)) BETWEEN 1 AND 256
   AND revoke_reason IS NOT NULL AND octet_length(btrim(revoke_reason)) BETWEEN 1 AND 1024)
 )
);
CREATE INDEX agent_successor_grant_lookup
 ON cairn.agent_successor_grant(successor_agent_id, successor_execution_id, database_generation, repo)
 WHERE revoked_at IS NULL;
