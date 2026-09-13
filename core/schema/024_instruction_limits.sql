ALTER TABLE cairn.record_authority ADD COLUMN category text NOT NULL DEFAULT 'workflow'
 CHECK(category IN ('security','workflow','preference'));
ALTER TABLE cairn.policy_revision DROP CONSTRAINT policy_revision_engine_check;
ALTER TABLE cairn.policy_revision ADD CHECK(engine IN ('local-loop/2','local-loop/3'));
