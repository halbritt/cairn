package core

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
)

const (
	CampaignLedgerSchema       = "cairn.campaign-ledger/1"
	MaxCampaignLedgerItems     = 512
	MaxCampaignLedgerDetailLen = 2048
)

type CampaignLedgerItem struct {
	Repo       string            `json:"repo"`
	Campaign   string            `json:"campaign"`
	Kind       string            `json:"kind"`
	ItemKey    string            `json:"item_key"`
	Owner      string            `json:"owner"`
	Status     string            `json:"status"`
	Summary    string            `json:"summary"`
	Detail     string            `json:"detail,omitempty"`
	Source     *RecordVersionRef `json:"source,omitempty"`
	EventID    string            `json:"event_id,omitempty"`
	DeliveryID string            `json:"delivery_id,omitempty"`
	Result     *RecordVersionRef `json:"result,omitempty"`
	DueAt      *time.Time        `json:"due_at,omitempty"`
	Revision   int64             `json:"revision"`
	UpdatedBy  string            `json:"updated_by"`
	CreatedAt  time.Time         `json:"created_at"`
	UpdatedAt  time.Time         `json:"updated_at"`
}

type SetCampaignLedgerItemRequest struct {
	RequestID        string            `json:"request_id"`
	Repo             string            `json:"repo,omitempty"`
	Campaign         string            `json:"campaign"`
	Kind             string            `json:"kind"`
	ItemKey          string            `json:"item_key"`
	Owner            string            `json:"owner,omitempty"`
	Status           string            `json:"status,omitempty"`
	Summary          string            `json:"summary,omitempty"`
	Detail           *string           `json:"detail,omitempty"`
	Source           *RecordVersionRef `json:"source,omitempty"`
	EventID          string            `json:"event_id,omitempty"`
	DeliveryID       string            `json:"delivery_id,omitempty"`
	Result           *RecordVersionRef `json:"result,omitempty"`
	DueAt            *time.Time        `json:"due_at,omitempty"`
	ExpectedRevision int64             `json:"expected_revision,omitempty"`
}

type CampaignOperationEntry struct {
	Repo            string            `json:"repo"`
	Campaign        string            `json:"campaign"`
	Caller          string            `json:"caller"`
	OpKey           string            `json:"op_key"`
	Operation       string            `json:"operation"`
	State           string            `json:"state"`
	SourceRequestID string            `json:"source_request_id,omitempty"`
	ActionRequestID string            `json:"action_request_id"`
	ReplyRequestID  string            `json:"reply_request_id,omitempty"`
	Source          *RecordVersionRef `json:"source,omitempty"`
	DeliveryID      string            `json:"delivery_id,omitempty"`
	EventID         string            `json:"event_id,omitempty"`
	ReplyEventID    string            `json:"reply_event_id,omitempty"`
	CreatedAt       time.Time         `json:"created_at"`
	UpdatedAt       time.Time         `json:"updated_at"`
}

type CampaignLedgerCounts struct {
	ActiveAssignments    int `json:"active_assignments"`
	CompletedAssignments int `json:"completed_assignments"`
	OpenDecisions        int `json:"open_decisions"`
	OpenBlockers         int `json:"open_blockers"`
	ActiveHolds          int `json:"active_holds"`
}

type CampaignLedgerSnapshot struct {
	Schema                string                   `json:"schema"`
	Campaign              TeamCampaign             `json:"campaign"`
	DerivedContextSummary string                   `json:"derived_context_summary"`
	Counts                CampaignLedgerCounts     `json:"counts"`
	Assignments           []CampaignLedgerItem     `json:"assignments"`
	Decisions             []CampaignLedgerItem     `json:"decisions"`
	Blockers              []CampaignLedgerItem     `json:"blockers"`
	Holds                 []CampaignLedgerItem     `json:"holds"`
	Operations            []CampaignOperationEntry `json:"operations"`
}

type GetCampaignLedgerRequest struct {
	Repo     string `json:"repo,omitempty"`
	Campaign string `json:"campaign"`
	Kind     string `json:"kind,omitempty"`
	Status   string `json:"status,omitempty"`
	Owner    string `json:"owner,omitempty"`
	OpLimit  int    `json:"op_limit,omitempty"`
}

type SyncCampaignContextRequest struct {
	RequestID string           `json:"request_id"`
	Repo      string           `json:"repo,omitempty"`
	Campaign  string           `json:"campaign"`
	Session   *AgentSessionRef `json:"session,omitempty"`
}

