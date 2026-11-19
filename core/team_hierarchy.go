package core

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"slices"
	"sort"
	"strings"
	"time"

	"github.com/jackc/pgx/v5"
)

const (
	MaxTeamHierarchyDepth = 16
	MaxCampaignMembers    = 256
)

type SubscriptionFilter struct {
	Kinds             []string `json:"kinds,omitempty"`
	Actions           []string `json:"actions,omitempty"`
	Urgencies         []string `json:"urgencies,omitempty"`
	IncludeBlockers   bool     `json:"include_blockers,omitempty"`
	DirectReportsOnly bool     `json:"direct_reports_only,omitempty"`
	SubtreeOnly       bool     `json:"subtree_only,omitempty"`
}

type EventRouting struct {
	Campaign             string            `json:"campaign,omitempty"`
	Policy               string            `json:"policy"`
	OutOfChain           bool              `json:"out_of_chain"`
	Rerouted             bool              `json:"rerouted,omitempty"`
	OriginalDestination  *EventDestination `json:"original_destination,omitempty"`
	EffectiveDestination EventDestination  `json:"effective_destination"`
	Lead                 string            `json:"lead,omitempty"`
	Warning              string            `json:"warning,omitempty"`
	LocalOnly            bool              `json:"local_only,omitempty"`
}

type TeamMember struct {
	Repo       string    `json:"repo"`
	Campaign   string    `json:"campaign"`
	Member     string    `json:"member"`
	Lead       string    `json:"lead,omitempty"`
	Role       string    `json:"role"`
	AssignedBy string    `json:"assigned_by"`
	Active     bool      `json:"active"`
	UpdatedAt  time.Time `json:"updated_at"`
}

type TeamCampaign struct {
	Repo             string       `json:"repo"`
	Campaign         string       `json:"campaign"`
	RootAgent        string       `json:"root_agent"`
	OutOfChainPolicy string       `json:"out_of_chain_policy"`
	Sensitivity      string       `json:"sensitivity"`
	Active           bool         `json:"active"`
	Revision         int64        `json:"revision"`
	CreatedAt        time.Time    `json:"created_at"`
	UpdatedAt        time.Time    `json:"updated_at"`
	Members          []TeamMember `json:"members,omitempty"`
}

type SetTeamCampaignRequest struct {
	RequestID        string `json:"request_id"`
	Repo             string `json:"repo,omitempty"`
	Campaign         string `json:"campaign"`
	RootAgent        string `json:"root_agent,omitempty"`
	OutOfChainPolicy string `json:"out_of_chain_policy,omitempty"`
	Sensitivity      string `json:"sensitivity,omitempty"`
	Active           *bool  `json:"active,omitempty"`
	ExpectedRevision int64  `json:"expected_revision,omitempty"`
}

type SetTeamMemberRequest struct {
	RequestID string `json:"request_id"`
	Repo      string `json:"repo,omitempty"`
	Campaign  string `json:"campaign"`
	Member    string `json:"member"`
	Lead      string `json:"lead,omitempty"`
	Role      string `json:"role,omitempty"`
	Active    *bool  `json:"active,omitempty"`
}

type GetTeamCampaignRequest struct {
	Repo     string `json:"repo,omitempty"`
	Campaign string `json:"campaign"`
}

type ListTeamCampaignsRequest struct {
	Repo       string `json:"repo,omitempty"`
	ActiveOnly bool   `json:"active_only,omitempty"`
	After      string `json:"after,omitempty"`
	Limit      int    `json:"limit,omitempty"`
}

type TeamCampaignPage struct {
	Repo      string         `json:"repo"`
	Campaigns []TeamCampaign `json:"campaigns"`
	NextAfter string         `json:"next_after,omitempty"`
	More      bool           `json:"more"`
}

