ALTER TABLE cairn.agent_event DROP CONSTRAINT agent_event_destination_type_check;
ALTER TABLE cairn.agent_event ADD CONSTRAINT agent_event_destination_type_check CHECK(destination_type IN ('agent','topic','pool'));
ALTER TABLE cairn.agent_event ADD COLUMN pool_requirements jsonb;
ALTER TABLE cairn.agent_event ADD CONSTRAINT agent_event_pool_requirements CHECK(
 (destination_type='pool')=(pool_requirements IS NOT NULL) AND
 (destination_type<>'pool' OR (kind='request' AND sensitivity='shareable')));
CREATE TABLE cairn.agent_worker_pool (
 repo text NOT NULL,
 name text NOT NULL,
 enabled boolean NOT NULL,
 max_pending integer NOT NULL CHECK(max_pending BETWEEN 1 AND 10000),
 max_pending_per_publisher integer NOT NULL CHECK(max_pending_per_publisher BETWEEN 1 AND max_pending),
 revision bigint NOT NULL DEFAULT 1,
 PRIMARY KEY(repo,name)
);
CREATE TABLE cairn.agent_worker_slot (
 repo text NOT NULL,
 consumer text NOT NULL DEFAULT current_setting('cairn.caller'),
 spec jsonb NOT NULL,
 supervisor_id uuid NOT NULL UNIQUE,
 database_generation bigint NOT NULL,
 revision bigint NOT NULL DEFAULT 1,
 heartbeat_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 expires_at timestamptz NOT NULL DEFAULT clock_timestamp()+interval '90 seconds',
 health text NOT NULL DEFAULT 'available' CHECK(health IN ('available','paused','unavailable')),
 reason text NOT NULL DEFAULT 'configured for admission; provider capacity not measured',
 health_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 retry_at timestamptz,
 next_launch_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(repo,consumer)
);
ALTER TABLE cairn.agent_wake_attempt ADD COLUMN worker_id uuid;
CREATE TABLE cairn.agent_pool_request (
 event_id uuid PRIMARY KEY REFERENCES cairn.agent_event(event_id),
 delivery_id uuid UNIQUE REFERENCES cairn.agent_delivery(delivery_id),
 assigned_at timestamptz,
 CHECK((delivery_id IS NULL)=(assigned_at IS NULL))
);
CREATE SEQUENCE cairn.agent_pool_dispatch_sequence;
CREATE TABLE cairn.agent_pool_publisher_service (
 repo text NOT NULL,
 pool text NOT NULL,
 publisher text NOT NULL,
 last_dispatch bigint NOT NULL DEFAULT 0,
 PRIMARY KEY(repo,pool,publisher),
 FOREIGN KEY(repo,pool) REFERENCES cairn.agent_worker_pool(repo,name)
);
