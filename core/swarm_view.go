package core

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"regexp"
	"sort"
	"strings"
	"time"

	"github.com/jackc/pgx/v5"
)

// The swarm view is the operator's read-mostly page over one campaign
// (docs/plans/swarm-view-design.md, decision 0022). Everything in this file
// reads stored state in one read-only REPEATABLE READ transaction. It adds no
// writer and takes no row or advisory lock.
//
// Two things leave here with different rules. Events are a log: each
// agent_event row of the campaign is sent once, keyed by its commit-ordered
// position. State is a level: the snapshot is the current roster, deliveries,
// waits, ledger items and owner attention, and is replaced whole.

const (
	SwarmSnapshotSchema = "cairn.swarm-snapshot/1"
	SwarmStreamSchema   = "cairn.swarm-stream/1"
	SwarmLandingSchema  = "cairn.swarm-landing/1"

	swarmLogWindowDefault = 200
	swarmLogWindowMax     = 500
	swarmLogBatchDefault  = 200
	swarmLogBatchMax      = 500
	swarmLedgerMax        = 500
)

// swarmPlaneIdentifier finds tracker identifiers such as CAIRN-829 in a task
// summary or ledger item. Plane is linked, never mirrored.
var swarmPlaneIdentifier = regexp.MustCompile(`\b[A-Z][A-Z0-9]{1,11}-[0-9]{1,6}\b`)

// swarmCampaignName is the campaign key of cairn.agent_team_campaign.
var swarmCampaignName = regexp.MustCompile(`^[A-Za-z0-9_.-]{1,64}$`)

// SwarmSnapshot is the whole current state of one campaign.
type SwarmSnapshot struct {
	Schema            string            `json:"schema"`
	Collection        string            `json:"collection"`
	ObservedAt        time.Time         `json:"observed_at"`
	RestoreGeneration int64             `json:"restore_generation"`
	MembershipEpoch   string            `json:"membership_epoch"`
	OperatorPrincipal string            `json:"operator_principal"`
	Campaign          TeamCampaign      `json:"campaign"`
	Members           []SwarmMember     `json:"members"`
	Lineage           []SwarmHandoff    `json:"lineage"`
	Handoff           *SwarmHandoffDue  `json:"handoff,omitempty"`
	Ledger            []SwarmLedgerItem `json:"ledger"`
	LedgerTruncated   bool              `json:"ledger_truncated,omitempty"`
	Flow              SwarmFlow         `json:"flow"`
	NeedsYou          SwarmNeedsYou     `json:"needs_you"`
	ContextRoom       SwarmContextRoom  `json:"context_room"`
	// SectionErrors names a section whose read failed. The section is then
	// empty and the rest of the snapshot is still current.
	SectionErrors map[string]string `json:"section_errors,omitempty"`
}

// SwarmMember is one campaign member with what the store knows about its
// session. Membership is a principal string; it need not be a session.
type SwarmMember struct {
	TeamMember
	AgentID     string        `json:"agent_id,omitempty"`
	Label       string        `json:"label,omitempty"`
	RoleAddress string        `json:"role_address,omitempty"`
	Session     *SwarmSession `json:"session,omitempty"`
	Holds       []SwarmHold   `json:"holds"`
	PlaneIssues []string      `json:"plane_issues,omitempty"`
}

// SwarmSession is reported context, not attestation. LastSeen is set only when
// the session is not online, so a heartbeat does not change the snapshot.
type SwarmSession struct {
	Presence        string     `json:"presence"`
	State           string     `json:"state,omitempty"`
	Harness         string     `json:"harness,omitempty"`
	Model           string     `json:"model,omitempty"`
	ObservedModel   string     `json:"observed_model,omitempty"`
	Project         string     `json:"project,omitempty"`
	Workspace       string     `json:"workspace,omitempty"`
	TaskSummary     string     `json:"task_summary,omitempty"`
	NativeSessionID string     `json:"native_session_id,omitempty"`
	StartedAt       time.Time  `json:"started_at"`
	LastSeen        *time.Time `json:"last_seen,omitempty"`
	CompactionCount int        `json:"compaction_count"`
}

// SwarmHold is an open ledger item owned by the member.
type SwarmHold struct {
	Kind        string     `json:"kind"`
	ItemKey     string     `json:"item_key"`
	Status      string     `json:"status"`
	Summary     string     `json:"summary"`
	DueAt       *time.Time `json:"due_at,omitempty"`
	UpdatedAt   time.Time  `json:"updated_at"`
	PlaneIssues []string   `json:"plane_issues,omitempty"`
}