func NormalizeSubscriptionFilter(f *SubscriptionFilter) (*SubscriptionFilter, error) {
	if f == nil {
		return nil, nil
	}
	if f.DirectReportsOnly && f.SubtreeOnly {
		return nil, failure("INVALID_REQUEST", "choose at most one of direct_reports_only and subtree_only")
	}
	normList := func(items []string, maxItems int, valid func(string) bool, label string) ([]string, error) {
		if len(items) > maxItems {
			return nil, failure("INVALID_REQUEST", label+" exceeds maximum filter cardinality")
		}
		seen := make(map[string]bool, len(items))
		var out []string
		for _, raw := range items {
			v := strings.TrimSpace(raw)
			if !valid(v) {
				return nil, failure("INVALID_REQUEST", "invalid "+label+" filter value")
			}
			if !seen[v] {
				seen[v] = true
				out = append(out, v)
			}
		}
		sort.Strings(out)
		return out, nil
	}
	kinds, err := normList(f.Kinds, 16, func(s string) bool { return eventName.MatchString(s) }, "kinds")
	if err != nil {
		return nil, err
	}
	actions, err := normList(f.Actions, 8, func(s string) bool {
		return s == "none" || s == "review" || s == "decide" || s == "complete"
	}, "actions")
	if err != nil {
		return nil, err
	}
	urgencies, err := normList(f.Urgencies, 4, func(s string) bool {
		return s == "routine" || s == "urgent"
	}, "urgencies")
	if err != nil {
		return nil, err
	}
	if len(kinds) == 0 && len(actions) == 0 && len(urgencies) == 0 && !f.IncludeBlockers && !f.DirectReportsOnly && !f.SubtreeOnly {
		return nil, nil
	}
	return &SubscriptionFilter{
		Kinds:             kinds,
		Actions:           actions,
		Urgencies:         urgencies,
		IncludeBlockers:   f.IncludeBlockers,
		DirectReportsOnly: f.DirectReportsOnly,
		SubtreeOnly:       f.SubtreeOnly,
	}, nil
}

func subscriptionFilterEqual(a, b *SubscriptionFilter) bool {
	if a == nil && b == nil {
		return true
	}
	if a == nil || b == nil {
		return false
	}
	return slices.Equal(a.Kinds, b.Kinds) &&
		slices.Equal(a.Actions, b.Actions) &&
		slices.Equal(a.Urgencies, b.Urgencies) &&
		a.IncludeBlockers == b.IncludeBlockers &&
		a.DirectReportsOnly == b.DirectReportsOnly &&
		a.SubtreeOnly == b.SubtreeOnly
}

type teamNode struct {
	lead string
	role string
}

func isAncestorOrSelf(ancestor, target string, members map[string]teamNode) bool {
	if ancestor == "" || target == "" {
		return false
	}
	cur := target
	for depth := 0; depth <= MaxTeamHierarchyDepth; depth++ {
		if cur == ancestor {
			return true
		}
		n, ok := members[cur]
		if !ok || n.lead == "" {
			return false
		}
		cur = n.lead
	}
	return false
}

func isStrictAncestor(ancestor, target string, members map[string]teamNode) bool {
	if ancestor == "" || target == "" || ancestor == target {
		return false
	}
	return isAncestorOrSelf(ancestor, target, members)
}

func nextHopDownToward(ancestor, descendant string, members map[string]teamNode) string {
	if ancestor == "" || descendant == "" || ancestor == descendant {
		return ""
	}
	cur := descendant
	for depth := 0; depth <= MaxTeamHierarchyDepth; depth++ {
		n, ok := members[cur]
		if !ok || n.lead == "" {
			return ""
		}
		if n.lead == ancestor {
			return cur
		}
		cur = n.lead
	}
	return ""
}

func validateCampaignTree(rootAgent string, members map[string]teamNode) error {
	if len(members) > MaxCampaignMembers {
		return failure("BUDGET_REFUSED", "campaign exceeds maximum active member limit (256)")
	}
	root, ok := members[rootAgent]
	if !ok || root.role != "root" || root.lead != "" {
		return failure("INVALID_REQUEST", "campaign root_agent must remain the root member")
	}
	for m := range members {
		if m == rootAgent {
			continue
		}
		cur := m
		reached := false
		for depth := 0; depth < MaxTeamHierarchyDepth; depth++ {
			n, exists := members[cur]
			if !exists || n.lead == "" {
				return failure("INVALID_REQUEST", "team member chain does not reach campaign root")
			}
			parent, pExists := members[n.lead]
			if !pExists || (parent.role != "root" && parent.role != "lead") {
				return failure("INVALID_REQUEST", "team member lead must be an active root or lead in the same campaign")
			}
			cur = n.lead
			if cur == rootAgent {
				reached = true
				break
			}
		}
		if !reached {
			return failure("INVALID_REQUEST", "team hierarchy contains a cycle or exceeds maximum depth 16")
		}
	}
	return nil
}

