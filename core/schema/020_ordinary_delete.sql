ALTER TABLE cairn.mutation_request
 ADD COLUMN ordinary_deleted boolean NOT NULL DEFAULT false,
 DROP CONSTRAINT mutation_request_check,
 ADD CHECK (response IS NOT NULL OR payload_deleted_by IS NOT NULL OR ordinary_deleted),
 ADD CHECK (NOT ordinary_deleted OR response IS NULL);
