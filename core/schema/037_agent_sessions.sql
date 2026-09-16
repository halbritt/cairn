-- Session IDs are routing identities on the existing trusted host, not tokens.
CREATE TABLE cairn.agent_session (
 agent_id uuid PRIMARY KEY,
 ordinal bigint GENERATED ALWAYS AS IDENTITY UNIQUE,
 repo text NOT NULL,
 owner text NOT NULL DEFAULT current_setting('cairn.caller'),
 binding text NOT NULL,
 native_session_id text NOT NULL,
 visibility text NOT NULL CHECK(visibility IN ('local','hosted')),
 execution_id uuid NOT NULL,
 database_generation bigint NOT NULL,
 context_revision bigint NOT NULL DEFAULT 1,
 metadata jsonb NOT NULL,
 last_seen timestamptz NOT NULL DEFAULT clock_timestamp(),
 expires_at timestamptz NOT NULL DEFAULT clock_timestamp()+interval '90 seconds',
 stopped boolean NOT NULL DEFAULT false,
 UNIQUE(repo,owner,binding,native_session_id)
);
CREATE INDEX agent_session_presence ON cairn.agent_session(repo,visibility,expires_at);
CREATE INDEX agent_session_project ON cairn.agent_session(repo,(lower(metadata->>'harness')),(lower(metadata->>'project')));
