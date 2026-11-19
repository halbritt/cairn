package core

import (
	"context"
	"errors"
	"fmt"
	"strings"
	"unicode/utf8"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
)

const MaxCampaignHandoffNoteLen = 8192

type CheckCoordinatorHandoffRequest struct {
	Repo     string `json:"repo,omitempty"`
	Campaign string `json:"campaign"`
}

type RecordCoordinatorCompactionRequest struct {
	RequestID string `json:"request_id"`
	Repo      string `json:"repo,omitempty"`
	Campaign  string `json:"campaign"`
}

type CoordinatorHandoffStatus struct {
	Repo                      string   `json:"repo"`
	Campaign                  string   `json:"campaign"`
	RoleAddress               string   `json:"role_address"`
	HolderPrincipal           string   `json:"holder_principal"`
	AgentID                   string   `json:"agent_id,omitempty"`
	ExecutionID               string   `json:"execution_id,omitempty"`
	SessionFound              bool     `json:"session_found"`
	Stopped                   bool     `json:"stopped,omitempty"`
	AgeSeconds                int64    `json:"age_seconds"`
	CompactionCount           int      `json:"compaction_count"`
	MaxCoordinatorAgeSeconds  *int     `json:"max_coordinator_age_seconds,omitempty"`
	MaxCoordinatorCompactions *int     `json:"max_coordinator_compactions,omitempty"`
	ShouldHandoff             bool     `json:"should_handoff"`
	Reasons                   []string `json:"reasons"`
	ProposedCommand           string   `json:"proposed_command,omitempty"`
}

type NewCoordinatorSessionSpec struct {
	Binding         string         `json:"binding"`
	NativeSessionID string         `json:"native_session_id"`
	Metadata        *AgentMetadata `json:"metadata,omitempty"`
}

type HandoffCampaignCoordinatorRequest struct {
	RequestID              string                     `json:"request_id"`
	Repo                   string                     `json:"repo,omitempty"`
	Campaign               string                     `json:"campaign"`
	OnlyIfThresholdReached bool                       `json:"only_if_threshold_reached,omitempty"`
	Successor              *AgentSessionRef           `json:"successor,omitempty"`
	NewSession             *NewCoordinatorSessionSpec `json:"new_session,omitempty"`
	Reason                 string                     `json:"reason,omitempty"`
	Note                   string                     `json:"note,omitempty"`
	Shareable              bool                       `json:"shareable,omitempty"`
}

type CoordinatorHandoffResult struct {
	Performed                bool                     `json:"performed"`
	HandoffID                string                   `json:"handoff_id,omitempty"`
	Campaign                 TeamCampaign             `json:"campaign"`
	RoleAddress              string                   `json:"role_address"`
	FromPrincipal            string                   `json:"from_principal"`
	ToPrincipal              string                   `json:"to_principal,omitempty"`
	ToSession                *AgentInstance           `json:"to_session,omitempty"`
	Handoff                  *RecordVersionRef        `json:"handoff,omitempty"`
	TransferredDeliveries    int                      `json:"transferred_deliveries"`
	TransferredSubscriptions int                      `json:"transferred_subscriptions"`
	Status                   CoordinatorHandoffStatus `json:"status"`
}

func canonicalRoleAddress(destName string) (string, bool, error) {
	raw := destName
	if strings.HasPrefix(raw, "agent/role/") {
		raw = strings.TrimPrefix(raw, "agent/")
	} else if !strings.HasPrefix(raw, "role/") {
		return "", false, nil
	}
	parts := strings.Split(raw, "/")
	if len(parts) != 3 || parts[0] != "role" || !workstreamNamePattern.MatchString(parts[1]) || !workstreamNamePattern.MatchString(parts[2]) {
		return "", true, failure("INVALID_REQUEST", "campaign role address must be role/<campaign>/<role>")
	}
	return raw, true, nil
}

func resolveCampaignRoleDestinationTx(ctx context.Context, tx pgx.Tx, repo, eventSensitivity string, allowLocal bool, origDest EventDestination) (EventDestination, *EventRouting, error) {
	if origDest.Type != "agent" {
		return origDest, nil, nil
	}
	roleAddr, isRole, err := canonicalRoleAddress(origDest.Name)
	if err != nil {
		return origDest, nil, err
	}
	if isRole {
		var campaign, holder, policy, campSens string
		var active bool
		err = tx.QueryRow(ctx, `SELECT c.campaign, b.holder_principal, c.out_of_chain_policy, c.sensitivity, c.active FROM cairn.campaign_role_binding b JOIN cairn.agent_team_campaign c USING(repo, campaign) WHERE b.repo=$1 AND b.role_address=$2 AND (c.sensitivity='shareable' OR $3)`,
			repo, roleAddr, allowLocal).Scan(&campaign, &holder, &policy, &campSens, &active)
		if errors.Is(err, pgx.ErrNoRows) || (err == nil && !active) {
			return origDest, nil, failure("NOT_FOUND", "campaign role address not found or inactive")
		}
		if err != nil {
			return origDest, nil, err
		}
		if campSens == "local" && eventSensitivity == "shareable" {
			return origDest, nil, failure("DESTINATION_PROHIBITED", "shareable event cannot address a local campaign role")
		}
		resolved := EventDestination{Type: "agent", Name: holder}
		origCopy := origDest
		return resolved, &EventRouting{
			Campaign:             campaign,
			RoleAddress:          roleAddr,
			Policy:               policy,
			OriginalDestination:  &origCopy,
			EffectiveDestination: resolved,
			LocalOnly:            campSens == "local",
		}, nil
	}
	// Forward deliveries addressed to a stopped handed-off campaign coordinator to the current holder of that role.
	var campaign, roleName, boundAddr, holder, policy, campSens string
	err = tx.QueryRow(ctx, `SELECT c.campaign, h.role_name, b.role_address, b.holder_principal, c.out_of_chain_policy, c.sensitivity
		FROM cairn.campaign_coordinator_handoff h
		JOIN cairn.agent_team_campaign c ON c.repo=h.repo AND c.campaign=h.campaign
		JOIN cairn.campaign_role_binding b ON b.repo=h.repo AND b.campaign=h.campaign AND b.role_name=h.role_name
		JOIN cairn.agent_session pred ON pred.agent_id=h.from_agent_id AND pred.repo=h.repo
		WHERE h.repo=$1 AND h.from_principal=$2 AND c.active
		  AND h.from_agent_id IS NOT NULL AND h.from_execution_id IS NOT NULL
		  AND pred.stopped AND pred.execution_id=h.from_execution_id
		  AND (c.sensitivity='shareable' OR ($3 AND $4='local'))
		ORDER BY h.created_at DESC LIMIT 1`,
		repo, origDest.Name, allowLocal, eventSensitivity).Scan(&campaign, &roleName, &boundAddr, &holder, &policy, &campSens)
	if errors.Is(err, pgx.ErrNoRows) || (err == nil && holder == origDest.Name) {
		return origDest, nil, nil
	}
	if err != nil {
		return origDest, nil, err
	}
	_ = roleName
	resolved := EventDestination{Type: "agent", Name: holder}
	origCopy := origDest
	return resolved, &EventRouting{
		Campaign:             campaign,
		RoleAddress:          boundAddr,
		Policy:               policy,
		OriginalDestination:  &origCopy,
		EffectiveDestination: resolved,
		LocalOnly:            campSens == "local",
	}, nil
}

