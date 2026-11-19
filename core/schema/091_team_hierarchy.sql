-- Team hierarchy routing and filtered topic subscriptions (CAIRN-469):
-- campaign lead registry, out-of-chain warn/reroute publication policy,
-- blocker flag on events, and attribute/hierarchy filters on subscriptions.

ALTER TABLE cairn.agent_event
 ADD COLUMN blocker boolean NOT NULL DEFAULT false,
 ADD COLUMN routing_info jsonb,
 ADD CONSTRAINT agent_event_blocker_valid CHECK (
  NOT blocker OR COALESCE(notice_action, '') <> 'none'
 ),
 ADD CONSTRAINT agent_event_routing_info_valid CHECK (
  routing_info IS NULL OR jsonb_typeof(routing_info) = 'object'
 );

ALTER TABLE cairn.agent_subscription
 ADD COLUMN filter jsonb,
 ADD CONSTRAINT agent_subscription_filter_valid CHECK (
  filter IS NULL OR jsonb_typeof(filter) = 'object'
 );

ALTER TABLE cairn.agent_subscription_change
 ADD COLUMN filter jsonb,
 ADD CONSTRAINT agent_subscription_change_filter_valid CHECK (
  filter IS NULL OR jsonb_typeof(filter) = 'object'
 );

CREATE TABLE cairn.agent_team_campaign (
 repo text NOT NULL CHECK (octet_length(btrim(repo)) BETWEEN 1 AND 256 AND repo <> '*'),
 campaign text NOT NULL CHECK (campaign ~ '^[A-Za-z0-9_.-]{1,64}$'),
 root_agent text NOT NULL CHECK (octet_length(btrim(root_agent)) BETWEEN 1 AND 256 AND root_agent !~ '[\x00-\x20\x7f]'),
 out_of_chain_policy text NOT NULL DEFAULT 'warn' CHECK (out_of_chain_policy IN ('allow', 'warn', 'reroute')),
 sensitivity text NOT NULL DEFAULT 'shareable' CHECK (sensitivity IN ('local', 'shareable')),
 active boolean NOT NULL DEFAULT true,
 revision bigint NOT NULL DEFAULT 1 CHECK (revision >= 1),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY (repo, campaign)
);

CREATE TABLE cairn.agent_team_member (
 repo text NOT NULL,
 campaign text NOT NULL,
 member text NOT NULL CHECK (octet_length(btrim(member)) BETWEEN 1 AND 256 AND member !~ '[\x00-\x20\x7f]'),
 lead text CHECK (lead IS NULL OR (octet_length(btrim(lead)) BETWEEN 1 AND 256 AND lead !~ '[\x00-\x20\x7f]' AND lead <> member)),
 role text NOT NULL DEFAULT 'worker' CHECK (role IN ('root', 'lead', 'worker') AND ((role = 'root') = (lead IS NULL))),
 assigned_by text NOT NULL CHECK (octet_length(btrim(assigned_by)) BETWEEN 1 AND 256),
 active boolean NOT NULL DEFAULT true,
 updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY (repo, campaign, member),
 FOREIGN KEY (repo, campaign) REFERENCES cairn.agent_team_campaign(repo, campaign) ON DELETE CASCADE
);

CREATE INDEX agent_team_member_principal_idx
 ON cairn.agent_team_member(repo, member) WHERE active;

CREATE INDEX agent_team_member_lead_idx
 ON cairn.agent_team_member(repo, campaign, lead) WHERE active;

CREATE OR REPLACE FUNCTION cairn.subscription_matches_event(
 sub_filter jsonb,
 sub_repo text,
 sub_consumer text,
 pub_sender text,
 ev_kind text,
 ev_action text,
 ev_urgency text,
 ev_blocker boolean,
 ev_sensitivity text DEFAULT 'shareable'
) RETURNS boolean
LANGUAGE plpgsql
STABLE
AS $$
DECLARE
 is_blocker boolean;
 include_blockers boolean;
 has_kinds boolean;
 has_actions boolean;
 has_urgencies boolean;
 eff_action text;
 eff_urgency text;
 in_subtree boolean;