// SwarmLedgerItem is a ledger row with its journal link. The link annotates an
// event in the log; it never decides whether the event is in the log.
type SwarmLedgerItem struct {
	Kind        string     `json:"kind"`
	ItemKey     string     `json:"item_key"`
	Owner       string     `json:"owner"`
	Status      string     `json:"status"`
	Open        bool       `json:"open"`
	Summary     string     `json:"summary"`
	EventID     string     `json:"event_id,omitempty"`
	DeliveryID  string     `json:"delivery_id,omitempty"`
	DueAt       *time.Time `json:"due_at,omitempty"`
	UpdatedAt   time.Time  `json:"updated_at"`
	PlaneIssues []string   `json:"plane_issues,omitempty"`
}

// SwarmHandoff is one coordinator handoff, oldest first.
type SwarmHandoff struct {
	HandoffID     string    `json:"handoff_id"`
	RoleAddress   string    `json:"role_address"`
	FromPrincipal string    `json:"from_principal"`
	FromLabel     string    `json:"from_label,omitempty"`
	ToPrincipal   string    `json:"to_principal"`
	ToLabel       string    `json:"to_label,omitempty"`
	Reason        string    `json:"reason"`
	CreatedAt     time.Time `json:"created_at"`
}

// SwarmHandoffDue is the existing coordinator handoff check: session age or
// compactions past the campaign's thresholds. Since is when the age threshold
// was reached; the store does not record when a compaction count was reached.
type SwarmHandoffDue struct {
	HolderPrincipal string     `json:"holder_principal"`
	Reasons         []string   `json:"reasons"`
	Since           *time.Time `json:"since,omitempty"`
}

// swarmScope is computed once per read and handed to every section reader.
type swarmScope struct {
	Repo       string
	Campaign   TeamCampaign // with members
	Principals []string     // every principal that attributes an event to the campaign
	// MemberPrincipals are the member rows only, active or not.
	MemberPrincipals []string
	// Sessions holds the session row of each principal of the form agent/<uuid>.
	Sessions   map[string]AgentInstance // by agent_id
	Operator   string
	Now        time.Time
	Generation int64
	Epoch      string
}

func (sc swarmScope) isMember(principal string) bool {
	i := sort.SearchStrings(sc.Principals, principal)
	return i < len(sc.Principals) && sc.Principals[i] == principal
}

func swarmAgentID(principal string) string {
	id := strings.TrimPrefix(principal, "agent/")
	if id == principal || validID(id) != nil {
		return ""
	}
	return id
}

func swarmPlaneIssues(texts ...string) []string {
	seen := map[string]bool{}
	var out []string
	for _, text := range texts {
		for _, id := range swarmPlaneIdentifier.FindAllString(text, -1) {
			if !seen[id] {
				seen[id] = true
				out = append(out, id)
			}
		}
	}
	return out
}

func swarmLedgerOpen(kind, status string) bool {
	switch kind {
	case "assignment":
		return status == "planned" || status == "dispatched" || status == "in_progress" || status == "review"
	case "decision":
		return status == "open"
	case "blocker":
		return status == "open" || status == "escalated"
	case "hold":
		return status == "active"
	}
	return false
}

// beginSwarmRead opens the one transaction of a swarm view read. It mirrors
// beginLevel without the row locks that a read-only transaction cannot take:
// the restore pause and the generation are read plainly. A view that reads a
// moment before a restore fence only shows state that was current then; the
// cursor carries the generation, so the next read refuses it.
func (s *Store) beginSwarmRead(ctx context.Context) (pgx.Tx, error) {
	if err := s.requireOperator(); err != nil {
		return nil, err
	}
	tx, err := s.pool.BeginTx(ctx, pgx.TxOptions{IsoLevel: pgx.RepeatableRead, AccessMode: pgx.ReadOnly})
	if err != nil {
		return nil, err
	}
	fail := func(err error) (pgx.Tx, error) {
		tx.Rollback(context.Background())
		return nil, err
	}
	if _, err = tx.Exec(ctx, `SELECT set_config('cairn.caller',$1,true), set_config('cairn.witness','testimony',true)`, s.channel.Principal); err != nil {
		return fail(err)
	}
	var paused bool
	if err = tx.QueryRow(ctx, `SELECT paused FROM cairn.restore_admission WHERE singleton`).Scan(&paused); err != nil {
		return fail(err)
	}
	if paused {
		return fail(failure("RESTORE_PAUSED", "restore reconciliation is incomplete; normal service is paused"))
	}
	if err = s.checkCompatibilityTx(ctx, tx); err != nil {
		return fail(err)
	}
	return tx, nil
}

