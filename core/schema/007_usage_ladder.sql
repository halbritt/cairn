ALTER TABLE cairn.usage_observation DROP CONSTRAINT usage_observation_signal_check;
ALTER TABLE cairn.usage_observation ADD CONSTRAINT usage_observation_signal_check CHECK(signal IN ('cited','expanded','behaviorally_implicated'));
ALTER TABLE cairn.usage_observation ADD COLUMN witness text NOT NULL DEFAULT 'testimony' CHECK(witness IN ('testimony','instrumented','inferred'));
ALTER TABLE cairn.usage_observation ADD COLUMN method text NOT NULL DEFAULT '';
ALTER TABLE cairn.usage_observation ADD CONSTRAINT inferred_method CHECK(signal<>'behaviorally_implicated' OR (witness='inferred' AND length(method)>0));
CREATE TABLE cairn.usage_coverage (
 observation_id uuid PRIMARY KEY,
 sequence bigint GENERATED ALWAYS AS IDENTITY,
 receipt_id uuid NOT NULL REFERENCES cairn.retrieval_receipt(receipt_id),
 coverage text NOT NULL CHECK(coverage IN ('unknown','partial','complete')),
 method text NOT NULL,
 observer text NOT NULL DEFAULT current_setting('cairn.caller'),
 observed_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX usage_coverage_receipt ON cairn.usage_coverage(receipt_id,sequence DESC);