func loadCampaignMembersTx(ctx context.Context, tx pgx.Tx, repo, campaign string, activeOnly bool) ([]TeamMember, map[string]teamNode, error) {
	q := `SELECT repo, campaign, member, COALESCE(lead, ''), role, assigned_by, active, updated_at FROM cairn.agent_team_member WHERE repo=$1 AND campaign=$2`
	if activeOnly {
		q += ` AND active`
	}
	q += ` ORDER BY CASE role WHEN 'root' THEN 0 WHEN 'lead' THEN 1 ELSE 2 END, member`
	rows, err := tx.Query(ctx, q, repo, campaign)
	if err != nil {
		return nil, nil, err
	}
	defer rows.Close()
	out := []TeamMember{}
	nodes := map[string]teamNode{}
	for rows.Next() {
		var m TeamMember
		if err = rows.Scan(&m.Repo, &m.Campaign, &m.Member, &m.Lead, &m.Role, &m.AssignedBy, &m.Active, &m.UpdatedAt); err != nil {
			return nil, nil, err
		}
		out = append(out, m)
		if m.Active {
			nodes[m.Member] = teamNode{lead: m.Lead, role: m.Role}
		}
	}
	return out, nodes, rows.Err()
}

func readTeamCampaignTx(ctx context.Context, tx pgx.Tx, repo, campaign string, allowLocal, withMembers bool) (TeamCampaign, error) {
	var c TeamCampaign
	err := tx.QueryRow(ctx, `SELECT repo, campaign, root_agent, out_of_chain_policy, sensitivity, active, revision, created_at, updated_at FROM cairn.agent_team_campaign WHERE repo=$1 AND campaign=$2 AND (sensitivity='shareable' OR $3)`, repo, campaign, allowLocal).Scan(
		&c.Repo, &c.Campaign, &c.RootAgent, &c.OutOfChainPolicy, &c.Sensitivity, &c.Active, &c.Revision, &c.CreatedAt, &c.UpdatedAt,
	)
	if errors.Is(err, pgx.ErrNoRows) {
		return c, failure("NOT_FOUND", "team campaign not found")
	}
	if err != nil || !withMembers {
		return c, err
	}
	members, _, err := loadCampaignMembersTx(ctx, tx, repo, campaign, false)
	c.Members = members
	return c, err
}

func ensurePrincipalFreeInOtherCampaignsTx(ctx context.Context, tx pgx.Tx, repo, campaign, member string) error {
	var other string
	err := tx.QueryRow(ctx, `SELECT m.campaign FROM cairn.agent_team_member m JOIN cairn.agent_team_campaign c USING(repo, campaign) WHERE m.repo=$1 AND m.member=$2 AND m.active AND c.active AND m.campaign<>$3 LIMIT 1`, repo, member, campaign).Scan(&other)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil
	}
	if err != nil {
		return err
	}
	return failure("VERSION_CONFLICT", fmt.Sprintf("principal %s already belongs to active campaign %s", member, other))
}

func validTeamPrincipal(p string) bool {
	if !validWorkstreamPrincipal(p) {
		return false
	}
	if p == "local" || p == "hosted" || p == "system" {
		return false
	}
	for _, prefix := range []string{"operator:", "operator/", "system:", "daemon:", "scheduler:"} {
		if strings.HasPrefix(p, prefix) {
			return false
		}
	}
	return true
}