func (s *Store) swarmRepo(repo string) (string, error) {
	if repo == "" {
		repo = s.channel.Repo
	}
	if strings.TrimSpace(repo) == "" || repo == "*" || len(repo) > 256 {
		return "", failure("INVALID_REQUEST", "collection is required")
	}
	return repo, s.checkRepo(repo)
}

func swarmPresence(a AgentInstance, generation int64, now time.Time) string {
	switch {
	case a.Stopped:
		return "stopped"
	case a.DatabaseGeneration != generation:
		return "replaced"
	case !a.ExpiresAt.After(now):
		return "offline"
	}
	return "online"
}

// swarmScopeTx reads the campaign, every principal that attributes an event to
// it, and the session rows of those principals.
func (s *Store) swarmScopeTx(ctx context.Context, tx pgx.Tx, repo, campaign string) (swarmScope, []SwarmHandoff, map[string]string, error) {
	sc := swarmScope{Repo: repo, Operator: s.channel.Principal, Sessions: map[string]AgentInstance{}}
	if !swarmCampaignName.MatchString(campaign) {
		return sc, nil, nil, failure("INVALID_REQUEST", "campaign name is required")
	}
	if err := tx.QueryRow(ctx, `SELECT clock_timestamp(), (SELECT generation FROM cairn.retrieval_generation WHERE singleton)`).Scan(&sc.Now, &sc.Generation); err != nil {
		return sc, nil, nil, err
	}
	camp, err := readTeamCampaignTx(ctx, tx, repo, campaign, true, true)
	if err != nil {
		return sc, nil, nil, err
	}
	sc.Campaign = camp
	set := map[string]bool{camp.RootAgent: true}
	for _, m := range camp.Members {
		set[m.Member] = true
		sc.MemberPrincipals = append(sc.MemberPrincipals, m.Member)
	}
	sort.Strings(sc.MemberPrincipals)

	// Role addresses and every coordinator that held the root role. A
	// coordinator that handed off stays an inactive member row, and its
	// events stay in the log.
	roleHolders := map[string]string{}
	rows, err := tx.Query(ctx, `SELECT role_address, holder_principal FROM cairn.campaign_role_binding WHERE repo=$1 AND campaign=$2 ORDER BY role_name`, repo, campaign)
	if err != nil {
		return sc, nil, nil, err
	}
	for rows.Next() {
		var address, holder string
		if err = rows.Scan(&address, &holder); err != nil {
			rows.Close()
			return sc, nil, nil, err
		}
		set[address], set[holder] = true, true
		roleHolders[holder] = address
	}
	rows.Close()
	if err = rows.Err(); err != nil {
		return sc, nil, nil, err
	}
	lineage := []SwarmHandoff{}
	rows, err = tx.Query(ctx, `SELECT handoff_id::text, role_address, from_principal, to_principal, reason, created_at FROM cairn.campaign_coordinator_handoff WHERE repo=$1 AND campaign=$2 ORDER BY created_at, handoff_id`, repo, campaign)
	if err != nil {
		return sc, nil, nil, err
	}
	for rows.Next() {
		var h SwarmHandoff
		if err = rows.Scan(&h.HandoffID, &h.RoleAddress, &h.FromPrincipal, &h.ToPrincipal, &h.Reason, &h.CreatedAt); err != nil {
			rows.Close()
			return sc, nil, nil, err
		}
		set[h.FromPrincipal], set[h.ToPrincipal] = true, true
		lineage = append(lineage, h)
	}
	rows.Close()
	if err = rows.Err(); err != nil {
		return sc, nil, nil, err
	}
	for p := range set {
		if p != "" {
			sc.Principals = append(sc.Principals, p)
		}
	}
	sort.Strings(sc.Principals)

	var ids []string
	for _, p := range sc.Principals {
		if id := swarmAgentID(p); id != "" {
			ids = append(ids, id)
		}
	}
	if len(ids) > 0 {
		rows, err = tx.Query(ctx, `SELECT `+agentSessionColumns+` FROM cairn.agent_session WHERE repo=$1 AND agent_id=ANY($2::uuid[])`, repo, ids)
		if err != nil {
			return sc, nil, nil, err
		}
		for rows.Next() {
			a, sErr := scanAgentSession(rows)
			if sErr != nil {
				rows.Close()
				return sc, nil, nil, sErr
			}
			sc.Sessions[a.AgentID] = a
		}
		rows.Close()
		if err = rows.Err(); err != nil {
			return sc, nil, nil, err
		}
	}
	for i := range lineage {
		if a, ok := sc.Sessions[swarmAgentID(lineage[i].FromPrincipal)]; ok {
			lineage[i].FromLabel = a.DisplayName
		}
		if a, ok := sc.Sessions[swarmAgentID(lineage[i].ToPrincipal)]; ok {
			lineage[i].ToLabel = a.DisplayName
		}
	}

	// The epoch names the set of principals. When it changes, events that are
	// behind a client's cursor may now belong to the campaign, so the log
	// window is sent again from the start.
	h := sha256.New()
	h.Write([]byte(repo + "\x00" + camp.Campaign + "\x00" + camp.CreatedAt.UTC().Format(time.RFC3339Nano)))
	for _, p := range sc.Principals {
		h.Write([]byte("\x00" + p))
	}
	sc.Epoch = hex.EncodeToString(h.Sum(nil))[:24]
	return sc, lineage, roleHolders, nil
}

