-- Closure capacity for accepted obligations: one row per reply a request
-- delivery may still receive, and per terminal notice a wait owes its owner.
-- Rows hold identities only, never payloads. A row is reserved, delivered
-- (consumed by the exact reply or notice) or released by an explicit terminal.
CREATE TABLE cairn.agent_closure_reservation (
 reservation_id uuid PRIMARY KEY,
 repo text NOT NULL,
 obligation text NOT NULL CHECK (obligation IN ('request_reply','group_reply','pool_reply','wait_notice')),
 request_event_id uuid REFERENCES cairn.agent_event(event_id),
 request_delivery_id uuid UNIQUE REFERENCES cairn.agent_delivery(delivery_id),
 wait_uuid uuid UNIQUE REFERENCES cairn.agent_wait(wait_uuid),
 expected_sender text,
 recipient text NOT NULL,
 correlation_id uuid,
 state text NOT NULL DEFAULT 'reserved' CHECK (state IN ('reserved','delivered','released')),
 revision integer NOT NULL DEFAULT 1 CHECK (revision > 0),
 consumed_event_id uuid UNIQUE REFERENCES cairn.agent_event(event_id),
 consumed_delivery_id uuid UNIQUE REFERENCES cairn.agent_delivery(delivery_id),
 release_reason text NOT NULL DEFAULT '' CHECK (release_reason IN ('','group_closed','work_cancelled','request_expired','stopped_expired','operator_released','wait_cancelled','wait_restricted','restore_fenced','publisher_closed','operator_closed')),
 released_by text NOT NULL DEFAULT '',
 legacy boolean NOT NULL DEFAULT false,
 restore_generation bigint NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 transitioned_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 CHECK ((obligation = 'wait_notice') = (wait_uuid IS NOT NULL)),
 CHECK ((obligation = 'wait_notice') = (request_event_id IS NULL)),
 CHECK (obligation NOT IN ('request_reply','group_reply') OR (request_delivery_id IS NOT NULL AND expected_sender IS NOT NULL)),
 CHECK ((request_delivery_id IS NULL) = (expected_sender IS NULL)),
 CHECK (obligation <> 'wait_notice' OR request_delivery_id IS NULL),
 CHECK ((state = 'delivered') = (consumed_event_id IS NOT NULL AND consumed_delivery_id IS NOT NULL)),
 CHECK (state = 'delivered' OR (consumed_event_id IS NULL AND consumed_delivery_id IS NULL)),
 CHECK ((state = 'released') = (release_reason <> ''))
);
CREATE UNIQUE INDEX agent_closure_reservation_pool ON cairn.agent_closure_reservation(request_event_id) WHERE obligation = 'pool_reply';
CREATE INDEX agent_closure_reservation_reserved ON cairn.agent_closure_reservation(repo, recipient) WHERE state = 'reserved';
CREATE INDEX agent_closure_reservation_delivered ON cairn.agent_closure_reservation(repo, recipient, consumed_delivery_id) WHERE state = 'delivered';
CREATE INDEX agent_closure_reservation_request ON cairn.agent_closure_reservation(request_event_id, expected_sender) WHERE state = 'reserved';
CREATE INDEX agent_closure_reservation_list ON cairn.agent_closure_reservation(repo, created_at, reservation_id);

-- Completion of bounded reconciliation for a collection and restore
-- generation: written only by the batch that found every candidate class
-- exhausted under the collection insertion lock. It is evidence for an
-- enforcement gate, not the whole gate: writer compatibility is separate.
CREATE TABLE cairn.agent_closure_reconciliation (
 repo text NOT NULL,
 restore_generation bigint NOT NULL,
 reconciled_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 reconciled_by text NOT NULL DEFAULT current_setting('cairn.caller'),
 legacy_rows bigint NOT NULL CHECK (legacy_rows >= 0),
 legacy_unreserved bigint NOT NULL CHECK (legacy_unreserved >= 0),
 PRIMARY KEY (repo, restore_generation)
);