func computeCoordinatorHandoffStatusTx(ctx context.Context, tx pgx.Tx, camp TeamCampaign, allowLocal bool) (CoordinatorHandoffStatus, *AgentInstance, error) {
	status := CoordinatorHandoffStatus{
		Repo:                      camp.Repo,
		Campaign:                  camp.Campaign,
		RoleAddress:               campaignRootRoleAddress(camp.Campaign),
		HolderPrincipal:           camp.RootAgent,
		MaxCoordinatorAgeSeconds:  camp.MaxCoordinatorAgeSeconds,
		MaxCoordinatorCompactions: camp.MaxCoordinatorCompactions,
		Reasons:                   []string{},
	}
	var predPtr *AgentInstance
	if strings.HasPrefix(camp.RootAgent, "agent/") {
		agentID := strings.TrimPrefix(camp.RootAgent, "agent/")
		if validID(agentID) == nil {
			a, err := scanAgentSession(tx.QueryRow(ctx, `SELECT `+agentSessionColumns+` FROM cairn.agent_session WHERE agent_id=$1 AND repo=$2 AND (visibility='hosted' OR $3)`, agentID, camp.Repo, allowLocal))
			if err == nil {
				predPtr = &a
				status.SessionFound = true
				status.AgentID = a.AgentID
				status.ExecutionID = a.ExecutionID
				status.Stopped = a.Stopped
				status.CompactionCount = a.CompactionCount
				var ageSec int64
				if err = tx.QueryRow(ctx, `SELECT GREATEST(0, FLOOR(EXTRACT(EPOCH FROM (clock_timestamp() - $1::timestamptz))))::bigint`, a.StartedAt).Scan(&ageSec); err != nil {
					return status, nil, err
				}
				status.AgeSeconds = ageSec
			} else if !errors.Is(err, pgx.ErrNoRows) {
				return status, nil, err
			}
		}
	}
	if status.SessionFound && camp.Active {
		if camp.MaxCoordinatorAgeSeconds != nil && status.AgeSeconds >= int64(*camp.MaxCoordinatorAgeSeconds) {
			status.ShouldHandoff = true
			status.Reasons = append(status.Reasons, fmt.Sprintf("coordinator session age (%ds) reached max_coordinator_age_seconds (%ds)", status.AgeSeconds, *camp.MaxCoordinatorAgeSeconds))
		}
		if camp.MaxCoordinatorCompactions != nil && status.CompactionCount >= *camp.MaxCoordinatorCompactions {
			status.ShouldHandoff = true
			status.Reasons = append(status.Reasons, fmt.Sprintf("coordinator compaction_count (%d) reached max_coordinator_compactions (%d)", status.CompactionCount, *camp.MaxCoordinatorCompactions))
		}
		if status.ShouldHandoff {
			status.ProposedCommand = fmt.Sprintf("cairn ledger handoff --campaign %s --new-binding coord-%s --new-native-session NEW_SESSION_ID", camp.Campaign, camp.Campaign)
		}
	}
	return status, predPtr, nil
}