type SyncCampaignContextResult struct {
	Repo                  string         `json:"repo"`
	Campaign              string         `json:"campaign"`
	Principal             string         `json:"principal"`
	DerivedContextSummary string         `json:"derived_context_summary"`
	Agent                 *AgentInstance `json:"agent,omitempty"`
}

func validLedgerKindAndStatus(kind, status string) bool {
	switch kind {
	case "assignment":
		return status == "planned" || status == "dispatched" || status == "in_progress" || status == "review" || status == "completed" || status == "cancelled"
	case "decision":
		return status == "open" || status == "settled" || status == "superseded"
	case "blocker":
		return status == "open" || status == "escalated" || status == "resolved"
	case "hold":
		return status == "active" || status == "released"
	default:
		return false
	}
}

func isActiveLedgerItem(item CampaignLedgerItem) bool {
	switch item.Kind {
	case "assignment":
		return item.Status != "completed" && item.Status != "cancelled"
	case "decision":
		return item.Status == "open"
	case "blocker":
		return item.Status == "open" || item.Status == "escalated"
	case "hold":
		return item.Status == "active"
	default:
		return false
	}
}

func computeLedgerCounts(items []CampaignLedgerItem) CampaignLedgerCounts {
	var c CampaignLedgerCounts
	for _, it := range items {
		switch it.Kind {
		case "assignment":
			if it.Status == "completed" {
				c.CompletedAssignments++
			} else if it.Status != "cancelled" {
				c.ActiveAssignments++
			}
		case "decision":
			if it.Status == "open" {
				c.OpenDecisions++
			}
		case "blocker":
			if it.Status == "open" || it.Status == "escalated" {
				c.OpenBlockers++
			}
		case "hold":
			if it.Status == "active" {
				c.ActiveHolds++
			}
		}
	}
	return c
}

// DeriveCampaignAgentSummary computes a deterministic bounded (<= 1024 byte)
// task summary from the hosted campaign ledger for the given principal.
func DeriveCampaignAgentSummary(camp TeamCampaign, principal string, items []CampaignLedgerItem) string {
	counts := computeLedgerCounts(items)
	role := "observer"
	lead := ""
	if principal == camp.RootAgent {
		role = "root"
	} else {
		for _, m := range camp.Members {
			if m.Member == principal && m.Active {
				role = m.Role
				lead = m.Lead
				break
			}
		}
	}
	roleDesc := role
	if lead != "" {
		roleDesc = role + ", lead=" + lead
	}
	header := fmt.Sprintf("campaign %s [%s]: %d active assignments (%d completed), %d open blockers, %d open decisions, %d active holds",
		camp.Campaign, roleDesc, counts.ActiveAssignments, counts.CompletedAssignments, counts.OpenBlockers, counts.OpenDecisions, counts.ActiveHolds)

	nodes := make(map[string]teamNode, len(camp.Members))
	for _, m := range camp.Members {
		if m.Active {
			nodes[m.Member] = teamNode{lead: m.Lead, role: m.Role}
		}
	}
	relevant := func(it CampaignLedgerItem) bool {
		if !isActiveLedgerItem(it) {
			return false
		}
		if role == "root" || role == "observer" {
			return true
		}
		if role == "lead" {
			return isAncestorOrSelf(principal, it.Owner, nodes)
		}
		return it.Owner == principal
	}

	var highlights []string
	for _, wantKind := range []string{"blocker", "decision", "hold", "assignment"} {
		for _, it := range items {
			if it.Kind != wantKind || !relevant(it) {
				continue
			}
			highlights = append(highlights, fmt.Sprintf("%s:%s(%s->%s) %s", it.Kind, it.ItemKey, it.Owner, it.Status, it.Summary))
			if len(highlights) >= 6 {
				break
			}
		}
		if len(highlights) >= 6 {
			break
		}
	}
	full := header
	if len(highlights) > 0 {
		full += " | " + strings.Join(highlights, "; ")
	}
	if len(full) <= 1024 {
		return full
	}
	prefix, _, _ := TruncateCleanUTF8(full, 1020)
	return prefix + "..."
}

const campaignLedgerItemColumns = `repo, campaign, kind, item_key, owner, status, summary, detail, source_record_id::text, source_version, COALESCE(event_id::text, ''), COALESCE(delivery_id::text, ''), result_record_id::text, result_version, due_at, revision, updated_by, created_at, updated_at`

