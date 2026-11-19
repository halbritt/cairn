package core

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"strings"
	"unicode/utf8"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
)

type CampaignPublishRequest struct {
	Repo        string              `json:"repo,omitempty"`
	Campaign    string              `json:"campaign"`
	OpKey       string              `json:"op_key"`
	Publication PublishEventRequest `json:"publication"`
	ItemKey     string              `json:"item_key,omitempty"`
	ItemKind    string              `json:"item_kind,omitempty"`
	ItemStatus  string              `json:"item_status,omitempty"`
}

type CampaignSendRequest struct {
	Repo        string              `json:"repo,omitempty"`
	Campaign    string              `json:"campaign"`
	OpKey       string              `json:"op_key"`
	Body        string              `json:"body"`
	SourceKind  string              `json:"source_kind,omitempty"`
	Shareable   bool                `json:"shareable,omitempty"`
	Publication PublishEventRequest `json:"publication"`
	ItemKey     string              `json:"item_key,omitempty"`
	ItemKind    string              `json:"item_kind,omitempty"`
	ItemStatus  string              `json:"item_status,omitempty"`
}

type CampaignSendResult struct {
	OpKey                string           `json:"op_key"`
	State                string           `json:"state"`
	Source               RecordVersionRef `json:"source"`
	Event                AgentEvent       `json:"event"`
	SourceRequestID      string           `json:"source_request_id"`
	PublicationRequestID string           `json:"publication_request_id"`
}

type CampaignCompleteRequest struct {
	Repo          string `json:"repo,omitempty"`
	Campaign      string `json:"campaign"`
	OpKey         string `json:"op_key"`
	DeliveryID    string `json:"delivery_id"`
	LeaseID       string `json:"lease_id,omitempty"`
	Body          string `json:"body"`
	SourceKind    string `json:"source_kind,omitempty"`
	Shareable     bool   `json:"shareable,omitempty"`
	ReplyTo       string `json:"reply_to,omitempty"`
	CausationID   string `json:"causation_id,omitempty"`
	CorrelationID string `json:"correlation_id,omitempty"`
	Summary       string `json:"summary,omitempty"`
	ItemKey       string `json:"item_key,omitempty"`
	ItemStatus    string `json:"item_status,omitempty"`
}

type CampaignCompleteResult struct {
	OpKey           string           `json:"op_key"`
	State           string           `json:"state"`
	Delivery        AgentDelivery    `json:"delivery"`
	Result          RecordVersionRef `json:"result"`
	ReplyEvent      *AgentEvent      `json:"reply_event,omitempty"`
	ActionRequestID string           `json:"action_request_id"`
	ReplyRequestID  string           `json:"reply_request_id,omitempty"`
}

type CampaignAckRequest struct {
	Repo        string `json:"repo,omitempty"`
	Campaign    string `json:"campaign"`
	OpKey       string `json:"op_key"`
	DeliveryID  string `json:"delivery_id"`
	LeaseID     string `json:"lease_id,omitempty"`
	Disposition string `json:"disposition,omitempty"`
	Code        string `json:"code,omitempty"`
	ItemKey     string `json:"item_key,omitempty"`
	ItemKind    string `json:"item_kind,omitempty"`
	ItemStatus  string `json:"item_status,omitempty"`
}

type journalRecord struct {
	intentSHA256    string
	sourceRequestID string
	actionRequestID string
	replyRequestID  string
	leaseID         string
	sourceRecordID  string
	sourceVersion   int
	deliveryID      string
	eventID         string
	replyEventID    string
	state           string
}

func hashCampaignIntent(v any) (string, error) {
	raw, err := json.Marshal(v)
	if err != nil {
		return "", err
	}
	sum := sha256.Sum256(raw)
	return hex.EncodeToString(sum[:]), nil
}

