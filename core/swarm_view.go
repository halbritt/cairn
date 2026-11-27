package core

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
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

	swarmLogWindowDefault = SwarmFlowEventWindow
	swarmLogWindowMax     = 500
	swarmLogBatchDefault  = 200
	swarmLogBatchMax      = 500
	swarmLedgerMax        = 500
	swarmCursorMax        = 8192
	swarmLandingEvents    = 50
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
	HolderPrincipal string   `json:"holder_principal"`
	Reasons         []string `json:"reasons"`
	// Since is the first moment the handoff was due under the campaign's
	// limits as they are now. It is absent when a limit that is reached has
	// no recorded crossing time. It is derived on each read and nothing
	// stores it: while the limits stay as they are it does not move, and
	// reaching a second limit does not move it; changing a limit dates it
	// again from the new limit.
	Since *time.Time `json:"since,omitempty"`
	// AgeReachedAt is when the session reached the age limit, when it has.
	AgeReachedAt *time.Time `json:"age_reached_at,omitempty"`
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
	// Fence is the identity of the latest restore fence, or empty when the
	// store was never restored. Two restores of one backup reach the same
	// generation number with different fences.
	Fence string
	Epoch string
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
	if err := tx.QueryRow(ctx, `SELECT clock_timestamp(), (SELECT generation FROM cairn.retrieval_generation WHERE singleton), COALESCE((SELECT fence_id::text FROM cairn.restore_fence ORDER BY generation DESC LIMIT 1),'')`).Scan(&sc.Now, &sc.Generation, &sc.Fence); err != nil {
		return sc, nil, nil, err
	}
	camp, err := readTeamCampaignTx(ctx, tx, repo, campaign, true, true)
	if err != nil {
		return sc, nil, nil, err
	}
	sc.Campaign = camp
	for _, m := range camp.Members {
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
		lineage = append(lineage, h)
	}
	rows.Close()
	if err = rows.Err(); err != nil {
		return sc, nil, nil, err
	}
	// The principal set has one definition, shared with the landing read.
	if err = tx.QueryRow(ctx, `SELECT `+swarmCampaignPrincipals+` FROM cairn.agent_team_campaign c WHERE c.repo=$1 AND c.campaign=$2`, repo, campaign).Scan(&sc.Principals); err != nil {
		return sc, nil, nil, err
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

	// The epoch names everything that decides which events behind a cursor
	// belong to the campaign. When it changes, the log window is sent again
	// from the start. Two things decide it:
	//
	//   - the set of campaign principals;
	//   - every coordinator handoff in the collection, of any campaign, that
	//     moves work from or to one of those principals. A handoff rewrites
	//     the consumer of the old coordinator's pending deliveries (and the
	//     recipient of its queued notices) to the successor. It is the only
	//     writer that changes who a delivery belongs to, so a topic event can
	//     enter or leave this campaign's log only then.
	rows, err = tx.Query(ctx, `SELECT handoff_id::text FROM cairn.campaign_coordinator_handoff WHERE repo=$1 AND (from_principal=ANY($2) OR to_principal=ANY($2)) ORDER BY handoff_id`, repo, sc.Principals)
	if err != nil {
		return sc, nil, nil, err
	}
	transfers, err := pgx.CollectRows(rows, pgx.RowTo[string])
	if err != nil {
		return sc, nil, nil, err
	}
	h := sha256.New()
	h.Write([]byte(repo + "\x00" + camp.Campaign + "\x00" + camp.CreatedAt.UTC().Format(time.RFC3339Nano)))
	for _, p := range sc.Principals {
		h.Write([]byte("\x00" + p))
	}
	h.Write([]byte("\x00handoffs"))
	for _, id := range transfers {
		h.Write([]byte("\x00" + id))
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
		// Since is the first moment the handoff was due under the limits in
		// force now. It is given only when every limit that is reached has a
		// known crossing time: a crossing whose time the store does not hold
		// may have come first, and a time that moves forward when a second
		// limit is reached would make the row look newer than it is. The
		// view stores nothing, so an edited limit dates the handoff again.
		var first *time.Time
		dated := true
		earlier := func(at time.Time) {
			if first == nil || at.Before(*first) {
				first = &at
			}
		}
		if limit := sc.Campaign.MaxCoordinatorAgeSeconds; limit != nil {
			if at := a.StartedAt.Add(time.Duration(*limit) * time.Second); !at.After(sc.Now) {
				due.Reasons = append(due.Reasons, "coordinator session age reached max_coordinator_age_seconds")
				due.AgeReachedAt = &at
				earlier(at)
			}
		}
		if limit := sc.Campaign.MaxCoordinatorCompactions; limit != nil && a.CompactionCount >= *limit {
			due.Reasons = append(due.Reasons, "coordinator compaction count reached max_coordinator_compactions")
			// Only the first compaction has its own time in the store.
			var compacted *time.Time
			if *limit == 1 {
				if err := tx.QueryRow(ctx, `SELECT first_compacted_at FROM cairn.agent_session WHERE repo=$1 AND agent_id=$2`,
					sc.Repo, a.AgentID).Scan(&compacted); err != nil {
					return snap, err
				}
			}
			if compacted != nil && !compacted.IsZero() && !compacted.After(sc.Now) {
				earlier(*compacted)
			} else {
				dated = false
			}
		}
		if dated {
			due.Since = first
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
	System        string           `json:"system,omitempty"`
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
	Fence      string `json:"f"`
	Epoch      string `json:"m"`
	Position   int64  `json:"p"`
}

func encodeSwarmCursor(sc swarmScope, position int64) string {
	raw, _ := json.Marshal(swarmCursor{Version: 1, Collection: sc.Repo, Campaign: sc.Campaign.Campaign, Generation: sc.Generation, Fence: sc.Fence, Epoch: sc.Epoch, Position: position})
	return base64.RawURLEncoding.EncodeToString(raw)
}

// decodeSwarmCursor returns nil for an empty cursor. A cursor for another
// collection or campaign is a caller error. A cursor from before a restore,
// or from another restore of the same backup, is stale: the page reloads.
func decodeSwarmCursor(raw string, sc swarmScope) (*swarmCursor, error) {
	if raw == "" {
		return nil, nil
	}
	invalid := failure("INVALID_REQUEST", "invalid swarm cursor")
	// A collection name may be 256 bytes and JSON may escape each of them
	// to six, so the bound is well above the largest cursor the encoder emits.
	if len(raw) > swarmCursorMax || strings.Contains(raw, "=") {
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
	// The generation is a counter stored in the database, so two restores of
	// one backup both reach the same number. The fence identity tells them
	// apart: a cursor of the discarded branch must not continue on the other.
	if c.Generation != sc.Generation || c.Fence != sc.Fence {
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
//   - a topic event was published to a campaign principal: it has a delivery
//     to one, or a queued next-turn notice for one. Both rows are written
//     with the event and none is added later.
//
// The last rule reads who owns a delivery now, and a coordinator handoff moves
// the old coordinator's pending deliveries and queued notices to its
// successor. That is the one fact here that can change after the commit; the
// membership epoch covers it (swarmScopeTx), so the window is resent when it
// does.
//
// A pool request gets its delivery at assignment, after the event commits, so
// a pool delivery is not used. The journal link to a ledger item is written
// in a later transaction and is never used either. Parameters: $1 repo,
// $2 principals, $3 campaign name, $4 campaign creation time.
// The same definition scopes campaign events and, negated over every active
// campaign, landing events. %[1]s is the principals array, %[2]s the campaign
// name and %[3]s the campaign's creation time.
const swarmEventAttribution = `e.created_at>=%[3]s AND (
	e.publisher=ANY(%[1]s)
	OR (e.destination_type='agent' AND e.destination_name=ANY(%[1]s))
	OR e.routing_info->>'campaign'=%[2]s
	OR (e.destination_type='topic' AND (
		EXISTS(SELECT 1 FROM cairn.agent_delivery a WHERE a.event_id=e.event_id AND a.consumer=ANY(%[1]s))
		OR EXISTS(SELECT 1 FROM cairn.agent_organic_notice q WHERE q.event_id=e.event_id AND q.recipient_inbox=ANY(%[1]s)))))`

var (
	swarmEventInCampaign = "e.repo=$1 AND " + fmt.Sprintf(swarmEventAttribution, "$2", "$3", "$4")
	swarmLandingEvent    = `e.repo=$1 AND NOT EXISTS (
		SELECT 1 FROM (
			SELECT c.campaign, c.created_at, ` + swarmCampaignPrincipals + ` AS principals
			FROM cairn.agent_team_campaign c WHERE c.repo=$1 AND c.active
		) camp WHERE ` + fmt.Sprintf(swarmEventAttribution, "camp.principals", "camp.campaign", "camp.created_at") + `)`
)

// swarmCampaignPrincipals is every principal that attributes an event to the
// campaign row c: its members, active or not, its root, its role addresses
// and their holders, and both sides of every coordinator handoff.
const swarmCampaignPrincipals = `ARRAY(SELECT p FROM (
	SELECT c.root_agent AS p
	UNION SELECT m.member FROM cairn.agent_team_member m WHERE m.repo=c.repo AND m.campaign=c.campaign
	UNION SELECT unnest(ARRAY[b.role_address, b.holder_principal]) FROM cairn.campaign_role_binding b WHERE b.repo=c.repo AND b.campaign=c.campaign
	UNION SELECT unnest(ARRAY[h.from_principal, h.to_principal]) FROM cairn.campaign_coordinator_handoff h WHERE h.repo=c.repo AND h.campaign=c.campaign
) principals WHERE p<>'')`

// Both the inbox preview and acknowledgement scope the same event rows.
func (sc swarmScope) inboxEventFilter() (string, []any) {
	if sc.Campaign.Campaign == "" {
		return swarmLandingEvent, []any{sc.Repo}
	}
	return swarmEventInCampaign, []any{sc.Repo, sc.Principals, sc.Campaign.Campaign, sc.Campaign.CreatedAt}
}

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
	if err = rows.Err(); err != nil {
		return out, err
	}
	return out, swarmAnnotateSystemEvents(ctx, tx, sc.Repo, out)
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
	Schema             string                 `json:"schema"`
	Collection         string                 `json:"collection"`
	ObservedAt         time.Time              `json:"observed_at"`
	RestoreGeneration  int64                  `json:"restore_generation"`
	OperatorPrincipal  string                 `json:"operator_principal"`
	Campaigns          []SwarmLandingCampaign `json:"campaigns"`
	CampaignsTruncated bool                   `json:"campaigns_truncated,omitempty"`
	Unaffiliated       []SwarmLandingSession  `json:"unaffiliated"`
	// Events are the newest messages that belong to no active campaign: a
	// direct message between two sessions that are in none appears here only.
	Events        []SwarmEvent      `json:"events"`
	NeedsYou      SwarmNeedsYou     `json:"needs_you"`
	SectionErrors map[string]string `json:"section_errors,omitempty"`
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
	out := SwarmLanding{Schema: SwarmLandingSchema, Campaigns: []SwarmLandingCampaign{}, Unaffiliated: []SwarmLandingSession{}, Events: []SwarmEvent{}, NeedsYou: emptySwarmNeedsYou(), OperatorPrincipal: s.channel.Principal}
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
	rows, err := tx.Query(ctx, `SELECT campaign FROM cairn.agent_team_campaign WHERE repo=$1 AND active ORDER BY created_at DESC, campaign LIMIT 201`, repo)
	if err != nil {
		return out, err
	}
	names, err := pgx.CollectRows(rows, pgx.RowTo[string])
	if err != nil {
		return out, err
	}
	if len(names) > 200 {
		names, out.CampaignsTruncated = names[:200], true
	}
	// Affiliation is read for every active campaign, not only the listed
	// ones, so a member of a campaign beyond the list is not shown as
	// belonging to none.
	rows, err = tx.Query(ctx, `SELECT DISTINCT m.member FROM cairn.agent_team_member m JOIN cairn.agent_team_campaign c ON c.repo=m.repo AND c.campaign=m.campaign WHERE m.repo=$1 AND c.active AND m.active`, repo)
	if err != nil {
		return out, err
	}
	members, err := pgx.CollectRows(rows, pgx.RowTo[string])
	if err != nil {
		return out, err
	}
	affiliated := map[string]bool{}
	for _, member := range members {
		affiliated[member] = true
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
	// The newest events that no active campaign claims, by the same
	// attribution rule as a campaign's log, so that an event is on a
	// campaign page or here and never on both or on neither. The landing
	// page is not a resumable log: it is read whole each time.
	rows, err = tx.Query(ctx, `SELECT `+eventColumns+` FROM cairn.agent_event e WHERE `+swarmLandingEvent+` ORDER BY e.position DESC LIMIT $2`, repo, swarmLandingEvents)
	if err != nil {
		return out, err
	}
	for rows.Next() {
		e, sErr := scanEvent(rows)
		if sErr != nil {
			rows.Close()
			return out, sErr
		}
		out.Events = append(out.Events, SwarmEvent{Position: e.Position, EventID: e.EventID, Kind: e.Kind, From: e.From, To: e.Destination, Summary: e.Summary, Action: e.Action, Blocker: e.Blocker, Supersedes: e.Supersedes, CausationID: e.CausationID, CorrelationID: e.CorrelationID, Grouped: e.ResponseGroup != nil, ExpectBy: e.ExpectBy, ExpectNextBy: e.ExpectNextBy, Ref: e.Ref, CreatedAt: e.CreatedAt})
	}
	rows.Close()
	if err = rows.Err(); err != nil {
		return out, err
	}
	if err = swarmAnnotateSystemEvents(ctx, tx, repo, out.Events); err != nil {
		return out, err
	}
	errs := map[string]string{}
	swarmSection(ctx, tx, errs, "needs_you", func(sp pgx.Tx) error {
		// Attention remains with active campaigns even for former members.
		rows, err := sp.Query(ctx, `SELECT DISTINCT p.principal FROM cairn.agent_team_campaign c
			CROSS JOIN LATERAL unnest(`+swarmCampaignPrincipals+`) p(principal)
			WHERE c.repo=$1 AND c.active`, repo)
		if err != nil {
			return err
		}
		principals, err := pgx.CollectRows(rows, pgx.RowTo[string])
		if err != nil {
			return err
		}
		campaignPrincipals := make(map[string]bool, len(principals))
		for _, principal := range principals {
			campaignPrincipals[principal] = true
		}
		needs, err := s.swarmLandingNeedsYouTx(ctx, sp, repo, out.ObservedAt, out.RestoreGeneration, campaignPrincipals)
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
