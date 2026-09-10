-- Relevance metadata belongs to the exact version, separately from applicability.
CREATE TABLE cairn.record_entities (
 record_id uuid NOT NULL,
 version integer NOT NULL,
 entities jsonb NOT NULL CHECK(jsonb_typeof(entities)='array' AND jsonb_array_length(entities) BETWEEN 1 AND 16),
 PRIMARY KEY(record_id,version),
 FOREIGN KEY(record_id,version) REFERENCES cairn.record_version(record_id,version) ON DELETE CASCADE
);
