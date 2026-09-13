-- Earlier relation writes did not inherit exclusions from retained dependents
-- of forgotten records. Rebuild that derived restriction from canonical links.
WITH RECURSIVE dependencies(record_id,version,deletion_id,source_id) AS (
 SELECT v.record_id,v.version,d.deletion_id,d.record_id
 FROM cairn.deletion_request d JOIN cairn.record_version v USING(record_id)
 UNION
 SELECT r.from_id,r.from_version,d.deletion_id,d.source_id
 FROM cairn.record_relation r JOIN dependencies d
 ON r.to_id=d.record_id AND r.to_version=d.version
), repaired AS (
 INSERT INTO cairn.deletion_dependency(record_id,version,deletion_id)
 SELECT record_id,version,deletion_id FROM dependencies WHERE record_id<>source_id
 ON CONFLICT DO NOTHING
 RETURNING record_id
)
UPDATE cairn.memory_record SET use_generation=use_generation+1
WHERE record_id IN (SELECT record_id FROM repaired);
