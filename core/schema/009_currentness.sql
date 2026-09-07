CREATE TABLE cairn.record_applicability (
 record_id uuid NOT NULL,
 version integer NOT NULL,
 pins jsonb NOT NULL,
 PRIMARY KEY(record_id,version),
 FOREIGN KEY(record_id,version) REFERENCES cairn.record_version(record_id,version)
);