func (s *Store) SetTeamCampaign(ctx context.Context, req SetTeamCampaignRequest, dest Destination) (TeamCampaign, error) {
	var empty TeamCampaign
	if err := validEventDestination(dest); err != nil {
		return empty, err
	}
	if validID(req.RequestID) != nil {
		return empty, failure("INVALID_REQUEST", "stable request UUID required")
	}
	repo, err := s.eventScope(req.Repo, "")
	if err != nil {
		return empty, err
	}
	req.Repo = repo
	if !workstreamNamePattern.MatchString(req.Campaign) {
		return empty, failure("INVALID_REQUEST", "campaign must be 1..64 ASCII letters, digits or _.-")
	}
	if req.RootAgent != "" && !validTeamPrincipal(req.RootAgent) {
		return empty, failure("INVALID_REQUEST", "invalid root_agent principal")
	}
	if !s.channel.Operator && req.RootAgent != "" && req.RootAgent != s.channel.Principal {
		return empty, failure("AUTHORITY_DENIED", "only the root_agent or an operator can create or update a team campaign")
	}
	if req.OutOfChainPolicy != "" && req.OutOfChainPolicy != "allow" && req.OutOfChainPolicy != "warn" && req.OutOfChainPolicy != "reroute" {
		return empty, failure("INVALID_REQUEST", "out_of_chain_policy must be allow, warn or reroute")
	}
	if req.Sensitivity != "" && req.Sensitivity != "shareable" && req.Sensitivity != "local" {
		return empty, failure("INVALID_REQUEST", "sensitivity must be shareable or local")
	}
	if req.Sensitivity == "local" && !dest.AllowLocal {
		return empty, failure("DESTINATION_PROHIBITED", "hosted destination cannot write local team campaign")
	}
	if req.ExpectedRevision < 0 {
		return empty, failure("INVALID_REQUEST", "expected_revision must be nonnegative")
	}
	input := struct {
		SetTeamCampaignRequest
		Destination string `json:"destination"`
		AllowLocal  bool   `json:"allow_local"`
	}{req, dest.Name, dest.AllowLocal}
	guard := func(tx pgx.Tx) error {
		if err := lock(ctx, tx, "agent-events:"+req.Repo); err != nil {
			return err
		}
		var curRoot, curSens string
		err := tx.QueryRow(ctx, `SELECT root_agent, sensitivity FROM cairn.agent_team_campaign WHERE repo=$1 AND campaign=$2`, req.Repo, req.Campaign).Scan(&curRoot, &curSens)
		if errors.Is(err, pgx.ErrNoRows) {
			return nil
		}
		if err != nil {
			return err
		}
		if curSens == "local" && !dest.AllowLocal {
			if !s.channel.Operator && s.channel.Principal != curRoot {
				return failure("AUTHORITY_DENIED", "only the campaign root_agent or an operator can update a team campaign")
			}
			return failure("AUTHORITY_DENIED", "local team campaign cannot be updated from a hosted destination")
		}
		return nil
	}
	return mutate(ctx, s, "team-campaign-set", req.RequestID, input, func(tx pgx.Tx) (TeamCampaign, error) {
		if _, err := retrievalGeneration(ctx, tx); err != nil {
			return empty, err
		}
		if err := lock(ctx, tx, "agent-events:"+req.Repo); err != nil {
			return empty, err
		}
		caller := s.channel.Principal
		var curRoot, curPolicy, curSens string
		var curActive bool
		var curRev int64
		err := tx.QueryRow(ctx, `SELECT root_agent, out_of_chain_policy, sensitivity, active, revision FROM cairn.agent_team_campaign WHERE repo=$1 AND campaign=$2 FOR UPDATE`, req.Repo, req.Campaign).Scan(
			&curRoot, &curPolicy, &curSens, &curActive, &curRev,
		)
		if errors.Is(err, pgx.ErrNoRows) {
			if req.ExpectedRevision != 0 {
				return empty, failure("VERSION_CONFLICT", "team campaign does not exist for expected_revision")
			}
			rootAgent := req.RootAgent
			if rootAgent == "" {
				rootAgent = caller
			}
			if s.channel.Operator && req.RootAgent == "" {
				return empty, failure("INVALID_REQUEST", "operator must specify an explicit agent root_agent")
			}
			if !validTeamPrincipal(rootAgent) {
				return empty, failure("INVALID_REQUEST", "campaign root_agent must be an agent principal")
			}
			if !s.channel.Operator && caller != rootAgent {
				return empty, failure("AUTHORITY_DENIED", "only the root_agent or an operator can create a team campaign")
			}
			policy := req.OutOfChainPolicy
			if policy == "" {
				policy = "warn"
			}
			sens := req.Sensitivity
			if sens == "" {
				sens = "shareable"
			}
			active := true
			if req.Active != nil {
				active = *req.Active
			}
			if active {
				if err = ensurePrincipalFreeInOtherCampaignsTx(ctx, tx, req.Repo, req.Campaign, rootAgent); err != nil {
					return empty, err
				}
			}
			if _, err = tx.Exec(ctx, `INSERT INTO cairn.agent_team_campaign(repo, campaign, root_agent, out_of_chain_policy, sensitivity, active, revision) VALUES($1,$2,$3,$4,$5,$6,1)`, req.Repo, req.Campaign, rootAgent, policy, sens, active); err != nil {
				return empty, err
			}
			if _, err = tx.Exec(ctx, `INSERT INTO cairn.agent_team_member(repo, campaign, member, lead, role, assigned_by, active) VALUES($1,$2,$3,NULL,'root',$4,true)`, req.Repo, req.Campaign, rootAgent, caller); err != nil {
				return empty, err
			}
			return readTeamCampaignTx(ctx, tx, req.Repo, req.Campaign, dest.AllowLocal, true)
		}
		if err != nil {
			return empty, err
		}
		if !s.channel.Operator && caller != curRoot {
			return empty, failure("AUTHORITY_DENIED", "only the campaign root_agent or an operator can update a team campaign")
		}
		if curSens == "local" && !dest.AllowLocal {
			return empty, failure("AUTHORITY_DENIED", "local team campaign cannot be updated from a hosted destination")
		}
		if req.RootAgent != "" && req.RootAgent != curRoot {
			return empty, failure("INVALID_REQUEST", "campaign root_agent cannot be changed once created")
		}
		if req.ExpectedRevision > 0 && req.ExpectedRevision != curRev {
			return empty, failure("VERSION_CONFLICT", "team campaign revision changed")
		}
		if curSens == "local" && req.Sensitivity == "shareable" {
			return empty, failure("DESTINATION_PROHIBITED", "cannot downgrade local team campaign sensitivity to shareable")
		}
		policy := curPolicy
		if req.OutOfChainPolicy != "" {
			policy = req.OutOfChainPolicy
		}
		sens := curSens
		if req.Sensitivity != "" {
			sens = req.Sensitivity
		}
		active := curActive
		if req.Active != nil {
			active = *req.Active
		}
		if active && !curActive {
			if _, err = tx.Exec(ctx, `INSERT INTO cairn.agent_team_member(repo, campaign, member, lead, role, assigned_by, active, updated_at) VALUES($1,$2,$3,NULL,'root',$4,true,clock_timestamp()) ON CONFLICT(repo, campaign, member) DO UPDATE SET lead=NULL, role='root', active=true, updated_at=clock_timestamp()`, req.Repo, req.Campaign, curRoot, caller); err != nil {
				return empty, err
			}
			activeMembers, nodes, mErr := loadCampaignMembersTx(ctx, tx, req.Repo, req.Campaign, true)
			if mErr != nil {
				return empty, mErr
			}
			if err = validateCampaignTree(curRoot, nodes); err != nil {
				return empty, err
			}
			for _, m := range activeMembers {
				if err = ensurePrincipalFreeInOtherCampaignsTx(ctx, tx, req.Repo, req.Campaign, m.Member); err != nil {
					return empty, err
				}
			}
		}
		if _, err = tx.Exec(ctx, `UPDATE cairn.agent_team_campaign SET out_of_chain_policy=$3, sensitivity=$4, active=$5, revision=revision+1, updated_at=clock_timestamp() WHERE repo=$1 AND campaign=$2`, req.Repo, req.Campaign, policy, sens, active); err != nil {
			return empty, err
		}
		return readTeamCampaignTx(ctx, tx, req.Repo, req.Campaign, dest.AllowLocal, true)
	}, guard)
}

