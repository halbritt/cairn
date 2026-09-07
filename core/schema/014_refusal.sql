CREATE TABLE cairn.refusal (
 refusal_id uuid PRIMARY KEY,
 caller text NOT NULL DEFAULT current_setting('cairn.caller'),
 operation text NOT NULL,
 request_id uuid NOT NULL,
 request_digest bytea NOT NULL,
 code text NOT NULL,
 detail jsonb NOT NULL,
 observed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(caller,operation,request_id,request_digest,code)
);
