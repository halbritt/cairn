-- A historical run's context.txt that predates managed-context registration can be
-- adopted into the same custody table, so the existing deletion inventory and
-- purge workflow cover it. An adopted row records what the operator's adoption
-- observed at that moment (file size, identity and modification time) and no
-- earlier launch, delivery or completion; those keep their own records. A
-- registered row, written before its file exists, has no such observation. Rows
-- remain immutable.
ALTER TABLE cairn.managed_context
 ADD COLUMN custody_origin text NOT NULL DEFAULT 'registered' CHECK (custody_origin IN ('registered','adopted')),
 ADD COLUMN observed_bytes bigint CHECK (observed_bytes >= 0),
 ADD COLUMN observed_file_device text CHECK (observed_file_device ~ '^[0-9]{1,20}$'),
 ADD COLUMN observed_file_inode text CHECK (observed_file_inode ~ '^[0-9]{1,20}$'),
 ADD COLUMN observed_file_modified_at timestamptz,
 ADD CONSTRAINT managed_context_adoption_observation CHECK (
  (custody_origin = 'registered' AND observed_bytes IS NULL AND observed_file_device IS NULL
   AND observed_file_inode IS NULL AND observed_file_modified_at IS NULL)
  OR (custody_origin = 'adopted' AND observed_bytes IS NOT NULL AND observed_file_device IS NOT NULL
   AND observed_file_inode IS NOT NULL AND observed_file_modified_at IS NOT NULL));