func (s *Store) SetTeamMember(ctx context.Context, req SetTeamMemberRequest, dest Destination) (TeamMember, error) {
	var empty TeamMember
	if err := validEventDestination(dest); err != nil {
		return empty, err
	}
	if validID(req.RequestID) != nil {
		return empty, failure("INVALID_REQUEST", "stable request UUID required")
	}
	repo, err := s.eventScope(req.Repo, "")
	if err != nil {
		return empty, err
	}
	req.Repo = repo
	if !workstreamNamePattern.MatchString(req.Campaign) {
		return empty, failure("INVALID_REQUEST", "campaign must be 1..64 ASCII letters, digits or _.-")
	}
	if !validTeamPrincipal(req.Member) {
		return empty, failure("INVALID_REQUEST", "invalid member principal")
	}
	if req.Lead != "" && (!validTeamPrincipal(req.Lead) || req.Lead == req.Member) {
		return empty, failure("INVALID_REQUEST", "lead must be a valid agent principal distinct from member")
	}
	if req.Role != "" && req.Role != "lead" && req.Role != "worker" {
		return empty, failure("INVALID_REQUEST", "role must be lead or worker")
	}
	input := struct {
		SetTeamMemberRequest
		Destination string `json:"destination"`
		AllowLocal  bool   `json:"allow_local"`
	}{req, dest.Name, dest.AllowLocal}
	guard := func(tx pgx.Tx) error {
		if err := lock(ctx, tx, "agent-events:"+req.Repo); err != nil {
			return err
		}
		var curSens string
		err := tx.QueryRow(ctx, `SELECT sensitivity FROM cairn.agent_team_campaign WHERE repo=$1 AND campaign=$2`, req.Repo, req.Campaign).Scan(&curSens)
		if errors.Is(err, pgx.ErrNoRows) {
			return nil
		}
		if err != nil {
			return err
		}
		if curSens == "local" && !dest.AllowLocal {
			return failure("NOT_FOUND", "team campaign not found")
		}
		return nil
	}
	return mutate(ctx, s, "team-member-set", req.RequestID, input, func(tx pgx.Tx) (TeamMember, error) {
		if _, err := retrievalGeneration(ctx, tx); err != nil {
			return empty, err
		}
		if err := lock(ctx, tx, "agent-events:"+req.Repo); err != nil {
			return empty, err
		}
		var campRoot, campSens string
		var campActive bool
		err := tx.QueryRow(ctx, `SELECT root_agent, sensitivity, active FROM cairn.agent_team_campaign WHERE repo=$1 AND campaign=$2 FOR UPDATE`, req.Repo, req.Campaign).Scan(&campRoot, &campSens, &campActive)
		if errors.Is(err, pgx.ErrNoRows) || (err == nil && campSens == "local" && !dest.AllowLocal) {
			return empty, failure("NOT_FOUND", "team campaign not found")
		}
		if err != nil {
			return empty, err
		}
		if req.Member == campRoot {
			return empty, failure("INVALID_REQUEST", "campaign root_agent cannot be modified as a subordinate member")
		}
		active := true
		if req.Active != nil {
			active = *req.Active
		}
		if active && !campActive {
			return empty, failure("INVALID_REQUEST", "cannot add active members to an inactive campaign")
		}
		allMembers, nodes, err := loadCampaignMembersTx(ctx, tx, req.Repo, req.Campaign, false)
		if err != nil {
			return empty, err
		}
		var existedRow, existedActive bool
		var prevLead, prevRole string
		for _, m := range allMembers {
			if m.Member == req.Member {
				existedRow = true
				existedActive = m.Active
				prevLead = m.Lead
				prevRole = m.Role
				break
			}
		}
		role := req.Role
		if role == "" {
			if existedRow {
				role = prevRole
			} else {
				role = "worker"
			}
		}
		lead := req.Lead
		if lead == "" && existedRow {
			lead = prevLead
		}
		caller := s.channel.Principal
		authorized := s.channel.Operator || caller == campRoot
		if !authorized {
			if !existedRow && active && caller == req.Member && role == "worker" {
				if leadNode, ok := nodes[lead]; ok && (leadNode.role == "root" || leadNode.role == "lead") {
					authorized = true
				}
			} else if existedRow {
				if callerNode, ok := nodes[caller]; ok && callerNode.role == "lead" {
					existingAllowed := false
					if existedActive {
						existingAllowed = isStrictAncestor(caller, req.Member, nodes)
					} else if prevLead != "" {
						existingAllowed = isAncestorOrSelf(caller, prevLead, nodes)
					}
					newAllowed := !active || (lead != "" && isAncestorOrSelf(caller, lead, nodes))
					authorized = existingAllowed && newAllowed
				}
			}
		}
		if !authorized {
			return empty, failure("AUTHORITY_DENIED", "only the campaign root_agent, a self-enrolling worker, an ancestor lead of an enrolled member, or an operator can manage team members")
		}
		hasActiveChildren := false
		for child, cn := range nodes {
			if child != req.Member && cn.lead == req.Member {
				hasActiveChildren = true
				break
			}
		}
		if !active {
			if hasActiveChildren {
				return empty, failure("INVALID_REQUEST", "member still has active subordinates; reassign or deactivate them first")
			}
			if !existedRow {
				return empty, failure("NOT_FOUND", "team member not found")
			}
			var out TeamMember
			err = tx.QueryRow(ctx, `UPDATE cairn.agent_team_member SET active=false, assigned_by=$4, updated_at=clock_timestamp() WHERE repo=$1 AND campaign=$2 AND member=$3 RETURNING repo, campaign, member, COALESCE(lead, ''), role, assigned_by, active, updated_at`, req.Repo, req.Campaign, req.Member, caller).Scan(
				&out.Repo, &out.Campaign, &out.Member, &out.Lead, &out.Role, &out.AssignedBy, &out.Active, &out.UpdatedAt,
			)
			return out, err
		}
		if role == "worker" && hasActiveChildren {
			return empty, failure("INVALID_REQUEST", "member with active subordinates must have role lead")
		}
		if lead == "" {
			return empty, failure("INVALID_REQUEST", "lead is required for active team members")
		}
		if err = ensurePrincipalFreeInOtherCampaignsTx(ctx, tx, req.Repo, req.Campaign, req.Member); err != nil {
			return empty, err
		}
		nodes[req.Member] = teamNode{lead: lead, role: role}
		if err = validateCampaignTree(campRoot, nodes); err != nil {
			return empty, err
		}
		var out TeamMember
		err = tx.QueryRow(ctx, `INSERT INTO cairn.agent_team_member(repo, campaign, member, lead, role, assigned_by, active, updated_at) VALUES($1,$2,$3,$4,$5,$6,true,clock_timestamp()) ON CONFLICT(repo, campaign, member) DO UPDATE SET lead=$4, role=$5, assigned_by=$6, active=true, updated_at=clock_timestamp() RETURNING repo, campaign, member, COALESCE(lead, ''), role, assigned_by, active, updated_at`, req.Repo, req.Campaign, req.Member, lead, role, caller).Scan(
			&out.Repo, &out.Campaign, &out.Member, &out.Lead, &out.Role, &out.AssignedBy, &out.Active, &out.UpdatedAt,
		)
		return out, err
	}, guard)
}

