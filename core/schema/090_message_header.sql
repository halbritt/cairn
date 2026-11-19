-- Structured message header (CAIRN-470): optional single-line summary ("what
-- changed", 1..280 UTF-8 bytes) stored on cairn.agent_event alongside action
-- and expect_next_by / expect_by deadlines so message bodies need not be
-- compressed to fit delivery budgets.

ALTER TABLE cairn.agent_event
 ADD COLUMN summary text,
 ADD CONSTRAINT agent_event_summary_valid CHECK (
  summary IS NULL OR (
   octet_length(btrim(summary)) > 0
   AND octet_length(summary) <= 280
   AND summary !~ '[\x00-\x1f\x7f\u0080-\u009f\u061c\u200e\u200f\u2028-\u202e\u2066-\u2069]'
  )
 );