BEGIN
 IF sub_filter IS NULL OR sub_filter = '{}'::jsonb THEN
  RETURN true;
 END IF;

 IF COALESCE((sub_filter->>'direct_reports_only')::boolean, false) THEN
  IF NOT EXISTS (
   SELECT 1
   FROM cairn.agent_team_member m
   JOIN cairn.agent_team_campaign c ON c.repo = m.repo AND c.campaign = m.campaign
   WHERE m.repo = sub_repo AND m.member = pub_sender
     AND m.active AND c.active AND m.lead = sub_consumer
     AND (c.sensitivity = 'shareable' OR ev_sensitivity = 'local')
  ) THEN
   RETURN false;
  END IF;
 ELSIF COALESCE((sub_filter->>'subtree_only')::boolean, false) THEN
  WITH RECURSIVE chain(cur, lead, campaign, depth, visited) AS (
   SELECT m.member, m.lead, m.campaign, 1, ARRAY[m.member]
   FROM cairn.agent_team_member m
   JOIN cairn.agent_team_campaign c ON c.repo = m.repo AND c.campaign = m.campaign
   WHERE m.repo = sub_repo AND m.member = pub_sender
     AND m.active AND c.active AND m.lead IS NOT NULL
     AND (c.sensitivity = 'shareable' OR ev_sensitivity = 'local')
   UNION ALL
   SELECT p.member, p.lead, p.campaign, chain.depth + 1, chain.visited || p.member
   FROM chain
   JOIN cairn.agent_team_member p ON p.repo = sub_repo AND p.campaign = chain.campaign AND p.member = chain.lead
   WHERE p.active AND chain.depth < 16 AND p.lead IS NOT NULL AND NOT (p.member = ANY(chain.visited))
  )
  SELECT EXISTS (SELECT 1 FROM chain WHERE lead = sub_consumer) INTO in_subtree;
  IF NOT COALESCE(in_subtree, false) THEN
   RETURN false;
  END IF;
 END IF;

 is_blocker := COALESCE(ev_blocker, false) OR ev_kind = 'blocker';
 include_blockers := COALESCE((sub_filter->>'include_blockers')::boolean, false);
 has_kinds := jsonb_typeof(sub_filter->'kinds') = 'array' AND jsonb_array_length(sub_filter->'kinds') > 0;
 has_actions := jsonb_typeof(sub_filter->'actions') = 'array' AND jsonb_array_length(sub_filter->'actions') > 0;
 has_urgencies := jsonb_typeof(sub_filter->'urgencies') = 'array' AND jsonb_array_length(sub_filter->'urgencies') > 0;

 IF NOT has_kinds AND NOT has_actions AND NOT has_urgencies AND NOT include_blockers THEN
  RETURN true;
 END IF;
 IF include_blockers AND is_blocker THEN
  RETURN true;
 END IF;
 IF NOT has_kinds AND NOT has_actions AND NOT has_urgencies AND include_blockers THEN
  RETURN false;
 END IF;

 eff_action := CASE
  WHEN COALESCE(ev_action, '') <> '' THEN ev_action
  WHEN ev_kind = 'request' THEN 'complete'
  WHEN ev_kind = 'decide' THEN 'decide'
  ELSE 'review'
 END;
 eff_urgency := CASE
  WHEN COALESCE(ev_urgency, '') <> '' THEN ev_urgency
  WHEN is_blocker OR ev_kind = 'request' OR eff_action = 'decide' THEN 'urgent'
  ELSE 'routine'
 END;

 IF has_kinds AND NOT ((sub_filter->'kinds') ? ev_kind OR (is_blocker AND (sub_filter->'kinds') ? 'blocker')) THEN
  RETURN false;
 END IF;
 IF has_actions AND NOT ((sub_filter->'actions') ? eff_action) THEN
  RETURN false;
 END IF;
 IF has_urgencies AND NOT ((sub_filter->'urgencies') ? eff_urgency) THEN
  RETURN false;
 END IF;
 RETURN true;
END;
$$;
