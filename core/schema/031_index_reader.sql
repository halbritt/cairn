-- An observer may designate one ordinary expansion reader at index creation.
-- This never changes retrieval_receipt.caller or authorizes receipt inspection.
ALTER TABLE cairn.index_session ADD COLUMN expansion_reader text
 CHECK(expansion_reader IS NULL OR (length(btrim(expansion_reader))>0 AND octet_length(expansion_reader)<=256));