func (s *Store) GetTeamCampaign(ctx context.Context, req GetTeamCampaignRequest, dest Destination) (TeamCampaign, error) {
	var empty TeamCampaign
	if err := validEventDestination(dest); err != nil {
		return empty, err
	}
	repo, err := s.eventScope(req.Repo, "")
	if err != nil {
		return empty, err
	}
	if !workstreamNamePattern.MatchString(req.Campaign) {
		return empty, failure("INVALID_REQUEST", "campaign must be 1..64 ASCII letters, digits or _.-")
	}
	tx, err := s.begin(ctx)
	if err != nil {
		return empty, err
	}
	defer tx.Rollback(ctx)
	return readTeamCampaignTx(ctx, tx, repo, req.Campaign, dest.AllowLocal, true)
}

func (s *Store) ListTeamCampaigns(ctx context.Context, req ListTeamCampaignsRequest, dest Destination) (TeamCampaignPage, error) {
	out := TeamCampaignPage{Campaigns: []TeamCampaign{}}
	if err := validEventDestination(dest); err != nil {
		return out, err
	}
	repo, err := s.eventScope(req.Repo, "")
	if err != nil {
		return out, err
	}
	out.Repo = repo
	limit, err := eventLimit(req.Limit)
	if err != nil {
		return out, err
	}
	if req.After != "" && !workstreamNamePattern.MatchString(req.After) {
		return out, failure("INVALID_REQUEST", "invalid campaign cursor")
	}
	tx, err := s.begin(ctx)
	if err != nil {
		return out, err
	}
	defer tx.Rollback(ctx)
	rows, err := tx.Query(ctx, `SELECT repo, campaign, root_agent, out_of_chain_policy, sensitivity, active, revision, created_at, updated_at FROM cairn.agent_team_campaign WHERE repo=$1 AND (sensitivity='shareable' OR $2) AND (NOT $3 OR active) AND campaign>$4 ORDER BY campaign LIMIT $5`, repo, dest.AllowLocal, req.ActiveOnly, req.After, limit+1)
	if err != nil {
		return out, err
	}
	defer rows.Close()
	for rows.Next() {
		var c TeamCampaign
		if err = rows.Scan(&c.Repo, &c.Campaign, &c.RootAgent, &c.OutOfChainPolicy, &c.Sensitivity, &c.Active, &c.Revision, &c.CreatedAt, &c.UpdatedAt); err != nil {
			return out, err
		}
		if len(out.Campaigns) == limit {
			out.More = true
			break
		}
		out.Campaigns = append(out.Campaigns, c)
		out.NextAfter = c.Campaign
	}
	if err = rows.Err(); err != nil {
		return out, err
	}
	for i := range out.Campaigns {
		members, _, mErr := loadCampaignMembersTx(ctx, tx, repo, out.Campaigns[i].Campaign, false)
		if mErr != nil {
			return out, mErr
		}
		out.Campaigns[i].Members = members
	}
	return out, nil
}

