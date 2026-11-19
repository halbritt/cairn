-- Notice intent fields (CAIRN-464): publisher-supplied action, sender-scoped
-- supersedes topic key, and optional expect_next_by deadline on notice events;
-- supersede provenance on deliveries; and opt-in host auto-settle flag on
-- native session polls.

ALTER TABLE cairn.agent_event
 ADD COLUMN notice_action text,
 ADD COLUMN notice_supersedes text,
 ADD COLUMN notice_expect_next_by timestamptz,
 ADD CONSTRAINT agent_notice_intent_valid CHECK (
  (notice_action IS NULL AND notice_supersedes IS NULL AND notice_expect_next_by IS NULL)
  OR (
   kind = 'notice'
   AND (notice_action IS NULL OR notice_action IN ('none', 'review', 'decide'))
   AND (notice_supersedes IS NULL OR notice_supersedes ~ '^[A-Za-z0-9_.:/-]{1,128}$')
   AND (notice_action IS DISTINCT FROM 'none' OR COALESCE(notice_policy->>'urgency', '') <> 'urgent')
  )
 );

CREATE INDEX agent_event_notice_supersedes
 ON cairn.agent_event(repo, publisher, notice_supersedes)
 WHERE kind = 'notice' AND notice_supersedes IS NOT NULL;

ALTER TABLE cairn.agent_delivery
 ADD COLUMN superseded_by uuid REFERENCES cairn.agent_event(event_id),
 ADD CONSTRAINT agent_delivery_superseded_by_valid CHECK (
  superseded_by IS NULL OR (state = 'ignored' AND code = 'superseded' AND superseded_by <> event_id)
 );

ALTER TABLE cairn.agent_session_poll
 ADD COLUMN auto_settle_action_none boolean NOT NULL DEFAULT false;