func (s *Store) preflightLedgerLinkTx(ctx context.Context, tx pgx.Tx, camp TeamCampaign, itemKey, itemKind, itemStatus, defaultOwner string) error {
	if itemKey == "" {
		return nil
	}
	if itemKind == "" {
		itemKind = "assignment"
	}
	nodes := make(map[string]teamNode, len(camp.Members))
	for _, m := range camp.Members {
		if m.Active {
			nodes[m.Member] = teamNode{lead: m.Lead, role: m.Role}
		}
	}
	caller := s.channel.Principal
	existing, err := scanCampaignLedgerItem(tx.QueryRow(ctx, `SELECT `+campaignLedgerItemColumns+` FROM cairn.campaign_ledger_item WHERE repo=$1 AND campaign=$2 AND kind=$3 AND item_key=$4 FOR SHARE`, camp.Repo, camp.Campaign, itemKind, itemKey))
	existed := err == nil
	if err != nil && !errors.Is(err, pgx.ErrNoRows) {
		return err
	}
	if !existed {
		var count int
		if err = tx.QueryRow(ctx, `SELECT count(*) FROM cairn.campaign_ledger_item WHERE repo=$1 AND campaign=$2`, camp.Repo, camp.Campaign).Scan(&count); err != nil {
			return err
		}
		if count >= MaxCampaignLedgerItems {
			return failure("BUDGET_REFUSED", "campaign ledger exceeds maximum item limit (512)")
		}
	}
	owner := ""
	if existed {
		owner = existing.Owner
	} else if itemKind == "assignment" && defaultOwner != "" {
		owner = defaultOwner
	} else if s.channel.Operator {
		owner = camp.RootAgent
	} else {
		owner = caller
	}
	status := itemStatus
	if status == "" {
		if existed {
			status = existing.Status
		} else {
			switch itemKind {
			case "assignment":
				status = "dispatched"
			case "decision", "blocker":
				status = "open"
			case "hold":
				status = "active"
			}
		}
	}
	authorized := s.channel.Operator || caller == camp.RootAgent
	if !authorized {
		if callerNode, ok := nodes[caller]; ok {
			switch callerNode.role {
			case "lead":
				prevOK := !existed || isAncestorOrSelf(caller, existing.Owner, nodes)
				escalating := isStrictAncestor(owner, caller, nodes) && ((itemKind == "decision" && status == "open") || (itemKind == "blocker" && (status == "open" || status == "escalated")))
				authorized = prevOK && (isAncestorOrSelf(caller, owner, nodes) || escalating)
			case "worker":
				if itemKind == "assignment" {
					authorized = existed && existing.Owner == caller && owner == caller
				} else if itemKind == "blocker" || itemKind == "hold" {
					authorized = (!existed || existing.Owner == caller) && owner == caller
				}
			}
		}
	}
	if !authorized {
		return failure("AUTHORITY_DENIED", "caller is not authorized to modify linked campaign ledger item")
	}
	if _, enrolled := nodes[owner]; !enrolled && owner != camp.RootAgent {
		return failure("INVALID_REQUEST", "ledger item owner must be an active member of this campaign")
	}
	return nil
}

func checkActiveMember(camp TeamCampaign, caller string, isOperator bool) error {
	if isOperator || caller == camp.RootAgent {
		return nil
	}
	for _, m := range camp.Members {
		if m.Member == caller && m.Active {
			return nil
		}
	}
	return failure("AUTHORITY_DENIED", "only an active campaign member or operator can journal campaign operations")
}