// swarmSection runs one section reader inside a savepoint, so that a failed
// read leaves the transaction usable and the other sections current.
func swarmSection(ctx context.Context, tx pgx.Tx, errs map[string]string, name string, read func(pgx.Tx) error) {
	sp, err := tx.Begin(ctx)
	if err == nil {
		if err = read(sp); err == nil {
			err = sp.Commit(ctx)
		} else {
			sp.Rollback(ctx)
		}
	}
	if err != nil {
		message := err.Error()
		if len(message) > 300 {
			message = message[:300]
		}
		errs[name] = message
	}
}

func (s *Store) swarmSnapshotTx(ctx context.Context, tx pgx.Tx, sc swarmScope, lineage []SwarmHandoff, roleHolders map[string]string) (SwarmSnapshot, error) {
	snap := SwarmSnapshot{
		Schema: SwarmSnapshotSchema, Collection: sc.Repo, ObservedAt: sc.Now, RestoreGeneration: sc.Generation,
		MembershipEpoch: sc.Epoch, OperatorPrincipal: sc.Operator, Campaign: sc.Campaign,
		Members: []SwarmMember{}, Lineage: lineage, Ledger: []SwarmLedgerItem{},
		Flow: emptySwarmFlow(), NeedsYou: emptySwarmNeedsYou(), ContextRoom: emptySwarmContextRoom(sc),
	}
	snap.Campaign.Members = nil

	items, err := loadCampaignLedgerItemsTx(ctx, tx, sc.Repo, sc.Campaign.Campaign, true)
	if err != nil {
		return snap, err
	}
	holds := map[string][]SwarmHold{}
	for _, it := range items {
		open := swarmLedgerOpen(it.Kind, it.Status)
		issues := swarmPlaneIssues(it.ItemKey, it.Summary)
		if open {
			holds[it.Owner] = append(holds[it.Owner], SwarmHold{Kind: it.Kind, ItemKey: it.ItemKey, Status: it.Status, Summary: it.Summary, DueAt: it.DueAt, UpdatedAt: it.UpdatedAt, PlaneIssues: issues})
		}
		if len(snap.Ledger) == swarmLedgerMax {
			snap.LedgerTruncated = true
			continue
		}
		snap.Ledger = append(snap.Ledger, SwarmLedgerItem{Kind: it.Kind, ItemKey: it.ItemKey, Owner: it.Owner, Status: it.Status, Open: open, Summary: it.Summary, EventID: it.EventID, DeliveryID: it.DeliveryID, DueAt: it.DueAt, UpdatedAt: it.UpdatedAt, PlaneIssues: issues})
	}

	for _, m := range sc.Campaign.Members {
		row := SwarmMember{TeamMember: m, AgentID: swarmAgentID(m.Member), Holds: holds[m.Member]}
		if row.Holds == nil {
			row.Holds = []SwarmHold{}
		}
		if m.Active {
			row.RoleAddress = roleHolders[m.Member]
		}
		texts := []string{}
		for _, h := range row.Holds {
			texts = append(texts, h.ItemKey, h.Summary)
		}
		if a, ok := sc.Sessions[row.AgentID]; ok {
			row.Label = a.DisplayName
			presence := swarmPresence(a, sc.Generation, sc.Now)
			session := &SwarmSession{
				Presence: presence, State: a.Metadata.State, Harness: a.Metadata.Harness, Model: a.Metadata.Model,
				ObservedModel: a.Metadata.ObservedModel, Project: a.Metadata.Project, Workspace: a.Metadata.Workspace,
				TaskSummary: a.Metadata.TaskSummary, NativeSessionID: a.NativeSessionID, StartedAt: a.StartedAt,
				CompactionCount: a.CompactionCount,
			}
			if presence != "online" {
				seen := a.LastSeen
				session.LastSeen = &seen
				session.State = ""
			}
			row.Session = session
			texts = append([]string{a.Metadata.TaskSummary}, texts...)
		}
		row.PlaneIssues = swarmPlaneIssues(texts...)
		snap.Members = append(snap.Members, row)
	}

	// The existing handoff check, read without its clock arithmetic so the
	// value is stable between reads.
	if a, ok := sc.Sessions[swarmAgentID(sc.Campaign.RootAgent)]; ok && sc.Campaign.Active {
		due := SwarmHandoffDue{HolderPrincipal: sc.Campaign.RootAgent, Reasons: []string{}}
		if limit := sc.Campaign.MaxCoordinatorAgeSeconds; limit != nil {
			if at := a.StartedAt.Add(time.Duration(*limit) * time.Second); !at.After(sc.Now) {
				due.Reasons = append(due.Reasons, "coordinator session age reached max_coordinator_age_seconds")
				due.Since = &at
			}
		}
		if limit := sc.Campaign.MaxCoordinatorCompactions; limit != nil && a.CompactionCount >= *limit {
			due.Reasons = append(due.Reasons, "coordinator compaction count reached max_coordinator_compactions")
		}
		if len(due.Reasons) > 0 {
			snap.Handoff = &due
		}
	}

	errs := map[string]string{}
	swarmSection(ctx, tx, errs, "flow", func(sp pgx.Tx) error {
		flow, err := s.swarmFlowTx(ctx, sp, sc)
		if err == nil {
			snap.Flow = flow
		}
		return err
	})
	swarmSection(ctx, tx, errs, "needs_you", func(sp pgx.Tx) error {
		needs, err := s.swarmNeedsYouTx(ctx, sp, sc, snap)
		if err == nil {
			snap.NeedsYou = needs
		}
		return err
	})
	swarmSection(ctx, tx, errs, "context_room", func(sp pgx.Tx) error {
		room, err := s.swarmContextRoomTx(ctx, sp, sc)
		if err == nil {
			snap.ContextRoom = room
		}
		return err
	})
	if len(errs) > 0 {
		snap.SectionErrors = errs
	}
	return snap, nil
}

