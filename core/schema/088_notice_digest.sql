-- FYI notice digest custody (CAIRN-465): one native session attempt can hold
-- up to 64 deduplicated routine/review notices as a single digest turn while
-- keeping per-delivery leases, states, late-completion hold protection, and
-- superseded counts.

ALTER TABLE cairn.agent_session_attempt
 DROP CONSTRAINT agent_session_attempt_admission_kind_check,
 ADD COLUMN digest_superseded_count int NOT NULL DEFAULT 0 CHECK (digest_superseded_count >= 0),
 ADD CONSTRAINT agent_session_attempt_admission_kind_check CHECK (
  admission_kind IN ('ordinary', 'organic', 'digest')
 ),
 ADD CONSTRAINT agent_session_attempt_digest_shape CHECK (
  (admission_kind = 'digest' AND batch_root IS NULL AND organic_root IS NULL AND NOT turn_exclusive)
  OR (admission_kind <> 'digest' AND digest_superseded_count = 0)
 );

CREATE TABLE cairn.agent_session_digest_item (
 attempt_id uuid NOT NULL REFERENCES cairn.agent_session_attempt(attempt_id) ON DELETE CASCADE,
 ordinal smallint NOT NULL CHECK (ordinal BETWEEN 1 AND 64),
 delivery_id uuid NOT NULL REFERENCES cairn.agent_delivery(delivery_id) ON DELETE CASCADE,
 lease_id uuid NOT NULL,
 topic_key text NOT NULL DEFAULT '',
 PRIMARY KEY (attempt_id, ordinal),
 UNIQUE (attempt_id, delivery_id)
);

CREATE INDEX agent_session_digest_item_delivery
 ON cairn.agent_session_digest_item(delivery_id);

ALTER TABLE cairn.agent_session_poll
 ADD COLUMN digest_window_seconds int NOT NULL DEFAULT 0 CHECK (digest_window_seconds >= 0 AND digest_window_seconds <= 3600),
 ADD COLUMN digest_flush_now boolean NOT NULL DEFAULT false,
 ADD COLUMN digest_only boolean NOT NULL DEFAULT false;
