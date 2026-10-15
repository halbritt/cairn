-- Experimental disposable schema only, outside core migrations. No seed/authority.
CREATE SCHEMA cairn_debit_experiment;
CREATE TABLE cairn_debit_experiment.ledger (
 id integer PRIMARY KEY CHECK(id=1),
 revision bigint NOT NULL CHECK(revision>=0),
 generation bigint NOT NULL CHECK(generation>=0),
 recovery bigint NOT NULL CHECK(recovery>=0),
 principal text NOT NULL, incarnation text NOT NULL, active boolean NOT NULL,
 currency text NOT NULL CHECK(currency='USD'), units_per_currency bigint NOT NULL CHECK(units_per_currency>0)
);
CREATE TABLE cairn_debit_experiment.balance (
 key text PRIMARY KEY CHECK(length(key) BETWEEN 1 AND 256),
 remaining bigint NOT NULL CHECK(remaining>=0), committed bigint NOT NULL CHECK(committed>=0)
);
CREATE TABLE cairn_debit_experiment.slot (
 key text PRIMARY KEY CHECK(length(key) BETWEEN 1 AND 256),
 attempt text NOT NULL UNIQUE,
 document jsonb NOT NULL CHECK(jsonb_typeof(document)='object' AND octet_length(document::text)<=262144),
 quarantined boolean NOT NULL DEFAULT false
);
CREATE TABLE cairn_debit_experiment.audit (
 revision bigint PRIMARY KEY CHECK(revision>0),
 event jsonb NOT NULL CHECK(jsonb_typeof(event)='object' AND octet_length(event::text)<=65536)
);
-- Limits apply to individual records, never lifetime rows or campaign audit totals.
-- No pruning, provisioning, funding, permissions, TTL or refund procedure exists.