// SwarmSnapshotRequest names one campaign of one collection.
type SwarmSnapshotRequest struct {
	Repo     string `json:"repo,omitempty"`
	Campaign string `json:"campaign"`
}

// GetSwarmSnapshot reads the current state of one campaign.
func (s *Store) GetSwarmSnapshot(ctx context.Context, req SwarmSnapshotRequest) (SwarmSnapshot, error) {
	repo, err := s.swarmRepo(req.Repo)
	if err != nil {
		return SwarmSnapshot{}, err
	}
	tx, err := s.beginSwarmRead(ctx)
	if err != nil {
		return SwarmSnapshot{}, err
	}
	defer tx.Rollback(context.Background())
	sc, lineage, holders, err := s.swarmScopeTx(ctx, tx, repo, req.Campaign)
	if err != nil {
		return SwarmSnapshot{}, err
	}
	snap, err := s.swarmSnapshotTx(ctx, tx, sc, lineage, holders)
	if err != nil {
		return SwarmSnapshot{}, err
	}
	return snap, tx.Commit(ctx)
}

// SwarmEvent is the envelope of one campaign event. It carries no record
// body: the page loads a body only when the owner opens the event.
type SwarmEvent struct {
	Position      int64            `json:"position"`
	EventID       string           `json:"event_id"`
	Kind          string           `json:"kind"`
	From          string           `json:"from"`
	To            EventDestination `json:"to"`
	Summary       string           `json:"summary,omitempty"`
	Action        string           `json:"action,omitempty"`
	Blocker       bool             `json:"blocker,omitempty"`
	Supersedes    string           `json:"supersedes,omitempty"`
	CausationID   string           `json:"causation_id,omitempty"`
	CorrelationID string           `json:"correlation_id,omitempty"`
	Grouped       bool             `json:"grouped,omitempty"`
	ExpectBy      *time.Time       `json:"expect_by,omitempty"`
	ExpectNextBy  *time.Time       `json:"expect_next_by,omitempty"`
	Routing       *EventRouting    `json:"routing,omitempty"`
	Ref           RecordVersionRef `json:"ref"`
	CreatedAt     time.Time        `json:"created_at"`
	// Cursor resumes the log after this event. It is the stream ID.
	Cursor string `json:"cursor"`
}

// swarmCursor binds a log position to the collection, the campaign, the
// restore generation and the membership epoch it was read under.
type swarmCursor struct {
	Version    int    `json:"v"`
	Collection string `json:"c"`
	Campaign   string `json:"n"`
	Generation int64  `json:"g"`
	Epoch      string `json:"m"`
	Position   int64  `json:"p"`
}