func DeriveCampaignHandoffMarkdown(camp TeamCampaign, fromPrincipal, toPrincipal, reason, note string, status CoordinatorHandoffStatus, items []CampaignLedgerItem) string {
	var b strings.Builder
	fmt.Fprintf(&b, "Handoff: campaign %s (%s)\n\n", camp.Campaign, campaignRootRoleAddress(camp.Campaign))
	fmt.Fprintf(&b, "## Role Transfer\n- Campaign: `%s` (role inbox `%s`)\n- Predecessor: `%s` (age=%ds, compactions=%d)\n- Successor: `%s`\n- Reason: %s\n",
		camp.Campaign, campaignRootRoleAddress(camp.Campaign), fromPrincipal, status.AgeSeconds, status.CompactionCount, toPrincipal, reason)
	fmt.Fprintf(&b, "\n## Ledger Summary\n%s\n", DeriveCampaignAgentSummary(camp, toPrincipal, items))
	for _, section := range []struct {
		title string
		kind  string
	}{{"Active Assignments", "assignment"}, {"Open Decisions", "decision"}, {"Open Blockers", "blocker"}, {"Active Holds", "hold"}} {
		var lines []string
		for _, it := range items {
			if it.Kind == section.kind && isActiveLedgerItem(it) {
				lines = append(lines, fmt.Sprintf("- `%s` (`%s`, owner `%s`): %s", it.ItemKey, it.Status, it.Owner, it.Summary))
			}
		}
		if len(lines) > 0 {
			fmt.Fprintf(&b, "\n## %s\n%s\n", section.title, strings.Join(lines, "\n"))
		}
	}
	if strings.TrimSpace(note) != "" {
		fmt.Fprintf(&b, "\n## Coordinator Notes\n%s\n", strings.TrimSpace(note))
	}
	out := b.String()
	if len(out) > 60000 {
		prefix, _, _ := TruncateCleanUTF8(out, 60000)
		return prefix + "\n...(truncated)"
	}
	return out
}

func (s *Store) CheckCoordinatorHandoff(ctx context.Context, req CheckCoordinatorHandoffRequest, dest Destination) (CoordinatorHandoffStatus, error) {
	var empty CoordinatorHandoffStatus
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
	camp, err := readTeamCampaignTx(ctx, tx, repo, req.Campaign, dest.AllowLocal, false)
	if err != nil {
		return empty, err
	}
	status, _, err := computeCoordinatorHandoffStatusTx(ctx, tx, camp, dest.AllowLocal)
	return status, err
}

