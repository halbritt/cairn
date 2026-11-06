ALTER TABLE cairn.agent_event_admission_refusal DROP CONSTRAINT agent_event_admission_refusal_dimension_check;
ALTER TABLE cairn.agent_event_admission_refusal ADD CONSTRAINT agent_event_admission_refusal_dimension_check CHECK (dimension IN ('topic_recipients','recipient_outstanding','producer_recipient_outstanding','collection_outstanding','producer_publications','producer_deliveries','owner_publications','owner_deliveries','collection_publications','collection_deliveries','closure_collection','closure_recipient'));

-- Integer nanounits retain fractional refill without floating-point drift.
CREATE TABLE cairn.agent_event_rate_bucket (
 repo text NOT NULL,
 identity_kind text NOT NULL CHECK (identity_kind IN ('producer','owner','collection')),
 identity text NOT NULL,
 unit text NOT NULL CHECK (unit IN ('publication','delivery')),
 tokens_nano bigint NOT NULL CHECK (tokens_nano BETWEEN 0 AND 1000000000000000000),
 refill_at timestamptz NOT NULL,
 policy_revision bigint NOT NULL CHECK (policy_revision>0),
 restore_generation bigint NOT NULL CHECK (restore_generation>=0),
 PRIMARY KEY(repo,identity_kind,identity,unit),
 CHECK ((identity_kind='collection')=(identity=''))
);