func scanCampaignLedgerItem(row pgx.Row) (CampaignLedgerItem, error) {
	var it CampaignLedgerItem
	var srcID, resID *string
	var srcVer, resVer *int
	err := row.Scan(
		&it.Repo, &it.Campaign, &it.Kind, &it.ItemKey, &it.Owner, &it.Status, &it.Summary, &it.Detail,
		&srcID, &srcVer, &it.EventID, &it.DeliveryID, &resID, &resVer, &it.DueAt,
		&it.Revision, &it.UpdatedBy, &it.CreatedAt, &it.UpdatedAt,
	)
	if srcID != nil && srcVer != nil {
		it.Source = &RecordVersionRef{RecordID: *srcID, Version: *srcVer}
	}
	if resID != nil && resVer != nil {
		it.Result = &RecordVersionRef{RecordID: *resID, Version: *resVer}
	}
	return it, err
}

func validAnyLedgerStatus(status string) bool {
	return validLedgerKindAndStatus("assignment", status) ||
		validLedgerKindAndStatus("decision", status) ||
		validLedgerKindAndStatus("blocker", status) ||
		validLedgerKindAndStatus("hold", status)
}

func loadCampaignLedgerItemsTx(ctx context.Context, tx pgx.Tx, repo, campaign string, allowLocal bool) ([]CampaignLedgerItem, error) {
	rows, err := tx.Query(ctx, `SELECT i.repo, i.campaign, i.kind, i.item_key, i.owner, i.status, i.summary, i.detail,
		CASE WHEN sm.record_id IS NOT NULL AND sm.lifecycle='active' AND sv.payload_deleted_by IS NULL AND (sm.sensitivity='shareable' OR $3) THEN i.source_record_id::text END,
		CASE WHEN sm.record_id IS NOT NULL AND sm.lifecycle='active' AND sv.payload_deleted_by IS NULL AND (sm.sensitivity='shareable' OR $3) THEN i.source_version END,
		COALESCE(CASE WHEN ev.event_id IS NOT NULL AND (ev.sensitivity='shareable' OR $3) THEN i.event_id::text END, ''),
		COALESCE(CASE WHEN de.event_id IS NOT NULL AND (de.sensitivity='shareable' OR $3) THEN i.delivery_id::text END, ''),
		CASE WHEN rm.record_id IS NOT NULL AND rm.lifecycle='active' AND rv.payload_deleted_by IS NULL AND (rm.sensitivity='shareable' OR $3) THEN i.result_record_id::text END,
		CASE WHEN rm.record_id IS NOT NULL AND rm.lifecycle='active' AND rv.payload_deleted_by IS NULL AND (rm.sensitivity='shareable' OR $3) THEN i.result_version END,
		i.due_at, i.revision, i.updated_by, i.created_at, i.updated_at
		FROM cairn.campaign_ledger_item i
		LEFT JOIN cairn.memory_record sm ON sm.record_id = i.source_record_id
		LEFT JOIN cairn.record_version sv ON sv.record_id = i.source_record_id AND sv.version = i.source_version
		LEFT JOIN cairn.agent_event ev ON ev.event_id = i.event_id
		LEFT JOIN cairn.agent_delivery dl ON dl.delivery_id = i.delivery_id
		LEFT JOIN cairn.agent_event de ON de.event_id = dl.event_id
		LEFT JOIN cairn.memory_record rm ON rm.record_id = i.result_record_id
		LEFT JOIN cairn.record_version rv ON rv.record_id = i.result_record_id AND rv.version = i.result_version
		WHERE i.repo=$1 AND i.campaign=$2 ORDER BY i.kind, i.item_key`, repo, campaign, allowLocal)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []CampaignLedgerItem{}
	for rows.Next() {
		it, sErr := scanCampaignLedgerItem(rows)
		if sErr != nil {
			return nil, sErr
		}
		out = append(out, it)
	}
	return out, rows.Err()
}

func checkLedgerRefSensitivityTx(ctx context.Context, tx pgx.Tx, repo, campSens string, allowLocal bool, ref *RecordVersionRef, label string) error {
	if ref == nil {
		return nil
	}
	if validID(ref.RecordID) != nil || ref.Version < 1 {
		return failure("INVALID_REQUEST", "invalid "+label+" record version reference")
	}
	var recSens string
	err := tx.QueryRow(ctx, `SELECT m.sensitivity FROM cairn.memory_record m JOIN cairn.record_version v USING(record_id) WHERE m.record_id=$1 AND v.version=$2 AND v.repo=$3 AND m.lifecycle='active' AND v.payload_deleted_by IS NULL AND (m.sensitivity='shareable' OR $4)`, ref.RecordID, ref.Version, repo, allowLocal).Scan(&recSens)
	if errors.Is(err, pgx.ErrNoRows) {
		return failure("NOT_FOUND", label+" record version unavailable")
	}
	if err != nil {
		return err
	}
	if campSens == "shareable" && recSens == "local" {
		return failure("DESTINATION_PROHIBITED", "shareable campaign ledger cannot reference a local "+label+" record")
	}
	return nil
}