func evaluateHierarchyRoutingTx(ctx context.Context, tx pgx.Tx, repo, caller, kind, eventSensitivity string, origDest EventDestination) (*EventRouting, error) {
	if origDest.Type != "agent" || origDest.Name == caller {
		return nil, nil
	}
	var campaign, rootAgent, policy, sens, callerLead string
	err := tx.QueryRow(ctx, `SELECT c.campaign, c.root_agent, c.out_of_chain_policy, c.sensitivity, COALESCE(m.lead, '') FROM cairn.agent_team_member m JOIN cairn.agent_team_campaign c USING(repo, campaign) WHERE m.repo=$1 AND m.member=$2 AND m.active AND c.active`, repo, caller).Scan(
		&campaign, &rootAgent, &policy, &sens, &callerLead,
	)
	if errors.Is(err, pgx.ErrNoRows) || (err == nil && policy == "allow") {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	if callerLead != "" && origDest.Name == callerLead {
		return nil, nil
	}
	_, nodes, err := loadCampaignMembersTx(ctx, tx, repo, campaign, true)
	if err != nil {
		return nil, err
	}
	if tn, ok := nodes[origDest.Name]; ok && tn.lead == caller {
		return nil, nil
	}
	origCopy := origDest
	routing := &EventRouting{
		Campaign:             campaign,
		Policy:               policy,
		OutOfChain:           true,
		OriginalDestination:  &origCopy,
		EffectiveDestination: origDest,
		Lead:                 callerLead,
		LocalOnly:            sens == "local",
	}
	if policy == "reroute" && (kind == "" || kind == "notice") && (sens == "shareable" || eventSensitivity == "local") {
		rerouteTo := ""
		if downHop := nextHopDownToward(caller, origDest.Name, nodes); downHop != "" {
			rerouteTo = downHop
		} else if callerLead != "" {
			rerouteTo = callerLead
		}
		if rerouteTo != "" && rerouteTo != origDest.Name {
			routing.Rerouted = true
			routing.EffectiveDestination = EventDestination{Type: "agent", Name: rerouteTo}
			routing.Warning = fmt.Sprintf("rerouted out-of-chain publication from %s to hierarchy hop %s in campaign %s", origDest.Name, rerouteTo, campaign)
			return routing, nil
		}
	}
	if callerLead != "" {
		routing.Warning = fmt.Sprintf("recipient %s is outside sender's immediate hierarchy chain (lead: %s) in campaign %s", origDest.Name, callerLead, campaign)
	} else {
		routing.Warning = fmt.Sprintf("recipient %s is outside sender's immediate hierarchy chain in campaign %s", origDest.Name, campaign)
	}
	return routing, nil
}

func encodeSubscriptionFilter(f *SubscriptionFilter) ([]byte, error) {
	if f == nil {
		return nil, nil
	}
	return json.Marshal(f)
}