func (s *Store) reserveOrLoadJournal(ctx context.Context, repo, campaign, opKey, opName, opSens, intentHash, leaseID, itemKey, itemKind, itemStatus, defaultOwner string, dest Destination, requireLeaseOnNew, needSourceReq, needReplyReq bool) (journalRecord, TeamCampaign, error) {
	var out journalRecord
	var camp TeamCampaign
	tx, err := s.begin(ctx)
	if err != nil {
		return out, camp, err
	}
	defer tx.Rollback(context.Background())
	if _, err = retrievalGeneration(ctx, tx); err != nil {
		return out, camp, err
	}
	if err = lock(ctx, tx, "agent-events:"+repo); err != nil {
		return out, camp, err
	}
	if err = lock(ctx, tx, "campaign-op:"+repo+":"+campaign+":"+s.channel.Principal+":"+opKey); err != nil {
		return out, camp, err
	}
	camp, err = readTeamCampaignTx(ctx, tx, repo, campaign, dest.AllowLocal, true)
	if err != nil {
		return out, camp, err
	}
	if err = checkActiveMember(camp, s.channel.Principal, s.channel.Operator); err != nil {
		return out, camp, err
	}
	if opSens != "local" {
		opSens = "shareable"
	}
	if camp.Sensitivity == "shareable" && opSens == "local" && itemKey != "" {
		return out, camp, failure("DESTINATION_PROHIBITED", "shareable campaign ledger item cannot link a local operation")
	}
	var existingOp string
	var srcReq, replyReq, storedLease, srcRec, deliv, ev, replyEv *string
	var srcVer *int
	err = tx.QueryRow(ctx, `SELECT operation, intent_sha256, source_request_id::text, action_request_id::text, reply_request_id::text, lease_id::text, source_record_id::text, source_version, delivery_id::text, event_id::text, reply_event_id::text, state FROM cairn.campaign_operation_journal WHERE repo=$1 AND campaign=$2 AND caller=$3 AND op_key=$4 FOR UPDATE`,
		repo, campaign, s.channel.Principal, opKey).Scan(
		&existingOp, &out.intentSHA256, &srcReq, &out.actionRequestID, &replyReq, &storedLease, &srcRec, &srcVer, &deliv, &ev, &replyEv, &out.state,
	)
	if errors.Is(err, pgx.ErrNoRows) {
		if !camp.Active {
			return out, camp, failure("INVALID_REQUEST", "cannot journal operations on an inactive campaign")
		}
		if requireLeaseOnNew && leaseID == "" {
			return out, camp, failure("INVALID_REQUEST", "lease_id is required on initial campaign "+opName)
		}
		if err = s.preflightLedgerLinkTx(ctx, tx, camp, itemKey, itemKind, itemStatus, defaultOwner); err != nil {
			return out, camp, err
		}
		out.intentSHA256 = intentHash
		out.actionRequestID = uuid.NewString()
		out.state = "reserved"
		out.leaseID = leaseID
		var srcReqPtr, replyReqPtr, leasePtr *string
		if needSourceReq {
			sID := uuid.NewString()
			out.sourceRequestID = sID
			srcReqPtr = &sID
		}
		if needReplyReq {
			rID := uuid.NewString()
			out.replyRequestID = rID
			replyReqPtr = &rID
		}
		if leaseID != "" {
			leasePtr = &leaseID
		}
		if _, err = tx.Exec(ctx, `INSERT INTO cairn.campaign_operation_journal(repo, campaign, caller, op_key, operation, sensitivity, intent_sha256, source_request_id, action_request_id, reply_request_id, lease_id, state) VALUES($1,$2,$3,$4,$5,$6,$7,$8::uuid,$9::uuid,$10::uuid,$11::uuid,'reserved')`,
			repo, campaign, s.channel.Principal, opKey, opName, opSens, intentHash, srcReqPtr, out.actionRequestID, replyReqPtr, leasePtr); err != nil {
			return out, camp, err
		}
		return out, camp, tx.Commit(ctx)
	}
	if err != nil {
		return out, camp, err
	}
	if existingOp != opName || out.intentSHA256 != intentHash {
		return out, camp, failure("IDEMPOTENCY_CONFLICT", "campaign op_key already used with different operation or arguments")
	}
	if out.state != "committed" && !camp.Active {
		return out, camp, failure("INVALID_REQUEST", "cannot resume incomplete operation on an inactive campaign")
	}
	if srcReq != nil {
		out.sourceRequestID = *srcReq
	}
	if replyReq != nil {
		out.replyRequestID = *replyReq
	}
	if storedLease != nil {
		out.leaseID = *storedLease
	}
	if srcRec != nil && srcVer != nil {
		out.sourceRecordID, out.sourceVersion = *srcRec, *srcVer
	}
	if deliv != nil {
		out.deliveryID = *deliv
	}
	if ev != nil {
		out.eventID = *ev
	}
	if replyEv != nil {
		out.replyEventID = *replyEv
	}
	if out.state == "reserved" && leaseID != "" && leaseID != out.leaseID {
		var actionReceiptExists bool
		if err = tx.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM cairn.mutation_request WHERE request_id = $1::uuid)`, out.actionRequestID).Scan(&actionReceiptExists); err != nil {
			return out, camp, err
		}
		if !actionReceiptExists {
			out.leaseID = leaseID
			if _, err = tx.Exec(ctx, `UPDATE cairn.campaign_operation_journal SET lease_id=$5::uuid, updated_at=clock_timestamp() WHERE repo=$1 AND campaign=$2 AND caller=$3 AND op_key=$4`, repo, campaign, s.channel.Principal, opKey, leaseID); err != nil {
				return out, camp, err
			}
		}
	}
	return out, camp, tx.Commit(ctx)
}

func (s *Store) commitJournalStep(ctx context.Context, repo, campaign, opKey, state string, src *RecordVersionRef, deliveryID, eventID, replyEventID string) error {
	tx, err := s.begin(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback(context.Background())
	if err = lock(ctx, tx, "campaign-op:"+repo+":"+campaign+":"+s.channel.Principal+":"+opKey); err != nil {
		return err
	}
	var srcID *string
	var srcVer *int
	if src != nil {
		srcID, srcVer = &src.RecordID, &src.Version
	}
	_, err = tx.Exec(ctx, `UPDATE cairn.campaign_operation_journal SET state=$5, source_record_id=COALESCE($6::uuid, source_record_id), source_version=COALESCE($7, source_version), delivery_id=COALESCE(NULLIF($8,'')::uuid, delivery_id), event_id=COALESCE(NULLIF($9,'')::uuid, event_id), reply_event_id=COALESCE(NULLIF($10,'')::uuid, reply_event_id), updated_at=clock_timestamp() WHERE repo=$1 AND campaign=$2 AND caller=$3 AND op_key=$4`,
		repo, campaign, s.channel.Principal, opKey, state, srcID, srcVer, deliveryID, eventID, replyEventID)
	if err != nil {
		return err
	}
	return tx.Commit(ctx)
}

func defaultAssignmentOwner(dest EventDestination) string {
	if dest.Type == "agent" && validTeamPrincipal(dest.Name) {
		return dest.Name
	}
	return ""
}

func trimmedFallbackSummary(explicitSummary, body string) string {
	if explicitSummary != "" {
		return explicitSummary
	}
	trimmed := strings.TrimSpace(body)
	if trimmed != "" && len(trimmed) <= MaxEventSummaryBytes && !hasDisallowedSummaryRune(trimmed) {
		return trimmed
	}
	return ""
}

func (s *Store) finalizeJournalAndLink(ctx context.Context, repo, campaign, opKey, opSens, itemKey, itemKind, explicitStatus, implicitStatus, defaultOwner string, dest Destination, src *RecordVersionRef, eventID, deliveryID, replyEventID string, res *RecordVersionRef, fallbackSummary string) error {
	tx, err := s.begin(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback(context.Background())
	if _, err = retrievalGeneration(ctx, tx); err != nil {
		return err
	}
	if err = lock(ctx, tx, "agent-events:"+repo); err != nil {
		return err
	}
	if err = lock(ctx, tx, "campaign-op:"+repo+":"+campaign+":"+s.channel.Principal+":"+opKey); err != nil {
		return err
	}
	var curState string
	if err = tx.QueryRow(ctx, `SELECT state FROM cairn.campaign_operation_journal WHERE repo=$1 AND campaign=$2 AND caller=$3 AND op_key=$4 FOR UPDATE`, repo, campaign, s.channel.Principal, opKey).Scan(&curState); err != nil {
		return err
	}
	if curState == "committed" {
		return nil
	}
	if itemKey != "" {
		if itemKind == "" {
			itemKind = "assignment"
		}
		var curStatus string
		err = tx.QueryRow(ctx, `SELECT status FROM cairn.campaign_ledger_item WHERE repo=$1 AND campaign=$2 AND kind=$3 AND item_key=$4 FOR UPDATE`, repo, campaign, itemKind, itemKey).Scan(&curStatus)
		exists := err == nil
		if err != nil && !errors.Is(err, pgx.ErrNoRows) {
			return err
		}
		status := explicitStatus
		if status == "" && implicitStatus != "" {
			// Do not regress an assignment that already advanced past planned on a late send/publish retry.
			if !exists || itemKind != "assignment" || implicitStatus == "completed" || curStatus == "planned" {
				status = implicitStatus
			}
		}
		req := SetCampaignLedgerItemRequest{
			Repo: repo, Campaign: campaign, Kind: itemKind, ItemKey: itemKey,
			Status: status, Source: src, EventID: eventID, DeliveryID: deliveryID, Result: res,
		}
		if !exists {
			if itemKind == "assignment" && defaultOwner != "" {
				req.Owner = defaultOwner
			}
			if fallbackSummary == "" {
				fallbackSummary = itemKind + " " + itemKey
			}
			req.Summary = fallbackSummary
		}
		if _, err = s.upsertCampaignLedgerItemTx(ctx, tx, req, dest); err != nil {
			return err
		}
	}
	var srcID *string
	var srcVer *int
	journalSrc := src
	if journalSrc == nil {
		journalSrc = res
	}
	if journalSrc != nil {
		srcID, srcVer = &journalSrc.RecordID, &journalSrc.Version
	}
	if opSens != "local" {
		opSens = "shareable"
	}
	if _, err = tx.Exec(ctx, `UPDATE cairn.campaign_operation_journal SET state='committed', sensitivity=$11, source_record_id=COALESCE($5::uuid, source_record_id), source_version=COALESCE($6, source_version), delivery_id=COALESCE(NULLIF($7,'')::uuid, delivery_id), event_id=COALESCE(NULLIF($8,'')::uuid, event_id), reply_event_id=COALESCE(NULLIF($9,'')::uuid, reply_event_id), updated_at=clock_timestamp() WHERE repo=$1 AND campaign=$2 AND caller=$3 AND op_key=$4 AND $10`,
		repo, campaign, s.channel.Principal, opKey, srcID, srcVer, deliveryID, eventID, replyEventID, true, opSens); err != nil {
		return err
	}
	return tx.Commit(ctx)
}

func validateOpHeader(repo, campaign, opKey, itemKey, itemKind, itemStatus string) error {
	if !workstreamNamePattern.MatchString(campaign) {
		return failure("INVALID_REQUEST", "campaign must be 1..64 ASCII letters, digits or _.-")
	}
	if !noticeSupersedesKey.MatchString(opKey) {
		return failure("INVALID_REQUEST", "op_key must be 1..128 ASCII letters, digits or _.:/-")
	}
	if itemKey != "" {
		if !noticeSupersedesKey.MatchString(itemKey) {
			return failure("INVALID_REQUEST", "item_key must be 1..128 ASCII letters, digits or _.:/-")
		}
		k := itemKind
		if k == "" {
			k = "assignment"
		}
		if k != "assignment" && k != "decision" && k != "blocker" && k != "hold" {
			return failure("INVALID_REQUEST", "item_kind must be assignment, decision, blocker or hold")
		}
		if itemStatus != "" && !validLedgerKindAndStatus(k, itemStatus) {
			return failure("INVALID_REQUEST", "invalid item_status for kind "+k)
		}
	}
	_ = repo
	return nil
}

func (s *Store) lookupSourceSensitivity(ctx context.Context, repo string, ref RecordVersionRef, allowLocal bool) (string, error) {
	var sens string
	err := s.pool.QueryRow(ctx, `SELECT m.sensitivity FROM cairn.memory_record m JOIN cairn.record_version v USING(record_id) WHERE m.record_id=$1 AND v.version=$2 AND v.repo=$3 AND m.lifecycle='active' AND v.payload_deleted_by IS NULL AND (m.sensitivity='shareable' OR $4)`,
		ref.RecordID, ref.Version, repo, allowLocal).Scan(&sens)
	if errors.Is(err, pgx.ErrNoRows) {
		return "", failure("NOT_FOUND", "source record version unavailable")
	}
	return sens, err
}

func (s *Store) CampaignPublish(ctx context.Context, req CampaignPublishRequest, dest Destination) (AgentEvent, error) {
	if err := validEventDestination(dest); err != nil {
		return AgentEvent{}, err
	}
	repo, err := s.eventScope(req.Repo, "")
	if err != nil {
		return AgentEvent{}, err
	}
	if err := validateOpHeader(repo, req.Campaign, req.OpKey, req.ItemKey, req.ItemKind, req.ItemStatus); err != nil {
		return AgentEvent{}, err
	}
	normPub := req.Publication
	normPub.Repo = repo
	normPub.RequestID = ""
	if normPub, err = s.normalizePublication(normPub, dest); err != nil {
		return AgentEvent{}, err
	}
	opSens, err := s.lookupSourceSensitivity(ctx, repo, normPub.Ref, dest.AllowLocal)
	if err != nil {
		return AgentEvent{}, err
	}
	intentHash, err := hashCampaignIntent(struct {
		Op          string              `json:"op"`
		Repo        string              `json:"repo"`
		Campaign    string              `json:"campaign"`
		Destination string              `json:"destination"`
		Publication PublishEventRequest `json:"publication"`
		ItemKey     string              `json:"item_key,omitempty"`
		ItemKind    string              `json:"item_kind,omitempty"`
		ItemStatus  string              `json:"item_status,omitempty"`
	}{"publish", repo, req.Campaign, dest.Name, normPub, req.ItemKey, req.ItemKind, req.ItemStatus})
	if err != nil {
		return AgentEvent{}, err
	}
	defOwner := defaultAssignmentOwner(normPub.Destination)
	j, _, err := s.reserveOrLoadJournal(ctx, repo, req.Campaign, req.OpKey, "publish", opSens, intentHash, "", req.ItemKey, req.ItemKind, req.ItemStatus, defOwner, dest, false, false, false)
	if err != nil {
		return AgentEvent{}, err
	}
	pub := normPub
	pub.RequestID = j.actionRequestID
	ev, err := s.PublishEvent(ctx, pub, dest)
	if err != nil {
		return AgentEvent{}, err
	}
	src := ev.Ref
	implicitStatus := ""
	if req.ItemKey != "" && (req.ItemKind == "" || req.ItemKind == "assignment") {
		implicitStatus = "dispatched"
	}
	if err := s.finalizeJournalAndLink(ctx, repo, req.Campaign, req.OpKey, opSens, req.ItemKey, req.ItemKind, req.ItemStatus, implicitStatus, defOwner, dest, &src, ev.EventID, "", "", nil, ev.Summary); err != nil {
		return AgentEvent{}, err
	}
	return ev, nil
}

func (s *Store) CampaignSend(ctx context.Context, req CampaignSendRequest, dest Destination) (CampaignSendResult, error) {
	var empty CampaignSendResult
	if err := validEventDestination(dest); err != nil {
		return empty, err
	}
	repo, err := s.eventScope(req.Repo, "")
	if err != nil {
		return empty, err
	}
	if err := validateOpHeader(repo, req.Campaign, req.OpKey, req.ItemKey, req.ItemKind, req.ItemStatus); err != nil {
		return empty, err
	}
	if len(req.Body) == 0 || len(req.Body) > 65536 || strings.TrimSpace(req.Body) == "" || !utf8.ValidString(req.Body) {
		return empty, failure("INVALID_REQUEST", "send body requires 1-65536 bytes of nonblank UTF-8 text")
	}
	if len(req.Body) > MaxEventSummaryBytes && req.Publication.Summary == "" {
		return empty, failure("INVALID_REQUEST", "summary (1-280 single-line UTF-8 bytes) is required when body exceeds 280 bytes")
	}
	if !req.Shareable && !dest.AllowLocal {
		return empty, failure("DESTINATION_PROHIBITED", "hosted destination requires shareable: true")
	}
	srcKind := req.SourceKind
	if srcKind == "" {
		srcKind = "note"
	}
	opSens := "local"
	if req.Shareable {
		opSens = "shareable"
	}
	checkDraft := Draft{
		Body:        req.Body,
		Kind:        srcKind,
		ClaimType:   "self",
		Sensitivity: opSens,
		Scope:       Scope{Repo: repo, TaskID: "coordination/11111111-1111-4111-8111-111111111111", RunID: "*"},
	}
	if err := checkDraft.validate(); err != nil {
		return empty, err
	}
	normPub := req.Publication
	normPub.Repo = repo
	normPub.RequestID = ""
	normPub.Ref = RecordVersionRef{RecordID: "11111111-1111-4111-8111-111111111111", Version: 1}
	if normPub, err = s.normalizePublication(normPub, dest); err != nil {
		return empty, err
	}
	normPub.Ref = RecordVersionRef{}
	bodySum := sha256.Sum256([]byte(req.Body))
	intentHash, err := hashCampaignIntent(struct {
		Op          string              `json:"op"`
		Repo        string              `json:"repo"`
		Campaign    string              `json:"campaign"`
		Destination string              `json:"destination"`
		BodySHA256  string              `json:"body_sha256"`
		SourceKind  string              `json:"source_kind"`
		Shareable   bool                `json:"shareable"`
		Publication PublishEventRequest `json:"publication"`
		ItemKey     string              `json:"item_key,omitempty"`
		ItemKind    string              `json:"item_kind,omitempty"`
		ItemStatus  string              `json:"item_status,omitempty"`
	}{"send", repo, req.Campaign, dest.Name, hex.EncodeToString(bodySum[:]), srcKind, req.Shareable, normPub, req.ItemKey, req.ItemKind, req.ItemStatus})
	if err != nil {
		return empty, err
	}
	defOwner := defaultAssignmentOwner(normPub.Destination)
	j, _, err := s.reserveOrLoadJournal(ctx, repo, req.Campaign, req.OpKey, "send", opSens, intentHash, "", req.ItemKey, req.ItemKind, req.ItemStatus, defOwner, dest, false, true, false)
	if err != nil {
		return empty, err
	}
	srcRef := RecordVersionRef{RecordID: j.sourceRecordID, Version: j.sourceVersion}
	if srcRef.RecordID == "" {
		rec, err := s.Create(ctx, CreateRequest{
			RequestID: j.sourceRequestID,
			Draft: Draft{
				Body:        req.Body,
				Kind:        srcKind,
				ClaimType:   "self",
				Sensitivity: opSens,
				Scope:       Scope{Repo: repo, TaskID: "coordination/" + j.actionRequestID, RunID: "*"},
			},
		})
		if err != nil {
			return empty, err
		}
		srcRef = RecordVersionRef{RecordID: rec.RecordID, Version: rec.Version}
		if err := s.commitJournalStep(ctx, repo, req.Campaign, req.OpKey, "source_saved", &srcRef, "", "", ""); err != nil {
			return empty, err
		}
	}
	pub := normPub
	pub.RequestID = j.actionRequestID
	pub.Ref = srcRef
	ev, err := s.PublishEvent(ctx, pub, dest)
	if err != nil {
		return empty, err
	}
	implicitStatus := ""
	if req.ItemKey != "" && (req.ItemKind == "" || req.ItemKind == "assignment") {
		implicitStatus = "dispatched"
	}
	fallbackSum := trimmedFallbackSummary(ev.Summary, req.Body)
	if err := s.finalizeJournalAndLink(ctx, repo, req.Campaign, req.OpKey, opSens, req.ItemKey, req.ItemKind, req.ItemStatus, implicitStatus, defOwner, dest, &srcRef, ev.EventID, "", "", nil, fallbackSum); err != nil {
		return empty, err
	}
	return CampaignSendResult{
		OpKey:                req.OpKey,
		State:                "committed",
		Source:               srcRef,
		Event:                ev,
		SourceRequestID:      j.sourceRequestID,
		PublicationRequestID: j.actionRequestID,
	}, nil
}

func (s *Store) lookupOwnedDeliverySensitivity(ctx context.Context, repo, deliveryID string, allowLocal bool) (string, error) {
	var sens string
	err := s.pool.QueryRow(ctx, `SELECT e.sensitivity FROM cairn.agent_delivery d JOIN cairn.agent_event e USING(event_id) WHERE d.delivery_id=$1 AND e.repo=$2 AND d.consumer=$3 AND (e.sensitivity='shareable' OR $4)`,
		deliveryID, repo, s.channel.Principal, allowLocal).Scan(&sens)
	if errors.Is(err, pgx.ErrNoRows) {
		return "", failure("NOT_FOUND", "delivery not found")
	}
	return sens, err
}

func (s *Store) CampaignAck(ctx context.Context, req CampaignAckRequest, dest Destination) (AgentDelivery, error) {
	if err := validEventDestination(dest); err != nil {
		return AgentDelivery{}, err
	}
	repo, err := s.eventScope(req.Repo, "")
	if err != nil {
		return AgentDelivery{}, err
	}
	if err := validateOpHeader(repo, req.Campaign, req.OpKey, req.ItemKey, req.ItemKind, req.ItemStatus); err != nil {
		return AgentDelivery{}, err
	}
	if validID(req.DeliveryID) != nil {
		return AgentDelivery{}, failure("INVALID_REQUEST", "delivery_id UUID required")
	}
	if req.LeaseID != "" && validID(req.LeaseID) != nil {
		return AgentDelivery{}, failure("INVALID_REQUEST", "lease_id must be a UUID")
	}
	disp := req.Disposition
	if disp == "" {
		disp = "handled"
	}
	if disp != "handled" && disp != "ignored" && disp != "failed" {
		return AgentDelivery{}, failure("INVALID_REQUEST", "disposition must be handled, ignored or failed")
	}
	if (disp == "handled" && req.Code != "") || (disp != "handled" && req.Code != "unsupported_kind" && req.Code != "source_unavailable" && req.Code != "processing_failed") {
		return AgentDelivery{}, failure("INVALID_REQUEST", "invalid disposition code")
	}
	opSens, err := s.lookupOwnedDeliverySensitivity(ctx, repo, req.DeliveryID, dest.AllowLocal)
	if err != nil {
		return AgentDelivery{}, err
	}
	intentHash, err := hashCampaignIntent(struct {
		Op          string `json:"op"`
		Repo        string `json:"repo"`
		Campaign    string `json:"campaign"`
		Destination string `json:"destination"`
		DeliveryID  string `json:"delivery_id"`
		Disposition string `json:"disposition"`
		Code        string `json:"code,omitempty"`
		ItemKey     string `json:"item_key,omitempty"`
		ItemKind    string `json:"item_kind,omitempty"`
		ItemStatus  string `json:"item_status,omitempty"`
	}{"ack", repo, req.Campaign, dest.Name, req.DeliveryID, disp, req.Code, req.ItemKey, req.ItemKind, req.ItemStatus})
	if err != nil {
		return AgentDelivery{}, err
	}
	j, _, err := s.reserveOrLoadJournal(ctx, repo, req.Campaign, req.OpKey, "ack", opSens, intentHash, req.LeaseID, req.ItemKey, req.ItemKind, req.ItemStatus, "", dest, true, false, false)
	if err != nil {
		return AgentDelivery{}, err
	}
	lease := j.leaseID
	if lease == "" {
		lease = req.LeaseID
	}
	deliv, err := s.CompleteEvent(ctx, CompleteEventRequest{
		RequestID:   j.actionRequestID,
		DeliveryID:  req.DeliveryID,
		LeaseID:     lease,
		Disposition: disp,
		Code:        req.Code,
	}, dest)
	if err != nil {
		return AgentDelivery{}, err
	}
	if err := s.finalizeJournalAndLink(ctx, repo, req.Campaign, req.OpKey, opSens, req.ItemKey, req.ItemKind, req.ItemStatus, "", "", dest, nil, deliv.Event.EventID, deliv.DeliveryID, "", nil, deliv.Event.Summary); err != nil {
		return AgentDelivery{}, err
	}
	return deliv, nil
}

func (s *Store) CampaignComplete(ctx context.Context, req CampaignCompleteRequest, dest Destination) (CampaignCompleteResult, error) {
	var empty CampaignCompleteResult
	if err := validEventDestination(dest); err != nil {
		return empty, err
	}
	repo, err := s.eventScope(req.Repo, "")
	if err != nil {
		return empty, err
	}
	if err := validateOpHeader(repo, req.Campaign, req.OpKey, req.ItemKey, "assignment", req.ItemStatus); err != nil {
		return empty, err
	}
	if validID(req.DeliveryID) != nil {
		return empty, failure("INVALID_REQUEST", "delivery_id UUID required")
	}
	if req.LeaseID != "" && validID(req.LeaseID) != nil {
		return empty, failure("INVALID_REQUEST", "lease_id must be a UUID")
	}
	if len(req.Body) == 0 || len(req.Body) > 65536 || strings.TrimSpace(req.Body) == "" || !utf8.ValidString(req.Body) {
		return empty, failure("INVALID_REQUEST", "complete body requires 1-65536 bytes of nonblank UTF-8 text")
	}
	if req.ReplyTo != "" && len(req.Body) > MaxEventSummaryBytes && req.Summary == "" {
		return empty, failure("INVALID_REQUEST", "summary (1-280 single-line UTF-8 bytes) is required when reply body exceeds 280 bytes")
	}
	if err := ValidateEventSummary(req.Summary); err != nil {
		return empty, err
	}
	if !req.Shareable && !dest.AllowLocal {
		return empty, failure("DESTINATION_PROHIBITED", "hosted destination requires shareable: true")
	}
	if req.ReplyTo != "" && !validWorkstreamPrincipal(req.ReplyTo) {
		return empty, failure("INVALID_REQUEST", "invalid reply_to principal")
	}
	for _, id := range []string{req.CausationID, req.CorrelationID} {
		if id != "" && validID(id) != nil {
			return empty, failure("INVALID_REQUEST", "causation_id and correlation_id must be UUIDs")
		}
	}
	srcKind := req.SourceKind
	if srcKind == "" {
		srcKind = "note"
	}
	sens := "local"
	if req.Shareable {
		sens = "shareable"
	}
	draft := Draft{
		Body:        req.Body,
		Kind:        srcKind,
		ClaimType:   "self",
		Sensitivity: sens,
		Scope:       Scope{Repo: repo, TaskID: "*", RunID: "*"},
	}
	if err := draft.validate(); err != nil {
		return empty, err
	}
	delivSens, err := s.lookupOwnedDeliverySensitivity(ctx, repo, req.DeliveryID, dest.AllowLocal)
	if err != nil {
		return empty, err
	}
	opSens := sens
	if delivSens == "local" {
		opSens = "local"
	}
	bodySum := sha256.Sum256([]byte(req.Body))
	intentHash, err := hashCampaignIntent(struct {
		Op            string `json:"op"`
		Repo          string `json:"repo"`
		Campaign      string `json:"campaign"`
		Destination   string `json:"destination"`
		DeliveryID    string `json:"delivery_id"`
		BodySHA256    string `json:"body_sha256"`
		SourceKind    string `json:"source_kind"`
		Shareable     bool   `json:"shareable"`
		ReplyTo       string `json:"reply_to,omitempty"`
		CausationID   string `json:"causation_id,omitempty"`
		CorrelationID string `json:"correlation_id,omitempty"`
		Summary       string `json:"summary,omitempty"`
		ItemKey       string `json:"item_key,omitempty"`
		ItemStatus    string `json:"item_status,omitempty"`
	}{"complete", repo, req.Campaign, dest.Name, req.DeliveryID, hex.EncodeToString(bodySum[:]), srcKind, req.Shareable, req.ReplyTo, req.CausationID, req.CorrelationID, req.Summary, req.ItemKey, req.ItemStatus})
	if err != nil {
		return empty, err
	}
	j, _, err := s.reserveOrLoadJournal(ctx, repo, req.Campaign, req.OpKey, "complete", opSens, intentHash, req.LeaseID, req.ItemKey, "assignment", req.ItemStatus, "", dest, true, false, req.ReplyTo != "")
	if err != nil {
		return empty, err
	}
	lease := j.leaseID
	if lease == "" {
		lease = req.LeaseID
	}
	deliv, err := s.CompleteEvent(ctx, CompleteEventRequest{
		RequestID:   j.actionRequestID,
		DeliveryID:  req.DeliveryID,
		LeaseID:     lease,
		Disposition: "handled",
		Draft:       &draft,
	}, dest)
	if err != nil {
		return empty, err
	}
	if deliv.Result == nil {
		return empty, failure("STORE_ERROR", "completed delivery returned no result reference")
	}
	resRef := *deliv.Result
	if j.state == "reserved" && req.ReplyTo != "" {
		if err := s.commitJournalStep(ctx, repo, req.Campaign, req.OpKey, "completed_reply_pending", &resRef, deliv.DeliveryID, deliv.Event.EventID, ""); err != nil {
			return empty, err
		}
	}
	var replyEvPtr *AgentEvent
	replyEvID := ""
	if req.ReplyTo != "" {
		causation := req.CausationID
		if causation == "" {
			causation = deliv.Event.EventID
		}
		corr := req.CorrelationID
		if corr == "" {
			corr = deliv.Event.CorrelationID
		}
		replyEv, err := s.PublishEvent(ctx, PublishEventRequest{
			RequestID:     j.replyRequestID,
			Repo:          repo,
			Kind:          "response",
			Ref:           resRef,
			Destination:   EventDestination{Type: "agent", Name: req.ReplyTo},
			CausationID:   causation,
			CorrelationID: corr,
			Summary:       req.Summary,
		}, dest)
		if err != nil {
			return empty, err
		}
		replyEvPtr = &replyEv
		replyEvID = replyEv.EventID
	}
	implicitStatus := ""
	if req.ItemKey != "" {
		implicitStatus = "completed"
	}
	fallbackSum := trimmedFallbackSummary(req.Summary, req.Body)
	if err := s.finalizeJournalAndLink(ctx, repo, req.Campaign, req.OpKey, opSens, req.ItemKey, "assignment", req.ItemStatus, implicitStatus, "", dest, nil, deliv.Event.EventID, deliv.DeliveryID, replyEvID, &resRef, fallbackSum); err != nil {
		return empty, err
	}
	return CampaignCompleteResult{
		OpKey:           req.OpKey,
		State:           "committed",
		Delivery:        deliv,
		Result:          resRef,
		ReplyEvent:      replyEvPtr,
		ActionRequestID: j.actionRequestID,
		ReplyRequestID:  j.replyRequestID,
	}, nil
}
