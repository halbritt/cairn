ALTER TABLE cairn.agent_wake_attempt
 ADD COLUMN provider_failure jsonb,
 ADD COLUMN provider_failure_at timestamptz,
 ADD CONSTRAINT wake_provider_failure_pair CHECK((provider_failure IS NULL)=(provider_failure_at IS NULL)),
 ADD CONSTRAINT wake_provider_failure_worker CHECK(provider_failure IS NULL OR worker_id IS NOT NULL);