func (s *Store) RecordCoordinatorCompaction(ctx context.Context, req RecordCoordinatorCompactionRequest, dest Destination) (CoordinatorHandoffStatus, error) {
	var empty CoordinatorHandoffStatus
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
	input := struct {
		RecordCoordinatorCompactionRequest
		Destination string `json:"destination"`
		AllowLocal  bool   `json:"allow_local"`
	}{req, dest.Name, dest.AllowLocal}
	guard := func(tx pgx.Tx) error {
		if _, err := retrievalGeneration(ctx, tx); err != nil {
			return err
		}
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
	return mutate(ctx, s, "ledger-compaction", req.RequestID, input, func(tx pgx.Tx) (CoordinatorHandoffStatus, error) {
		if _, err := retrievalGeneration(ctx, tx); err != nil {
			return empty, err
		}
		if err := lock(ctx, tx, "agent-events:"+req.Repo); err != nil {
			return empty, err
		}
		camp, err := readTeamCampaignTx(ctx, tx, req.Repo, req.Campaign, dest.AllowLocal, false)
		if err != nil {
			return empty, err
		}
		if !camp.Active {
			return empty, failure("INVALID_REQUEST", "cannot record compaction on an inactive campaign")
		}
		_, predPtr, err := computeCoordinatorHandoffStatusTx(ctx, tx, camp, dest.AllowLocal)
		if err != nil {
			return empty, err
		}
		if predPtr == nil || predPtr.Stopped {
			return empty, failure("NOT_FOUND", "active registered coordinator session not found")
		}
		caller := s.channel.Principal
		if !s.channel.Operator && caller != camp.RootAgent && (s.session != nil || caller != predPtr.ProfileOwner) {
			return empty, failure("AUTHORITY_DENIED", "only the campaign coordinator session, its profile owner, or an operator can record coordinator compactions")
		}
		if s.session != nil && caller == camp.RootAgent {
			if _, err = currentAgentSession(ctx, tx, s.session.Ref, s.session.Profile, req.Repo, true, dest); err != nil {
				return empty, err
			}
		}
		if _, err = tx.Exec(ctx, `UPDATE cairn.agent_session SET compaction_count=compaction_count+1, context_revision=context_revision+1, last_seen=clock_timestamp(), expires_at=clock_timestamp()+interval '90 seconds' WHERE agent_id=$1`, predPtr.AgentID); err != nil {
			return empty, err
		}
		status, _, err := computeCoordinatorHandoffStatusTx(ctx, tx, camp, dest.AllowLocal)
		return status, err
	}, guard)
}

func (s *Store) HandoffCampaignCoordinator(ctx context.Context, req HandoffCampaignCoordinatorRequest, dest Destination) (CoordinatorHandoffResult, error) {
	var empty CoordinatorHandoffResult
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
	if (req.Successor == nil) == (req.NewSession == nil) {
		return empty, failure("INVALID_REQUEST", "specify exactly one of successor or new_session")
	}
	if req.Successor != nil {
		if err := req.Successor.Validate(); err != nil {
			return empty, err
		}
	}
	if req.NewSession != nil {
		if !eventName.MatchString(req.NewSession.Binding) || strings.TrimSpace(req.NewSession.NativeSessionID) == "" || len(req.NewSession.NativeSessionID) > 256 || strings.ContainsRune(req.NewSession.NativeSessionID, 0) {
			return empty, failure("INVALID_REQUEST", "new_session requires bounded binding and native_session_id")
		}
	}
	if err := ValidateEventSummary(req.Reason); err != nil {
		return empty, err
	}
	if req.Note != "" {
		if !utf8.ValidString(req.Note) || strings.ContainsRune(req.Note, 0) {
			return empty, failure("INVALID_REQUEST", "invalid UTF-8 text in handoff note")
		}
		if len(req.Note) > MaxCampaignHandoffNoteLen {
			return empty, failure("BUDGET_REFUSED", "handoff note exceeds 8192-byte limit")
		}
		if !req.Shareable && !dest.AllowLocal {
			return empty, failure("DESTINATION_PROHIBITED", "hosted destination requires shareable: true")
		}
	}
	callerSession := s.session
	view := *s
	view.session = nil
	input := struct {
		HandoffCampaignCoordinatorRequest
		Destination string `json:"destination"`
		AllowLocal  bool   `json:"allow_local"`
	}{req, dest.Name, dest.AllowLocal}
	guard := func(tx pgx.Tx) error {
		generation, err := retrievalGeneration(ctx, tx)
		if err != nil {
			return err
		}
		if err := lock(ctx, tx, "agent-events:"+req.Repo); err != nil {
			return err
		}
		var curSens string
		err = tx.QueryRow(ctx, `SELECT sensitivity FROM cairn.agent_team_campaign WHERE repo=$1 AND campaign=$2`, req.Repo, req.Campaign).Scan(&curSens)
		if err != nil && !errors.Is(err, pgx.ErrNoRows) {
			return err
		}
		if err == nil && curSens == "local" && !dest.AllowLocal {
			return failure("NOT_FOUND", "team campaign not found")
		}
		if callerSession != nil {
			curPred, cErr := currentAgentSession(ctx, tx, callerSession.Ref, callerSession.Profile, req.Repo, true, dest)
			if cErr != nil {
				return cErr
			}
			if curPred.Stopped {
				var replayed bool
				if err := tx.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM cairn.campaign_coordinator_handoff WHERE request_id=$1::uuid AND repo=$2 AND campaign=$3 AND from_agent_id=$4::uuid AND from_execution_id=$5::uuid AND database_generation=$6)`,
					req.RequestID, req.Repo, req.Campaign, callerSession.Ref.AgentID, callerSession.Ref.ExecutionID, generation).Scan(&replayed); err != nil {
					return err
				}
				if !replayed {
					return activeAgent(curPred)
				}
			}
		}
		return nil
	}
	return mutate(ctx, &view, "ledger-handoff", req.RequestID, input, func(tx pgx.Tx) (CoordinatorHandoffResult, error) {
		return view.handoffCampaignCoordinatorTx(ctx, tx, req, callerSession, dest)
	}, guard)
}

func (s *Store) resolveOrCreateSuccessorTx(ctx context.Context, tx pgx.Tx, req HandoffCampaignCoordinatorRequest, camp TeamCampaign, predPtr *AgentInstance, callerSession *agentSessionChannel, generation int64, dest Destination) (AgentInstance, string, string, error) {
	var empty AgentInstance
	callerOwner := s.channel.Principal
	if callerSession != nil {
		callerOwner = callerSession.Profile
	}
	if req.NewSession != nil {
		profileOwner := callerOwner
		if s.channel.Operator {
			if predPtr == nil {
				return empty, "", "", failure("INVALID_REQUEST", "operator new_session handoff requires an existing registered coordinator session or explicit successor")
			}
			profileOwner = predPtr.ProfileOwner
		}
		if err := lock(ctx, tx, "agent-session:"+req.Repo+":"+profileOwner+":"+req.NewSession.Binding+":"+req.NewSession.NativeSessionID); err != nil {
			return empty, "", "", err
		}
		var exists bool
		if err := tx.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM cairn.agent_session WHERE repo=$1 AND owner=$2 AND binding=$3 AND native_session_id=$4)`,
			req.Repo, profileOwner, req.NewSession.Binding, req.NewSession.NativeSessionID).Scan(&exists); err != nil {
			return empty, "", "", err
		}
		if exists {
			return empty, "", "", failure("VERSION_CONFLICT", "new_session native_session_id is already registered; supply a fresh native_session_id or pass successor")
		}
		var meta AgentMetadata
		if predPtr != nil {
			meta = predPtr.Metadata
			meta.State = "idle"
		}
		if req.NewSession.Metadata != nil {
			m := *req.NewSession.Metadata
			if m.Harness != "" {
				meta.Harness = m.Harness
			}
			if m.Model != "" {
				meta.Model = m.Model
			}
			if m.ObservedModel != "" {
				meta.ObservedModel = m.ObservedModel
			}
			if m.Project != "" {
				meta.Project = m.Project
			}
			if m.ProjectAliases != nil {
				meta.ProjectAliases = m.ProjectAliases
			}
			if m.Workspace != "" {
				meta.Workspace = m.Workspace
			}
			if m.TaskSummary != "" {
				meta.TaskSummary = m.TaskSummary
			}
			if m.State != "" {
				meta.State = m.State
			}
			if m.DeliveryMode != "" {
				meta.DeliveryMode = m.DeliveryMode
			}
		} else if predPtr == nil {
			return empty, "", "", failure("INVALID_REQUEST", "new_session.metadata is required when predecessor coordinator session is not registered")
		}
		if err := meta.validate(); err != nil {
			return empty, "", "", err
		}
		toVis := dest.Name
		if camp.Sensitivity == "local" && toVis != "local" {
			return empty, "", "", failure("DESTINATION_PROHIBITED", "local campaign coordinator cannot hand off to a hosted session")
		}
		created, err := scanAgentSession(tx.QueryRow(ctx, `INSERT INTO cairn.agent_session(agent_id,repo,owner,binding,native_session_id,visibility,execution_id,database_generation,metadata) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9) RETURNING `+agentSessionColumns,
			uuid.NewString(), req.Repo, profileOwner, req.NewSession.Binding, req.NewSession.NativeSessionID, toVis, uuid.NewString(), generation, meta))
		return created, toVis, req.NewSession.Binding, err
	}
	var toVis, toBinding string
	if err := tx.QueryRow(ctx, `SELECT visibility, binding FROM cairn.agent_session WHERE agent_id=$1 AND repo=$2 FOR UPDATE`, req.Successor.AgentID, req.Repo).Scan(&toVis, &toBinding); err != nil {
		if errors.Is(err, pgx.ErrNoRows) {
			return empty, "", "", failure("NOT_FOUND", "successor session not found in repository")
		}
		return empty, "", "", err
	}
	if toVis == "local" && !dest.AllowLocal {
		return empty, "", "", failure("NOT_FOUND", "successor session not found in repository")
	}
	if camp.Sensitivity == "local" && toVis != "local" {
		return empty, "", "", failure("DESTINATION_PROHIBITED", "local campaign coordinator cannot hand off to a hosted session")
	}
	succ, err := scanAgentSession(tx.QueryRow(ctx, `SELECT `+agentSessionColumns+` FROM cairn.agent_session WHERE agent_id=$1 AND repo=$2 FOR UPDATE`, req.Successor.AgentID, req.Repo))
	if err != nil {
		return empty, "", "", err
	}
	if !s.channel.Operator && succ.ProfileOwner != callerOwner {
		return empty, "", "", failure("AUTHORITY_DENIED", "successor session belongs to a different profile")
	}
	if succ.ExecutionID != req.Successor.ExecutionID || succ.DatabaseGeneration != generation || succ.Stopped {
		return empty, "", "", failure("STALE_SESSION", "successor session is stopped, replaced, or predates restore")
	}
	return succ, toVis, toBinding, nil
}

func (s *Store) transferCoordinatorStateTx(ctx context.Context, tx pgx.Tx, repo, campaign, oldPrincipal, newPrincipal, newAgentID, newExecID, newBinding, caller string, generation int64) (int, int, error) {
	if _, err := tx.Exec(ctx, `UPDATE cairn.agent_team_campaign SET root_agent=$3, revision=revision+1, updated_at=clock_timestamp() WHERE repo=$1 AND campaign=$2`, repo, campaign, newPrincipal); err != nil {
		return 0, 0, err
	}
	if _, err := tx.Exec(ctx, `UPDATE cairn.agent_team_member SET active=false, updated_at=clock_timestamp() WHERE repo=$1 AND campaign=$2 AND member=$3`, repo, campaign, oldPrincipal); err != nil {
		return 0, 0, err
	}
	if _, err := tx.Exec(ctx, `INSERT INTO cairn.agent_team_member(repo, campaign, member, lead, role, assigned_by, active, updated_at) VALUES($1,$2,$3,NULL,'root',$4,true,clock_timestamp()) ON CONFLICT(repo, campaign, member) DO UPDATE SET lead=NULL, role='root', assigned_by=$4, active=true, updated_at=clock_timestamp()`, repo, campaign, newPrincipal, caller); err != nil {
		return 0, 0, err
	}
	if _, err := tx.Exec(ctx, `UPDATE cairn.agent_team_member SET lead=$4, updated_at=clock_timestamp() WHERE repo=$1 AND campaign=$2 AND active AND lead=$3`, repo, campaign, oldPrincipal, newPrincipal); err != nil {
		return 0, 0, err
	}
	if _, err := tx.Exec(ctx, `UPDATE cairn.campaign_ledger_item SET owner=$4, revision=revision+1, updated_by=$5, updated_at=clock_timestamp() WHERE repo=$1 AND campaign=$2 AND owner=$3`, repo, campaign, oldPrincipal, newPrincipal, caller); err != nil {
		return 0, 0, err
	}
	if _, err := tx.Exec(ctx, `INSERT INTO cairn.campaign_role_binding(repo, campaign, role_name, role_address, holder_principal, revision, updated_at) VALUES($1,$2,'root',$3,$4,1,clock_timestamp()) ON CONFLICT(repo, campaign, role_name) DO UPDATE SET holder_principal=$4, revision=cairn.campaign_role_binding.revision+1, updated_at=clock_timestamp()`,
		repo, campaign, campaignRootRoleAddress(campaign), newPrincipal); err != nil {
		return 0, 0, err
	}
	if _, err := tx.Exec(ctx, `WITH dedup AS (
		UPDATE cairn.agent_delivery d SET state='ignored', code='superseded', completed_at=clock_timestamp(), lease_id=NULL, lease_until=NULL
		FROM cairn.agent_event e WHERE d.event_id=e.event_id AND e.repo=$1 AND d.consumer=$2
		  AND (d.state='pending' OR (d.state='leased' AND d.lease_until<=clock_timestamp()))
		  AND EXISTS(SELECT 1 FROM cairn.agent_delivery dup WHERE dup.event_id=d.event_id AND dup.consumer=$3)
		RETURNING d.delivery_id
	) UPDATE cairn.agent_closure_reservation r SET state='released', release_reason='stopped_expired', released_by=$4, revision=r.revision+1, transitioned_at=clock_timestamp()
	  FROM dedup WHERE r.request_delivery_id=dedup.delivery_id AND r.state='reserved'`, repo, oldPrincipal, newPrincipal, caller); err != nil {
		return 0, 0, err
	}
	delivRows, err := tx.Query(ctx, `UPDATE cairn.agent_delivery d SET consumer=$3, state='pending', lease_id=NULL, lease_until=NULL FROM cairn.agent_event e WHERE d.event_id=e.event_id AND e.repo=$1 AND d.consumer=$2 AND (d.state='pending' OR (d.state='leased' AND d.lease_until<=clock_timestamp())) RETURNING d.delivery_id::text`,
		repo, oldPrincipal, newPrincipal)
	if err != nil {
		return 0, 0, err
	}
	var movedIDs []string
	for delivRows.Next() {
		var did string
		if err = delivRows.Scan(&did); err != nil {
			delivRows.Close()
			return 0, 0, err
		}
		movedIDs = append(movedIDs, did)
	}
	delivRows.Close()
	if err = delivRows.Err(); err != nil {
		return 0, 0, err
	}
	transferredDeliveries := len(movedIDs)
	if len(movedIDs) > 0 {
		if _, err = tx.Exec(ctx, `UPDATE cairn.agent_closure_reservation SET expected_sender=$3, revision=revision+1, transitioned_at=clock_timestamp() WHERE repo=$1 AND state='reserved' AND expected_sender=$2 AND request_delivery_id=ANY($4::uuid[])`, repo, oldPrincipal, newPrincipal, movedIDs); err != nil {
			return 0, 0, err
		}
		if _, err = tx.Exec(ctx, `UPDATE cairn.agent_response_group_member m SET consumer=$2 WHERE m.delivery_id=ANY($3::uuid[]) AND m.consumer=$1 AND NOT EXISTS(SELECT 1 FROM cairn.agent_response_group_member dup WHERE dup.event_id=m.event_id AND dup.consumer=$2)`, oldPrincipal, newPrincipal, movedIDs); err != nil {
			return 0, 0, err
		}
	}
	if _, err := tx.Exec(ctx, `WITH dup_waits AS (
		UPDATE cairn.agent_wait w SET state='cancelled', resolved_at=clock_timestamp(), terminal_reason='coordinator_handoff_duplicate'
		WHERE w.collection=$1 AND w.owner_principal=$2 AND w.state='waiting'
		  AND EXISTS(SELECT 1 FROM cairn.agent_wait dup WHERE dup.collection=w.collection AND dup.owner_agent_uuid=$3::uuid AND dup.predicate_kind=w.predicate_kind AND dup.dependency_event_uuid=w.dependency_event_uuid AND dup.state='waiting')
		RETURNING w.wait_uuid
	) UPDATE cairn.agent_closure_reservation r SET state='released', release_reason='wait_cancelled', released_by=$4, revision=r.revision+1, transitioned_at=clock_timestamp()
	  FROM dup_waits WHERE r.wait_uuid=dup_waits.wait_uuid AND r.state='reserved'`, repo, oldPrincipal, newAgentID, caller); err != nil {
		return 0, 0, err
	}
	if _, err := tx.Exec(ctx, `UPDATE cairn.agent_wait w SET owner_principal=$3, owner_agent_uuid=$4::uuid, creation_execution_uuid=$5::uuid, profile_binding_id=$6 WHERE w.collection=$1 AND w.owner_principal=$2 AND (w.state='waiting' OR EXISTS(SELECT 1 FROM cairn.agent_wait_outbox o WHERE o.wait_uuid=w.wait_uuid AND o.state='pending'))`,
		repo, oldPrincipal, newPrincipal, newAgentID, newExecID, newBinding); err != nil {
		return 0, 0, err
	}
	if _, err := tx.Exec(ctx, `UPDATE cairn.agent_closure_reservation SET recipient=$3, revision=revision+1, transitioned_at=clock_timestamp() WHERE repo=$1 AND recipient=$2 AND state='reserved'`, repo, oldPrincipal, newPrincipal); err != nil {
		return 0, 0, err
	}

	supportedOrganic, err := s.recipientSupportsNextOrganicTurnTx(ctx, tx, repo, newPrincipal)
	if err != nil {
		return 0, 0, err
	}
	if supportedOrganic {
		oTag, oErr := tx.Exec(ctx, `UPDATE cairn.agent_organic_notice SET recipient_agent_id=$3::uuid, recipient_inbox=$4 WHERE repo=$1 AND recipient_inbox=$2 AND state='queued' AND expires_at>clock_timestamp() AND restore_generation=$5`, repo, oldPrincipal, newAgentID, newPrincipal, generation)
		if oErr != nil {
			return 0, 0, oErr
		}
		transferredDeliveries += int(oTag.RowsAffected())
	} else {
		if _, oErr := tx.Exec(ctx, `UPDATE cairn.agent_organic_notice q SET state='unavailable', unavailable_reason='superseded' WHERE q.repo=$1 AND q.recipient_inbox=$2 AND q.state='queued' AND q.expires_at>clock_timestamp() AND q.restore_generation=$4 AND EXISTS(SELECT 1 FROM cairn.agent_delivery dup WHERE dup.event_id=q.event_id AND dup.consumer=$3)`, repo, oldPrincipal, newPrincipal, generation); oErr != nil {
			return 0, 0, oErr
		}
		oTag, oErr := tx.Exec(ctx, `WITH converted AS (UPDATE cairn.agent_organic_notice SET state='unavailable', unavailable_reason='coordinator_handoff' WHERE repo=$1 AND recipient_inbox=$2 AND state='queued' AND expires_at>clock_timestamp() AND restore_generation=$4 RETURNING reserved_delivery_id, event_id) INSERT INTO cairn.agent_delivery(delivery_id, event_id, consumer) SELECT reserved_delivery_id, event_id, $3 FROM converted`, repo, oldPrincipal, newPrincipal, generation)
		if oErr != nil {
			return 0, 0, oErr
		}
		transferredDeliveries += int(oTag.RowsAffected())
	}

	subRows, err := tx.Query(ctx, `SELECT topic, filter FROM cairn.agent_subscription WHERE repo=$1 AND consumer=$2 AND active`, repo, oldPrincipal)
	if err != nil {
		return 0, 0, err
	}
	type subEntry struct {
		topic  string
		filter []byte
	}
	var subs []subEntry
	for subRows.Next() {
		var se subEntry
		if err = subRows.Scan(&se.topic, &se.filter); err != nil {
			subRows.Close()
			return 0, 0, err
		}
		subs = append(subs, se)
	}
	subRows.Close()
	if err = subRows.Err(); err != nil {
		return 0, 0, err
	}
	for _, se := range subs {
		if _, err = tx.Exec(ctx, `INSERT INTO cairn.agent_subscription_change(repo, consumer, topic, active, filter) VALUES($1,$2,$3,false,NULL),($1,$4,$3,true,$5::jsonb)`, repo, oldPrincipal, se.topic, newPrincipal, se.filter); err != nil {
			return 0, 0, err
		}
		if _, err = tx.Exec(ctx, `UPDATE cairn.agent_subscription SET active=false, filter=NULL, changed_at=clock_timestamp() WHERE repo=$1 AND consumer=$2 AND topic=$3`, repo, oldPrincipal, se.topic); err != nil {
			return 0, 0, err
		}
		if _, err = tx.Exec(ctx, `INSERT INTO cairn.agent_subscription(repo, consumer, topic, active, filter, changed_at) VALUES($1,$2,$3,true,$4::jsonb,clock_timestamp()) ON CONFLICT(repo, consumer, topic) DO UPDATE SET active=true, filter=excluded.filter, changed_at=clock_timestamp()`, repo, newPrincipal, se.topic, se.filter); err != nil {
			return 0, 0, err
		}
	}
	return transferredDeliveries, len(subs), nil
}

func (s *Store) handoffCampaignCoordinatorTx(ctx context.Context, tx pgx.Tx, req HandoffCampaignCoordinatorRequest, callerSession *agentSessionChannel, dest Destination) (CoordinatorHandoffResult, error) {
	var empty CoordinatorHandoffResult
	generation, err := retrievalGeneration(ctx, tx)
	if err != nil {
		return empty, err
	}
	if err := lock(ctx, tx, "agent-events:"+req.Repo); err != nil {
		return empty, err
	}
	camp, err := readTeamCampaignTx(ctx, tx, req.Repo, req.Campaign, dest.AllowLocal, true)
	if err != nil {
		return empty, err
	}
	if !camp.Active {
		return empty, failure("INVALID_REQUEST", "cannot hand off coordinator of an inactive campaign")
	}
	oldPrincipal := camp.RootAgent
	if strings.HasPrefix(oldPrincipal, "agent/") {
		oldAgentID := strings.TrimPrefix(oldPrincipal, "agent/")
		if validID(oldAgentID) == nil {
			var predVis string
			err = tx.QueryRow(ctx, `SELECT visibility FROM cairn.agent_session WHERE agent_id=$1 AND repo=$2 FOR UPDATE`, oldAgentID, req.Repo).Scan(&predVis)
			if err == nil && predVis == "local" && !dest.AllowLocal {
				return empty, failure("AUTHORITY_DENIED", "local coordinator session cannot be handed off from a hosted destination")
			} else if err != nil && !errors.Is(err, pgx.ErrNoRows) {
				return empty, err
			}
		}
	}
	status, predPtr, err := computeCoordinatorHandoffStatusTx(ctx, tx, camp, dest.AllowLocal)
	if err != nil {
		return empty, err
	}
	caller := s.channel.Principal
	if !s.channel.Operator && caller != oldPrincipal && (callerSession != nil || predPtr == nil || caller != predPtr.ProfileOwner) {
		return empty, failure("AUTHORITY_DENIED", "only the campaign coordinator session, its profile owner, or an operator can perform a coordinator handoff")
	}
	if callerSession != nil {
		curPred, cErr := currentAgentSession(ctx, tx, callerSession.Ref, callerSession.Profile, req.Repo, true, dest)
		if cErr == nil {
			cErr = activeAgent(curPred)
		}
		if cErr != nil {
			return empty, cErr
		}
	}
	if req.Note != "" && camp.Sensitivity == "shareable" && !req.Shareable {
		return empty, failure("DESTINATION_PROHIBITED", "freeform handoff note in a shareable campaign requires shareable: true")
	}
	if req.OnlyIfThresholdReached && !status.ShouldHandoff {
		return CoordinatorHandoffResult{
			Performed: false, Campaign: camp, RoleAddress: camp.RoleAddress, FromPrincipal: oldPrincipal, Status: status,
		}, nil
	}

	var busy bool
	var oldAgentUUID *string
	if predPtr != nil {
		oldAgentUUID = &predPtr.AgentID
	}
	if err = tx.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM cairn.agent_delivery d JOIN cairn.agent_event e USING(event_id) WHERE e.repo=$1 AND d.consumer=$2 AND d.state='leased' AND d.lease_until>clock_timestamp())
		OR EXISTS(SELECT 1 FROM cairn.agent_wake_attempt WHERE repo=$1 AND (consumer=$2 OR ($3::uuid IS NOT NULL AND agent_id=$3::uuid)) AND finished_at IS NULL)
		OR ($3::uuid IS NOT NULL AND EXISTS(SELECT 1 FROM cairn.agent_session_attempt WHERE agent_id=$3::uuid AND finished_at IS NULL))`,
		req.Repo, oldPrincipal, oldAgentUUID).Scan(&busy); err != nil {
		return empty, err
	}
	if busy {
		return empty, failure("AGENT_BUSY", "finish or release active leased deliveries before coordinator handoff")
	}

	toAgent, toVis, toBinding, err := s.resolveOrCreateSuccessorTx(ctx, tx, req, camp, predPtr, callerSession, generation, dest)
	if err != nil {
		return empty, err
	}
	if toAgent.Inbox == oldPrincipal {
		return empty, failure("INVALID_REQUEST", "successor session must be distinct from predecessor coordinator")
	}
	if err = ensurePrincipalFreeInOtherCampaignsTx(ctx, tx, req.Repo, req.Campaign, toAgent.Inbox); err != nil {
		return empty, err
	}
	if toVis != "local" {
		var hasLocalPending bool
		if err = tx.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM cairn.agent_delivery d JOIN cairn.agent_event e USING(event_id) WHERE e.repo=$1 AND d.consumer=$2 AND e.sensitivity='local' AND (d.state='pending' OR (d.state='leased' AND d.lease_until<=clock_timestamp())))
			OR EXISTS(SELECT 1 FROM cairn.agent_organic_notice q JOIN cairn.agent_event e USING(event_id) WHERE q.repo=$1 AND q.recipient_inbox=$2 AND q.state='queued' AND q.expires_at>clock_timestamp() AND q.restore_generation=$3 AND e.sensitivity='local')
			OR EXISTS(SELECT 1 FROM cairn.agent_wait w WHERE w.collection=$1 AND w.owner_principal=$2 AND w.sensitivity_ceiling='local' AND (w.state='waiting' OR EXISTS(SELECT 1 FROM cairn.agent_wait_outbox o WHERE o.wait_uuid=w.wait_uuid AND o.state='pending')))`,
			req.Repo, oldPrincipal, generation).Scan(&hasLocalPending); err != nil {
			return empty, err
		}
		if hasLocalPending {
			return empty, failure("DESTINATION_PROHIBITED", "cannot transfer local inbox deliveries to a hosted successor session")
		}
	}

	reason := strings.TrimSpace(req.Reason)
	if reason == "" {
		if len(status.Reasons) > 0 {
			reason = strings.Join(status.Reasons, "; ")
		} else {
			reason = "explicit coordinator handoff"
		}
	}
	delivCount, subCount, err := s.transferCoordinatorStateTx(ctx, tx, req.Repo, req.Campaign, oldPrincipal, toAgent.Inbox, toAgent.AgentID, toAgent.ExecutionID, toBinding, caller, generation)
	if err != nil {
		return empty, err
	}
	updatedCamp, err := readTeamCampaignTx(ctx, tx, req.Repo, req.Campaign, dest.AllowLocal, true)
	if err != nil {
		return empty, err
	}
	updatedItems, err := loadCampaignLedgerItemsTx(ctx, tx, req.Repo, req.Campaign, dest.AllowLocal)
	if err != nil {
		return empty, err
	}
	hRecID := uuid.NewString()
	hDraft := Draft{
		Body:        DeriveCampaignHandoffMarkdown(updatedCamp, oldPrincipal, toAgent.Inbox, reason, req.Note, status, updatedItems),
		Kind:        "note",
		ClaimType:   "self",
		Sensitivity: camp.Sensitivity,
		Scope:       Scope{Repo: req.Repo, TaskID: "campaign/" + req.Campaign, RunID: "*"},
	}
	if err = hDraft.validate(); err != nil {
		return empty, err
	}
	if _, err = tx.Exec(ctx, `INSERT INTO cairn.memory_record(record_id,current_version,sensitivity) VALUES($1,1,$2)`, hRecID, camp.Sensitivity); err != nil {
		return empty, err
	}
	hRec, err := insertVersion(ctx, tx, hRecID, 1, hDraft)
	if err != nil {
		return empty, err
	}
	hRef := RecordVersionRef{RecordID: hRec.RecordID, Version: hRec.Version}
	if _, err = tx.Exec(ctx, `UPDATE cairn.campaign_role_binding SET handoff_record_id=$3::uuid, handoff_version=$4 WHERE repo=$1 AND campaign=$2 AND role_name='root'`, req.Repo, req.Campaign, hRef.RecordID, hRef.Version); err != nil {
		return empty, err
	}
	var fromExecUUID *string
	if predPtr != nil {
		fromExecUUID = &predPtr.ExecutionID
		if _, err = tx.Exec(ctx, `UPDATE cairn.agent_session SET stopped=true, expires_at=clock_timestamp(), context_revision=context_revision+1 WHERE agent_id=$1`, predPtr.AgentID); err != nil {
			return empty, err
		}
	}
	if err = syncCampaignAgentSummariesTx(ctx, tx, updatedCamp, updatedItems); err != nil {
		return empty, err
	}
	refreshedTo, err := scanAgentSession(tx.QueryRow(ctx, `SELECT `+agentSessionColumns+` FROM cairn.agent_session WHERE agent_id=$1`, toAgent.AgentID))
	if err != nil {
		return empty, err
	}
	handoffID := uuid.NewString()
	if _, err = tx.Exec(ctx, `INSERT INTO cairn.campaign_coordinator_handoff(handoff_id, request_id, repo, campaign, role_name, role_address, from_principal, from_agent_id, from_execution_id, to_principal, to_agent_id, to_execution_id, handoff_record_id, handoff_version, reason, transferred_deliveries, transferred_subscriptions, database_generation) VALUES($1::uuid,$2::uuid,$3,$4,'root',$5,$6,$7::uuid,$8::uuid,$9,$10::uuid,$11::uuid,$12::uuid,$13,$14,$15,$16,$17)`,
		handoffID, req.RequestID, req.Repo, req.Campaign, updatedCamp.RoleAddress, oldPrincipal, oldAgentUUID, fromExecUUID, refreshedTo.Inbox, refreshedTo.AgentID, refreshedTo.ExecutionID, hRef.RecordID, hRef.Version, reason, delivCount, subCount, generation); err != nil {
		return empty, err
	}
	return CoordinatorHandoffResult{
		Performed:                true,
		HandoffID:                handoffID,
		Campaign:                 updatedCamp,
		RoleAddress:              updatedCamp.RoleAddress,
		FromPrincipal:            oldPrincipal,
		ToPrincipal:              refreshedTo.Inbox,
		ToSession:                &refreshedTo,
		Handoff:                  &hRef,
		TransferredDeliveries:    delivCount,
		TransferredSubscriptions: subCount,
		Status:                   status,
	}, nil
}
