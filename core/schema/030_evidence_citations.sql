-- Null metadata preserves the fact that older links did not freeze a citation.
-- Do not infer an earlier cited digest from the object's current contents.
ALTER TABLE cairn.evidence_ref
 ADD COLUMN cited_digest bytea CHECK (octet_length(cited_digest) = 32),
 ADD COLUMN cited_spans jsonb,
 ADD CONSTRAINT evidence_citation_spans CHECK (
   cited_spans IS NULL OR (cited_digest IS NOT NULL AND
     jsonb_typeof(cited_spans) = 'array' AND jsonb_array_length(cited_spans) BETWEEN 1 AND 32)
 );