func encodeSwarmCursor(sc swarmScope, position int64) string {
	raw, _ := json.Marshal(swarmCursor{Version: 1, Collection: sc.Repo, Campaign: sc.Campaign.Campaign, Generation: sc.Generation, Epoch: sc.Epoch, Position: position})
	return base64.RawURLEncoding.EncodeToString(raw)
}

// decodeSwarmCursor returns nil for an empty cursor. A cursor for another
// collection or campaign is a caller error. A cursor from before a restore is
// stale: the page reloads.
func decodeSwarmCursor(raw string, sc swarmScope) (*swarmCursor, error) {
	if raw == "" {
		return nil, nil
	}
	invalid := failure("INVALID_REQUEST", "invalid swarm cursor")
	if len(raw) > 2048 || strings.Contains(raw, "=") {
		return nil, invalid
	}
	decoded, err := base64.RawURLEncoding.DecodeString(raw)
	if err != nil || len(decoded) == 0 {
		return nil, invalid
	}
	var c swarmCursor
	dec := json.NewDecoder(bytes.NewReader(decoded))
	dec.DisallowUnknownFields()
	if err := dec.Decode(&c); err != nil {
		return nil, invalid
	}
	var extra any
	if err := dec.Decode(&extra); !errors.Is(err, io.EOF) {
		return nil, invalid
	}
	if c.Version != 1 || c.Position < 0 || c.Epoch == "" || c.Collection != sc.Repo || c.Campaign != sc.Campaign.Campaign {
		return nil, invalid
	}
	if c.Generation != sc.Generation {
		return nil, failure("STALE_CURSOR", "swarm cursor predates the restore fence; reload the page")
	}
	return &c, nil
}

// swarmEventInCampaign decides whether an event belongs to the campaign's log.
// It uses only facts fixed when the event commits, so a reader that asks for
// positions above N can never be shown, later, an event at or below N:
//
//   - the publisher is a campaign principal;
//   - the event is addressed to a campaign principal (destination_name is the
//     resolved recipient, also for a notice whose delivery row is written at
//     a later turn);
//   - routing_info names the campaign (role addresses and hierarchy routing);
//   - a topic event has a delivery to a campaign principal. Topic deliveries
//     are written with the event and never added later.
//
// A pool request gets its delivery at assignment, after the event commits, so
// a pool delivery is not used. The journal link to a ledger item is written
// in a later transaction and is never used either. Parameters: $1 repo,
// $2 principals, $3 campaign name, $4 campaign creation time.
const swarmEventInCampaign = `e.repo=$1 AND e.created_at>=$4 AND (
	e.publisher=ANY($2)
	OR (e.destination_type='agent' AND e.destination_name=ANY($2))
	OR e.routing_info->>'campaign'=$3
	OR (e.destination_type='topic' AND EXISTS(SELECT 1 FROM cairn.agent_delivery a WHERE a.event_id=e.event_id AND a.consumer=ANY($2))))`