func syncCampaignAgentSummariesTx(ctx context.Context, tx pgx.Tx, camp TeamCampaign, items []CampaignLedgerItem) error {
	if !camp.Active {
		return nil
	}
	members := make([]string, 0, len(camp.Members)+1)
	members = append(members, camp.RootAgent)
	for _, m := range camp.Members {
		if m.Active {
			members = append(members, m.Member)
		}
	}
	seen := map[string]bool{}
	for _, p := range members {
		if !strings.HasPrefix(p, "agent/") || seen[p] {
			continue
		}
		seen[p] = true
		agentID := strings.TrimPrefix(p, "agent/")
		if validID(agentID) != nil {
			continue
		}
		summary := DeriveCampaignAgentSummary(camp, p, items)
		_, err := tx.Exec(ctx, `UPDATE cairn.agent_session SET metadata = jsonb_set(metadata, '{task_summary}', to_jsonb($2::text), true), context_revision = context_revision + 1 WHERE agent_id = $1 AND repo = $3 AND NOT stopped AND ($4 = 'shareable' OR visibility = 'local')`,
			agentID, summary, camp.Repo, camp.Sensitivity)
		if err != nil {
			return err
		}
	}
	return nil
}

func (s *Store) SetCampaignLedgerItem(ctx context.Context, req SetCampaignLedgerItemRequest, dest Destination) (CampaignLedgerItem, error) {
	var empty CampaignLedgerItem
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
	if req.Kind != "assignment" && req.Kind != "decision" && req.Kind != "blocker" && req.Kind != "hold" {
		return empty, failure("INVALID_REQUEST", "kind must be assignment, decision, blocker or hold")
	}
	if !noticeSupersedesKey.MatchString(req.ItemKey) {
		return empty, failure("INVALID_REQUEST", "item_key must be 1..128 ASCII letters, digits or _.:/-")
	}
	if req.Owner != "" && !validTeamPrincipal(req.Owner) {
		return empty, failure("INVALID_REQUEST", "invalid owner principal")
	}
	if req.Status != "" && !validLedgerKindAndStatus(req.Kind, req.Status) {
		return empty, failure("INVALID_REQUEST", "invalid status for ledger item kind "+req.Kind)
	}
	if err := ValidateEventSummary(req.Summary); err != nil {
		return empty, err
	}
	if req.Detail != nil {
		if !utf8.ValidString(*req.Detail) || strings.ContainsRune(*req.Detail, 0) {
			return empty, failure("INVALID_REQUEST", "invalid UTF-8 text in detail")
		}
		if len(*req.Detail) > MaxCampaignLedgerDetailLen {
			return empty, failure("BUDGET_REFUSED", "detail exceeds 2048-byte limit")
		}
	}
	if req.EventID != "" && validID(req.EventID) != nil {
		return empty, failure("INVALID_REQUEST", "event_id must be a UUID")
	}
	if req.DeliveryID != "" && validID(req.DeliveryID) != nil {
		return empty, failure("INVALID_REQUEST", "delivery_id must be a UUID")
	}
	if req.DueAt != nil && (req.DueAt.IsZero() || req.DueAt.Year() < 1 || req.DueAt.Year() > 9999) {
		return empty, failure("INVALID_REQUEST", "due_at must be a valid timestamp")
	}
	if req.ExpectedRevision < 0 {
		return empty, failure("INVALID_REQUEST", "expected_revision must be nonnegative")
	}
	input := struct {
		SetCampaignLedgerItemRequest
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
	return mutate(ctx, s, "ledger-item-set", req.RequestID, input, func(tx pgx.Tx) (CampaignLedgerItem, error) {
		if _, err := retrievalGeneration(ctx, tx); err != nil {
			return empty, err
		}
		if err := lock(ctx, tx, "agent-events:"+req.Repo); err != nil {
			return empty, err
		}
		return s.upsertCampaignLedgerItemTx(ctx, tx, req, dest)
	}, guard)
}

func (s *Store) upsertCampaignLedgerItemTx(ctx context.Context, tx pgx.Tx, req SetCampaignLedgerItemRequest, dest Destination) (CampaignLedgerItem, error) {
	var empty CampaignLedgerItem
	camp, err := readTeamCampaignTx(ctx, tx, req.Repo, req.Campaign, dest.AllowLocal, true)
	if err != nil {
		return empty, err
	}
	if !camp.Active {
		return empty, failure("INVALID_REQUEST", "cannot modify ledger of an inactive campaign")
	}
	nodes := make(map[string]teamNode, len(camp.Members))
	for _, m := range camp.Members {
		if m.Active {
			nodes[m.Member] = teamNode{lead: m.Lead, role: m.Role}
		}
	}
	caller := s.channel.Principal
	existing, err := scanCampaignLedgerItem(tx.QueryRow(ctx, `SELECT `+campaignLedgerItemColumns+` FROM cairn.campaign_ledger_item WHERE repo=$1 AND campaign=$2 AND kind=$3 AND item_key=$4 FOR UPDATE`, req.Repo, req.Campaign, req.Kind, req.ItemKey))
	existed := err == nil
	if err != nil && !errors.Is(err, pgx.ErrNoRows) {
		return empty, err
	}
	if !existed {
		if req.ExpectedRevision > 0 {
			return empty, failure("VERSION_CONFLICT", "campaign ledger item does not exist for expected_revision")
		}
		var count int
		if err = tx.QueryRow(ctx, `SELECT count(*) FROM cairn.campaign_ledger_item WHERE repo=$1 AND campaign=$2`, req.Repo, req.Campaign).Scan(&count); err != nil {
			return empty, err
		}
		if count >= MaxCampaignLedgerItems {
			return empty, failure("BUDGET_REFUSED", "campaign ledger exceeds maximum item limit (512)")
		}
	} else if req.ExpectedRevision > 0 && req.ExpectedRevision != existing.Revision {
		return empty, failure("VERSION_CONFLICT", "campaign ledger item revision changed")
	}

	owner := req.Owner
	if owner == "" {
		if existed {
			owner = existing.Owner
		} else if s.channel.Operator {
			owner = camp.RootAgent
		} else {
			owner = caller
		}
	}
	if !validTeamPrincipal(owner) {
		return empty, failure("INVALID_REQUEST", "ledger item owner must be an agent principal")
	}
	status := req.Status
	if status == "" {
		if existed {
			status = existing.Status
		} else {
			switch req.Kind {
			case "assignment":
				status = "planned"
			case "decision", "blocker":
				status = "open"
			case "hold":
				status = "active"
			}
		}
	}
	if !validLedgerKindAndStatus(req.Kind, status) {
		return empty, failure("INVALID_REQUEST", "invalid status for ledger item kind "+req.Kind)
	}
	summary := req.Summary
	if summary == "" && existed {
		summary = existing.Summary
	}
	if summary == "" {
		return empty, failure("INVALID_REQUEST", "summary is required for new campaign ledger items")
	}
	detail := ""
	if existed {
		detail = existing.Detail
	}
	if req.Detail != nil {
		detail = *req.Detail
	}
	src := existing.Source
	if req.Source != nil {
		src = req.Source
	}
	res := existing.Result
	if req.Result != nil {
		res = req.Result
	}
	eventID := existing.EventID
	if req.EventID != "" {
		eventID = req.EventID
	}
	deliveryID := existing.DeliveryID
	if req.DeliveryID != "" {
		deliveryID = req.DeliveryID
	}
	dueAt := existing.DueAt
	if req.DueAt != nil {
		dueAt = req.DueAt
	}

	authorized := s.channel.Operator || caller == camp.RootAgent
	if !authorized {
		if callerNode, ok := nodes[caller]; ok {
			switch callerNode.role {
			case "lead":
				prevOK := !existed || isAncestorOrSelf(caller, existing.Owner, nodes)
				escalating := isStrictAncestor(owner, caller, nodes) && ((req.Kind == "decision" && status == "open") || (req.Kind == "blocker" && (status == "open" || status == "escalated")))
				newOK := isAncestorOrSelf(caller, owner, nodes) || escalating
				authorized = prevOK && newOK
			case "worker":
				if req.Kind == "assignment" {
					authorized = existed && existing.Owner == caller && owner == caller
				} else if req.Kind == "blocker" || req.Kind == "hold" {
					authorized = (!existed || existing.Owner == caller) && owner == caller
				}
			}
		}
	}
	if !authorized {
		return empty, failure("AUTHORITY_DENIED", "caller is not authorized to modify this campaign ledger item")
	}
	if _, enrolled := nodes[owner]; !enrolled && owner != camp.RootAgent {
		return empty, failure("INVALID_REQUEST", "ledger item owner must be an active member of this campaign")
	}

	if err = checkLedgerRefSensitivityTx(ctx, tx, req.Repo, camp.Sensitivity, dest.AllowLocal, src, "source"); err != nil {
		return empty, err
	}
	if err = checkLedgerRefSensitivityTx(ctx, tx, req.Repo, camp.Sensitivity, dest.AllowLocal, res, "result"); err != nil {
		return empty, err
	}
	if eventID != "" {
		var evSens string
		err = tx.QueryRow(ctx, `SELECT e.sensitivity FROM cairn.agent_event e WHERE e.event_id=$1 AND e.repo=$4 AND (`+eventVisible+` OR $5)`,
			eventID, caller, dest.AllowLocal, req.Repo, s.channel.Operator).Scan(&evSens)
		if errors.Is(err, pgx.ErrNoRows) {
			return empty, failure("NOT_FOUND", "referenced event not found")
		}
		if err != nil {
			return empty, err
		}
		if camp.Sensitivity == "shareable" && evSens == "local" {
			return empty, failure("DESTINATION_PROHIBITED", "shareable campaign ledger cannot reference a local event")
		}
	}
	if deliveryID != "" {
		var delivSens string
		err = tx.QueryRow(ctx, `SELECT e.sensitivity FROM cairn.agent_delivery d JOIN cairn.agent_event e USING(event_id) WHERE d.delivery_id=$1 AND e.repo=$4 AND (e.sensitivity='shareable' OR $3) AND (d.consumer=$2 OR e.publisher=$2 OR $5)`,
			deliveryID, caller, dest.AllowLocal, req.Repo, s.channel.Operator).Scan(&delivSens)
		if errors.Is(err, pgx.ErrNoRows) {
			return empty, failure("NOT_FOUND", "referenced delivery not found")
		}
		if err != nil {
			return empty, err
		}
		if camp.Sensitivity == "shareable" && delivSens == "local" {
			return empty, failure("DESTINATION_PROHIBITED", "shareable campaign ledger cannot reference a local delivery")
		}
	}

	var srcID, resID *string
	var srcVer, resVer *int
	if src != nil {
		srcID, srcVer = &src.RecordID, &src.Version
	}
	if res != nil {
		resID, resVer = &res.RecordID, &res.Version
	}
	var out CampaignLedgerItem
	if !existed {
		out, err = scanCampaignLedgerItem(tx.QueryRow(ctx, `INSERT INTO cairn.campaign_ledger_item(repo, campaign, kind, item_key, owner, status, summary, detail, source_record_id, source_version, event_id, delivery_id, result_record_id, result_version, due_at, revision, updated_by) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9::uuid,$10,NULLIF($11,'')::uuid,NULLIF($12,'')::uuid,$13::uuid,$14,$15,1,$16) RETURNING `+campaignLedgerItemColumns,
			req.Repo, req.Campaign, req.Kind, req.ItemKey, owner, status, summary, detail, srcID, srcVer, eventID, deliveryID, resID, resVer, dueAt, caller))
	} else {
		out, err = scanCampaignLedgerItem(tx.QueryRow(ctx, `UPDATE cairn.campaign_ledger_item SET owner=$5, status=$6, summary=$7, detail=$8, source_record_id=$9::uuid, source_version=$10, event_id=NULLIF($11,'')::uuid, delivery_id=NULLIF($12,'')::uuid, result_record_id=$13::uuid, result_version=$14, due_at=$15, revision=revision+1, updated_by=$16, updated_at=clock_timestamp() WHERE repo=$1 AND campaign=$2 AND kind=$3 AND item_key=$4 RETURNING `+campaignLedgerItemColumns,
			req.Repo, req.Campaign, req.Kind, req.ItemKey, owner, status, summary, detail, srcID, srcVer, eventID, deliveryID, resID, resVer, dueAt, caller))
	}
	if err != nil {
		return empty, err
	}
	allItems, err := loadCampaignLedgerItemsTx(ctx, tx, req.Repo, req.Campaign, dest.AllowLocal)
	if err != nil {
		return empty, err
	}
	if err = syncCampaignAgentSummariesTx(ctx, tx, camp, allItems); err != nil {
		return empty, err
	}
	return out, nil
}

func (s *Store) GetCampaignLedger(ctx context.Context, req GetCampaignLedgerRequest, dest Destination) (CampaignLedgerSnapshot, error) {
	out := CampaignLedgerSnapshot{
		Schema:      CampaignLedgerSchema,
		Assignments: []CampaignLedgerItem{},
		Decisions:   []CampaignLedgerItem{},
		Blockers:    []CampaignLedgerItem{},
		Holds:       []CampaignLedgerItem{},
		Operations:  []CampaignOperationEntry{},
	}
	if err := validEventDestination(dest); err != nil {
		return out, err
	}
	repo, err := s.eventScope(req.Repo, "")
	if err != nil {
		return out, err
	}
	if !workstreamNamePattern.MatchString(req.Campaign) {
		return out, failure("INVALID_REQUEST", "campaign must be 1..64 ASCII letters, digits or _.-")
	}
	if req.Kind != "" && req.Kind != "assignment" && req.Kind != "decision" && req.Kind != "blocker" && req.Kind != "hold" {
		return out, failure("INVALID_REQUEST", "kind must be assignment, decision, blocker or hold")
	}
	if req.Status != "" {
		if (req.Kind != "" && !validLedgerKindAndStatus(req.Kind, req.Status)) || (req.Kind == "" && !validAnyLedgerStatus(req.Status)) {
			return out, failure("INVALID_REQUEST", "invalid ledger item status filter")
		}
	}
	if req.Owner != "" && !validTeamPrincipal(req.Owner) {
		return out, failure("INVALID_REQUEST", "invalid owner principal")
	}
	opLimit, err := eventLimit(req.OpLimit)
	if err != nil {
		return out, err
	}
	tx, err := s.begin(ctx)
	if err != nil {
		return out, err
	}
	defer tx.Rollback(ctx)
	camp, err := readTeamCampaignTx(ctx, tx, repo, req.Campaign, dest.AllowLocal, true)
	if err != nil {
		return out, err
	}
	out.Campaign = camp
	allItems, err := loadCampaignLedgerItemsTx(ctx, tx, repo, req.Campaign, dest.AllowLocal)
	if err != nil {
		return out, err
	}
	out.Counts = computeLedgerCounts(allItems)
	out.DerivedContextSummary = DeriveCampaignAgentSummary(camp, s.channel.Principal, allItems)
	for _, it := range allItems {
		if req.Kind != "" && it.Kind != req.Kind {
			continue
		}
		if req.Status != "" && it.Status != req.Status {
			continue
		}
		if req.Owner != "" && it.Owner != req.Owner {
			continue
		}
		switch it.Kind {
		case "assignment":
			out.Assignments = append(out.Assignments, it)
		case "decision":
			out.Decisions = append(out.Decisions, it)
		case "blocker":
			out.Blockers = append(out.Blockers, it)
		case "hold":
			out.Holds = append(out.Holds, it)
		}
	}
	caller := s.channel.Principal
	seeAllOps := s.channel.Operator || caller == camp.RootAgent
	rows, err := tx.Query(ctx, `SELECT j.repo, j.campaign, j.caller, j.op_key, j.operation, j.state, COALESCE(j.source_request_id::text,''), j.action_request_id::text, COALESCE(j.reply_request_id::text,''), CASE WHEN m.record_id IS NOT NULL AND m.lifecycle='active' AND sv.payload_deleted_by IS NULL AND (m.sensitivity='shareable' OR $5) THEN j.source_record_id::text END, CASE WHEN m.record_id IS NOT NULL AND m.lifecycle='active' AND sv.payload_deleted_by IS NULL AND (m.sensitivity='shareable' OR $5) THEN j.source_version END, COALESCE(CASE WHEN de.event_id IS NOT NULL AND (de.sensitivity='shareable' OR $5) THEN j.delivery_id::text END,''), COALESCE(CASE WHEN e.event_id IS NOT NULL AND (e.sensitivity='shareable' OR $5) THEN j.event_id::text END,''), COALESCE(CASE WHEN re.event_id IS NOT NULL AND (re.sensitivity='shareable' OR $5) THEN j.reply_event_id::text END,''), j.created_at, j.updated_at FROM cairn.campaign_operation_journal j LEFT JOIN cairn.memory_record m ON m.record_id=j.source_record_id LEFT JOIN cairn.record_version sv ON sv.record_id=j.source_record_id AND sv.version=j.source_version LEFT JOIN cairn.agent_delivery dl ON dl.delivery_id=j.delivery_id LEFT JOIN cairn.agent_event de ON de.event_id=dl.event_id LEFT JOIN cairn.agent_event e ON e.event_id=j.event_id LEFT JOIN cairn.agent_event re ON re.event_id=j.reply_event_id WHERE j.repo=$1 AND j.campaign=$2 AND ($3 OR j.caller=$4) AND (j.sensitivity='shareable' OR $5) ORDER BY j.updated_at DESC, j.op_key LIMIT $6`,
		repo, req.Campaign, seeAllOps, caller, dest.AllowLocal, opLimit)
	if err != nil {
		return out, err
	}
	defer rows.Close()
	for rows.Next() {
		var op CampaignOperationEntry
		var srcID *string
		var srcVer *int
		if err = rows.Scan(&op.Repo, &op.Campaign, &op.Caller, &op.OpKey, &op.Operation, &op.State, &op.SourceRequestID, &op.ActionRequestID, &op.ReplyRequestID, &srcID, &srcVer, &op.DeliveryID, &op.EventID, &op.ReplyEventID, &op.CreatedAt, &op.UpdatedAt); err != nil {
			return out, err
		}
		if srcID != nil && srcVer != nil {
			op.Source = &RecordVersionRef{RecordID: *srcID, Version: *srcVer}
		}
		out.Operations = append(out.Operations, op)
	}
	return out, rows.Err()
}

func (s *Store) SyncCampaignContext(ctx context.Context, req SyncCampaignContextRequest, dest Destination) (SyncCampaignContextResult, error) {
	var empty SyncCampaignContextResult
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
	if req.Session != nil && s.session != nil && *req.Session != s.session.Ref {
		return empty, failure("INVALID_REQUEST", "conflicting session identity")
	}
	sessionRef := req.Session
	if sessionRef == nil && s.session != nil {
		cp := s.session.Ref
		sessionRef = &cp
	}
	if sessionRef != nil {
		if err := sessionRef.Validate(); err != nil {
			return empty, err
		}
	}
	req.Session = sessionRef
	input := struct {
		SyncCampaignContextRequest
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
	return mutate(ctx, s, "ledger-sync-context", req.RequestID, input, func(tx pgx.Tx) (SyncCampaignContextResult, error) {
		if _, err := retrievalGeneration(ctx, tx); err != nil {
			return empty, err
		}
		if err := lock(ctx, tx, "agent-events:"+req.Repo); err != nil {
			return empty, err
		}
		camp, err := readTeamCampaignTx(ctx, tx, req.Repo, req.Campaign, dest.AllowLocal, true)
		if err != nil {
			return empty, err
		}
		items, err := loadCampaignLedgerItemsTx(ctx, tx, req.Repo, req.Campaign, dest.AllowLocal)
		if err != nil {
			return empty, err
		}
		principal := s.channel.Principal
		if sessionRef != nil {
			principal = "agent/" + sessionRef.AgentID
		}
		summary := DeriveCampaignAgentSummary(camp, principal, items)
		res := SyncCampaignContextResult{
			Repo:                  req.Repo,
			Campaign:              req.Campaign,
			Principal:             principal,
			DerivedContextSummary: summary,
		}
		if sessionRef != nil {
			if !camp.Active {
				return empty, failure("INVALID_REQUEST", "cannot sync session context from an inactive campaign")
			}
			enrolled := principal == camp.RootAgent
			for _, m := range camp.Members {
				if m.Active && m.Member == principal {
					enrolled = true
					break
				}
			}
			if !enrolled {
				return empty, failure("AUTHORITY_DENIED", "session is not an active member of this campaign")
			}
			ownerPrincipal := s.channel.Principal
			if s.session != nil {
				ownerPrincipal = s.session.Profile
			}
			cur, err := currentAgentSession(ctx, tx, *sessionRef, ownerPrincipal, s.channel.Repo, true, dest)
			if err != nil {
				return empty, err
			}
			if err = activeAgent(cur); err != nil {
				return empty, err
			}
			if camp.Sensitivity == "local" && !dest.AllowLocal {
				return empty, failure("NOT_FOUND", "team campaign not found")
			}
			var sessVis string
			if err = tx.QueryRow(ctx, `SELECT visibility FROM cairn.agent_session WHERE agent_id=$1`, sessionRef.AgentID).Scan(&sessVis); err != nil {
				return empty, err
			}
			if camp.Sensitivity == "local" && sessVis != "local" {
				return empty, failure("DESTINATION_PROHIBITED", "cannot write local campaign summary into hosted session metadata")
			}
			cur.Metadata.TaskSummary = summary
			updated, err := scanAgentSession(tx.QueryRow(ctx, `UPDATE cairn.agent_session SET metadata=$2,context_revision=context_revision+1,last_seen=clock_timestamp(),expires_at=clock_timestamp()+interval '90 seconds' WHERE agent_id=$1 RETURNING `+agentSessionColumns, sessionRef.AgentID, cur.Metadata))
			if err != nil {
				return empty, err
			}
			res.Agent = &updated
		}
		return res, nil
	}, guard)
}

var _ = sha256.New
var _ = hex.EncodeToString
var _ = json.Marshal
var _ = uuid.NewString