func (s *Store) swarmEventsTx(ctx context.Context, tx pgx.Tx, sc swarmScope, query string, args ...any) ([]SwarmEvent, error) {
	base := []any{sc.Repo, sc.Principals, sc.Campaign.Campaign, sc.Campaign.CreatedAt}
	rows, err := tx.Query(ctx, query, append(base, args...)...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []SwarmEvent{}
	for rows.Next() {
		e, err := scanEvent(rows)
		if err != nil {
			return nil, err
		}
		if e.Routing != nil {
			e.Routing.LocalOnly = false
		}
		out = append(out, SwarmEvent{
			Position: e.Position, EventID: e.EventID, Kind: e.Kind, From: e.From, To: e.Destination, Summary: e.Summary,
			Action: e.Action, Blocker: e.Blocker, Supersedes: e.Supersedes, CausationID: e.CausationID,
			CorrelationID: e.CorrelationID, Grouped: e.ResponseGroup != nil, ExpectBy: e.ExpectBy, ExpectNextBy: e.ExpectNextBy,
			Routing: e.Routing, Ref: e.Ref, CreatedAt: e.CreatedAt, Cursor: encodeSwarmCursor(sc, e.Position),
		})
	}
	return out, rows.Err()
}

// SwarmStreamRequest is one read of the stream. Cursor is the last stream ID
// the client received, or empty for a new log window.
type SwarmStreamRequest struct {
	Repo     string `json:"repo,omitempty"`
	Campaign string `json:"campaign"`
	Cursor   string `json:"cursor,omitempty"`
	Window   int    `json:"window,omitempty"`
	Batch    int    `json:"batch,omitempty"`
}

// SwarmStreamRead is the log since the cursor and the snapshot, read in the
// same transaction.
type SwarmStreamRead struct {
	Schema string `json:"schema"`
	// Resync is set when Events is a new log window and not a continuation:
	// there was no cursor, or the campaign's principals changed since the
	// cursor was issued. The client replaces its log with Events.
	Resync       bool         `json:"resync"`
	ResyncReason string       `json:"resync_reason,omitempty"`
	Events       []SwarmEvent `json:"events"`
	// More is set when Events was cut at the batch size. The next read
	// continues from Cursor without waiting.
	More bool `json:"more"`
	// Cursor resumes after the last event of this read. With no event it
	// still carries the current generation and epoch.
	Cursor   string        `json:"cursor"`
	Snapshot SwarmSnapshot `json:"snapshot"`
}

// ReadSwarmStream reads the campaign's events after the cursor and the
// current snapshot in one read-only REPEATABLE READ transaction.
//
// Every insert into cairn.agent_event takes the collection's advisory lock and
// holds it to commit, so positions become visible in order. One snapshot
// therefore never contains a position whose predecessor commits later, and
// "position above the cursor" skips nothing.
func (s *Store) ReadSwarmStream(ctx context.Context, req SwarmStreamRequest) (SwarmStreamRead, error) {
	out := SwarmStreamRead{Schema: SwarmStreamSchema, Events: []SwarmEvent{}}
	repo, err := s.swarmRepo(req.Repo)
	if err != nil {
		return out, err
	}
	window, batch := req.Window, req.Batch
	if window == 0 {
		window = swarmLogWindowDefault
	}
	if batch == 0 {
		batch = swarmLogBatchDefault
	}
	if window < 1 || window > swarmLogWindowMax || batch < 1 || batch > swarmLogBatchMax {
		return out, failure("INVALID_REQUEST", "window or batch out of range")
	}
	tx, err := s.beginSwarmRead(ctx)
	if err != nil {
		return out, err
	}
	defer tx.Rollback(context.Background())
	sc, lineage, holders, err := s.swarmScopeTx(ctx, tx, repo, req.Campaign)
	if err != nil {
		return out, err
	}
	cursor, err := decodeSwarmCursor(req.Cursor, sc)
	if err != nil {
		return out, err
	}
	var maxPosition int64
	if err = tx.QueryRow(ctx, `SELECT COALESCE(max(position),0) FROM cairn.agent_event WHERE repo=$1`, repo).Scan(&maxPosition); err != nil {
		return out, err
	}
	if cursor != nil && cursor.Position > maxPosition {
		return out, failure("STALE_CURSOR", "swarm cursor is ahead of the collection; reload the page")
	}
	position := int64(0)
	switch {
	case cursor == nil:
		out.Resync, out.ResyncReason = true, "new_window"
	case cursor.Epoch != sc.Epoch:
		out.Resync, out.ResyncReason = true, "membership_changed"
	default:
		position = cursor.Position
	}
	if out.Resync {
		// The newest window, oldest first.
		events, qErr := s.swarmEventsTx(ctx, tx, sc, `SELECT `+eventColumns+` FROM cairn.agent_event e WHERE `+swarmEventInCampaign+` ORDER BY e.position DESC LIMIT $5`, window)
		if qErr != nil {
			return out, qErr
		}
		for i, j := 0, len(events)-1; i < j; i, j = i+1, j-1 {
			events[i], events[j] = events[j], events[i]
		}
		out.Events = events
		// Everything visible in this snapshot is either in the window or
		// older than it, so the log continues after the newest position.
		position = maxPosition
	} else {
		events, qErr := s.swarmEventsTx(ctx, tx, sc, `SELECT `+eventColumns+` FROM cairn.agent_event e WHERE `+swarmEventInCampaign+` AND e.position>$5 ORDER BY e.position LIMIT $6`, position, batch+1)
		if qErr != nil {
			return out, qErr
		}
		if len(events) > batch {
			events, out.More = events[:batch], true
			position = events[batch-1].Position
		} else {
			position = maxPosition
		}
		out.Events = events
	}
	out.Cursor = encodeSwarmCursor(sc, position)
	if out.Snapshot, err = s.swarmSnapshotTx(ctx, tx, sc, lineage, holders); err != nil {
		return out, err
	}
	return out, tx.Commit(ctx)
}

// SwarmLanding lists the active campaigns of a collection and the online
// sessions that belong to none, so the page is useful before a swarm has a
// campaign record.
type SwarmLanding struct {
	Schema            string                 `json:"schema"`
	Collection        string                 `json:"collection"`
	ObservedAt        time.Time              `json:"observed_at"`
	RestoreGeneration int64                  `json:"restore_generation"`
	OperatorPrincipal string                 `json:"operator_principal"`
	Campaigns         []SwarmLandingCampaign `json:"campaigns"`
	Unaffiliated      []SwarmLandingSession  `json:"unaffiliated"`
	NeedsYou          SwarmNeedsYou          `json:"needs_you"`
	SectionErrors     map[string]string      `json:"section_errors,omitempty"`
}

type SwarmLandingCampaign struct {
	TeamCampaign
	RootLabel     string `json:"root_label,omitempty"`
	ActiveMembers int    `json:"active_members"`
	OnlineMembers int    `json:"online_members"`
}

type SwarmLandingSession struct {
	AgentID     string    `json:"agent_id"`
	Label       string    `json:"label"`
	State       string    `json:"state,omitempty"`
	Harness     string    `json:"harness,omitempty"`
	Model       string    `json:"model,omitempty"`
	Project     string    `json:"project,omitempty"`
	TaskSummary string    `json:"task_summary,omitempty"`
	StartedAt   time.Time `json:"started_at"`
}

// GetSwarmLanding reads the landing page of one collection.
func (s *Store) GetSwarmLanding(ctx context.Context, repo string) (SwarmLanding, error) {
	out := SwarmLanding{Schema: SwarmLandingSchema, Campaigns: []SwarmLandingCampaign{}, Unaffiliated: []SwarmLandingSession{}, NeedsYou: emptySwarmNeedsYou(), OperatorPrincipal: s.channel.Principal}
	repo, err := s.swarmRepo(repo)
	if err != nil {
		return out, err
	}
	out.Collection = repo
	tx, err := s.beginSwarmRead(ctx)
	if err != nil {
		return out, err
	}
	defer tx.Rollback(context.Background())
	if err = tx.QueryRow(ctx, `SELECT clock_timestamp(), (SELECT generation FROM cairn.retrieval_generation WHERE singleton)`).Scan(&out.ObservedAt, &out.RestoreGeneration); err != nil {
		return out, err
	}
	rows, err := tx.Query(ctx, `SELECT campaign FROM cairn.agent_team_campaign WHERE repo=$1 AND active ORDER BY created_at DESC, campaign LIMIT 200`, repo)
	if err != nil {
		return out, err
	}
	names, err := pgx.CollectRows(rows, pgx.RowTo[string])
	if err != nil {
		return out, err
	}
	rows, err = tx.Query(ctx, `SELECT `+agentSessionColumns+` FROM cairn.agent_session WHERE repo=$1 AND NOT stopped AND expires_at>clock_timestamp() AND database_generation=$2 ORDER BY ordinal`, repo, out.RestoreGeneration)
	if err != nil {
		return out, err
	}
	online := map[string]AgentInstance{}
	var order []string
	for rows.Next() {
		a, sErr := scanAgentSession(rows)
		if sErr != nil {
			rows.Close()
			return out, sErr
		}
		online["agent/"+a.AgentID] = a
		order = append(order, "agent/"+a.AgentID)
	}
	rows.Close()
	if err = rows.Err(); err != nil {
		return out, err
	}
	affiliated := map[string]bool{}
	for _, name := range names {
		camp, cErr := readTeamCampaignTx(ctx, tx, repo, name, true, true)
		if cErr != nil {
			return out, cErr
		}
		row := SwarmLandingCampaign{TeamCampaign: camp}
		for _, m := range camp.Members {
			if !m.Active {
				continue
			}
			row.ActiveMembers++
			affiliated[m.Member] = true
			if _, ok := online[m.Member]; ok {
				row.OnlineMembers++
			}
		}
		if a, ok := online[camp.RootAgent]; ok {
			row.RootLabel = a.DisplayName
		}
		row.Members = nil
		out.Campaigns = append(out.Campaigns, row)
	}
	for _, principal := range order {
		if affiliated[principal] {
			continue
		}
		a := online[principal]
		out.Unaffiliated = append(out.Unaffiliated, SwarmLandingSession{AgentID: a.AgentID, Label: a.DisplayName, State: a.Metadata.State, Harness: a.Metadata.Harness, Model: a.Metadata.Model, Project: a.Metadata.Project, TaskSummary: a.Metadata.TaskSummary, StartedAt: a.StartedAt})
	}
	errs := map[string]string{}
	swarmSection(ctx, tx, errs, "needs_you", func(sp pgx.Tx) error {
		needs, err := s.swarmLandingNeedsYouTx(ctx, sp, repo, out.ObservedAt, out.RestoreGeneration, affiliated)
		if err == nil {
			out.NeedsYou = needs
		}
		return err
	})
	if len(errs) > 0 {
		out.SectionErrors = errs
	}
	return out, tx.Commit(ctx)
}
