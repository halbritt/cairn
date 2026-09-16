package core

import (
	"context"
	"errors"
	"path/filepath"
	"strconv"
	"strings"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
)

// Metadata is reported context, not authentication or evidence of capability.
type AgentMetadata struct {
	Harness       string `json:"harness"`
	Model         string `json:"model,omitempty"`
	ObservedModel string `json:"observed_model,omitempty"`
	Project       string `json:"project"`
	Workspace     string `json:"workspace"`
	TaskSummary   string `json:"task_summary,omitempty"`
	State         string `json:"state"`
	DeliveryMode  string `json:"delivery_mode"`
}

type AgentInstance struct {
	AgentID            string        `json:"agent_id"`
	Ordinal            int64         `json:"ordinal"`
	DisplayName        string        `json:"display_name"`
	NativeSessionID    string        `json:"native_session_id"`
	MetadataSource     string        `json:"metadata_source"`
	Inbox              string        `json:"inbox"`
	Repo               string        `json:"repo"`
	ExecutionID        string        `json:"execution_id"`
	DatabaseGeneration int64         `json:"database_generation"`
	ContextRevision    int64         `json:"context_revision"`
	Metadata           AgentMetadata `json:"metadata"`
	LastSeen           time.Time     `json:"last_seen"`
	ExpiresAt          time.Time     `json:"expires_at"`
	Stopped            bool          `json:"stopped"`
	Online             bool          `json:"online"`
}

type RegisterAgentRequest struct {
	RequestID       string        `json:"request_id"`
	Repo            string        `json:"repo,omitempty"`
	Binding         string        `json:"binding"`
	NativeSessionID string        `json:"native_session_id"`
	Metadata        AgentMetadata `json:"metadata"`
}

type AgentSessionRef struct {
	AgentID     string `json:"agent_id"`
	ExecutionID string `json:"execution_id"`
}

type agentSessionChannel struct {
	Ref         AgentSessionRef
	Profile     string
	Destination Destination
}

// ForAgentSession borrows the profile's connection pool for session inbox work.
// Every transaction checks the registered association and execution generation.
// The UUID selects a session within the existing profile; it is not a credential.
// Closing this view does not close the profile's pool.
func (s *Store) ForAgentSession(ref AgentSessionRef, dest Destination) (*Store, error) {
	if err := validEventDestination(dest); err != nil {
		return nil, err
	}
	if err := ref.Validate(); err != nil {
		return nil, err
	}
	if s.session != nil || s.channel.Instrumented {
		return nil, failure("INVALID_REQUEST", "select an agent session from an ordinary base profile")
	}
	view := *s
	view.session = &agentSessionChannel{Ref: ref, Profile: s.channel.Principal, Destination: dest}
	view.channel = Channel{Principal: "agent/" + ref.AgentID, Repo: s.channel.Repo}
	return &view, nil
}

type UpdateAgentRequest struct {
	RequestID        string          `json:"request_id"`
	Session          AgentSessionRef `json:"session"`
	ExpectedRevision int64           `json:"expected_revision"`
	Metadata         AgentMetadata   `json:"metadata"`
}

type AgentDirectoryQuery struct {
	Repo           string `json:"repo,omitempty"`
	AgentID        string `json:"agent_id,omitempty"`
	Harness        string `json:"harness,omitempty"`
	Project        string `json:"project,omitempty"`
	Model          string `json:"model,omitempty"`
	Workspace      string `json:"workspace,omitempty"`
	State          string `json:"state,omitempty"`
	DeliveryMode   string `json:"delivery_mode,omitempty"`
	IncludeOffline bool   `json:"include_offline,omitempty"`
	After          int64  `json:"after,omitempty"`
	Limit          int    `json:"limit,omitempty"`
}

type AgentDirectoryPage struct {
	Agents    []AgentInstance `json:"agents"`
	NextAfter int64           `json:"next_after,omitempty"`
	More      bool            `json:"more"`
}

func (r AgentSessionRef) Validate() error {
	if validID(r.AgentID) != nil || validID(r.ExecutionID) != nil {
		return failure("INVALID_REQUEST", "agent and execution UUIDs required")
	}
	return nil
}

func (m AgentMetadata) validate() error {
	if !eventName.MatchString(m.Harness) || strings.TrimSpace(m.Project) == "" || !filepath.IsAbs(m.Workspace) || (m.State != "idle" && m.State != "busy") || (m.DeliveryMode != "existing-session" && m.DeliveryMode != "fresh-worker") {
		return failure("INVALID_REQUEST", "agent metadata needs harness, project, absolute workspace, idle/busy state and delivery mode")
	}
	for _, field := range []struct {
		value string
		limit int
	}{{m.Model, 256}, {m.ObservedModel, 256}, {m.Project, 256}, {m.Workspace, 4096}, {m.TaskSummary, 1024}} {
		if len(field.value) > field.limit || strings.ContainsRune(field.value, 0) {
			return failure("INVALID_REQUEST", "agent metadata field is oversized or contains NUL")
		}
	}
	return nil
}

const agentSessionColumns = `agent_id::text,ordinal,native_session_id,repo,execution_id::text,database_generation,context_revision,metadata,last_seen,expires_at,stopped,(NOT stopped AND expires_at>clock_timestamp() AND database_generation=(SELECT generation FROM cairn.retrieval_generation WHERE singleton))`

func scanAgentSession(row pgx.Row) (AgentInstance, error) {
	var a AgentInstance
	err := row.Scan(&a.AgentID, &a.Ordinal, &a.NativeSessionID, &a.Repo, &a.ExecutionID, &a.DatabaseGeneration, &a.ContextRevision, &a.Metadata, &a.LastSeen, &a.ExpiresAt, &a.Stopped, &a.Online)
	if err == nil {
		a.Inbox = "agent/" + a.AgentID
		a.DisplayName = "agent-" + strconv.FormatInt(a.Ordinal, 10)
		a.MetadataSource = "reported"
	}
	return a, err
}

func (s *Store) RegisterAgent(ctx context.Context, req RegisterAgentRequest, dest Destination) (AgentInstance, error) {
	if err := validEventDestination(dest); err != nil {
		return AgentInstance{}, err
	}
	var err error
	if req.Repo, err = s.eventScope(req.Repo, ""); err != nil {
		return AgentInstance{}, err
	}
	if !eventName.MatchString(req.Binding) || strings.TrimSpace(req.NativeSessionID) == "" || len(req.NativeSessionID) > 256 || strings.ContainsRune(req.NativeSessionID, 0) {
		return AgentInstance{}, failure("INVALID_REQUEST", "bounded launcher binding and native session ID required")
	}
	if err := req.Metadata.validate(); err != nil {
		return AgentInstance{}, err
	}
	guard := func(tx pgx.Tx) error {
		generation, err := retrievalGeneration(ctx, tx)
		if err != nil {
			return err
		}
		var previousGeneration int64
		err = tx.QueryRow(ctx, `SELECT (response->>'database_generation')::bigint FROM cairn.mutation_request WHERE caller=$1 AND operation='agent-register' AND request_id=$2`, s.channel.Principal, req.RequestID).Scan(&previousGeneration)
		if errors.Is(err, pgx.ErrNoRows) {
			return nil
		}
		if err == nil && previousGeneration != generation {
			return failure("STALE_SESSION", "registration predates restore; register with a new request ID")
		}
		return err
	}
	return mutate(ctx, s, "agent-register", req.RequestID, struct {
		Request     RegisterAgentRequest
		Destination Destination
	}{req, dest}, func(tx pgx.Tx) (AgentInstance, error) {
		if err := lock(ctx, tx, "agent-session:"+req.Repo+":"+s.channel.Principal+":"+req.Binding+":"+req.NativeSessionID); err != nil {
			return AgentInstance{}, err
		}
		generation, err := retrievalGeneration(ctx, tx)
		if err != nil {
			return AgentInstance{}, err
		}
		var existing string
		err = tx.QueryRow(ctx, `SELECT agent_id::text FROM cairn.agent_session WHERE repo=$1 AND owner=$2 AND binding=$3 AND native_session_id=$4 FOR UPDATE`, req.Repo, s.channel.Principal, req.Binding, req.NativeSessionID).Scan(&existing)
		if errors.Is(err, pgx.ErrNoRows) {
			return scanAgentSession(tx.QueryRow(ctx, `INSERT INTO cairn.agent_session(agent_id,repo,binding,native_session_id,visibility,execution_id,database_generation,metadata) VALUES($1,$2,$3,$4,$5,$6,$7,$8) RETURNING `+agentSessionColumns, uuid.NewString(), req.Repo, req.Binding, req.NativeSessionID, dest.Name, uuid.NewString(), generation, req.Metadata))
		}
		if err != nil {
			return AgentInstance{}, err
		}
		var held bool
		if err = tx.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM cairn.agent_wake_attempt WHERE repo=$1 AND consumer=$2 AND finished_at IS NULL)`, req.Repo, "agent/"+existing).Scan(&held); err != nil {
			return AgentInstance{}, err
		}
		if held {
			return AgentInstance{}, failure("AGENT_BUSY", "stop and reconcile the active wake attempt before resuming this agent")
		}
		return scanAgentSession(tx.QueryRow(ctx, `UPDATE cairn.agent_session SET execution_id=$2,database_generation=$3,metadata=$4,visibility=$5,context_revision=context_revision+1,last_seen=clock_timestamp(),expires_at=clock_timestamp()+interval '90 seconds',stopped=false WHERE agent_id=$1 RETURNING `+agentSessionColumns, existing, uuid.NewString(), generation, req.Metadata, dest.Name))
	}, guard)
}

func currentAgentSession(ctx context.Context, tx pgx.Tx, ref AgentSessionRef, owner, repo string, update bool, dest Destination) (AgentInstance, error) {
	generation, err := retrievalGeneration(ctx, tx)
	if err != nil {
		return AgentInstance{}, err
	}
	rowLock := " FOR SHARE"
	if update {
		rowLock = " FOR UPDATE"
	}
	a, err := scanAgentSession(tx.QueryRow(ctx, `SELECT `+agentSessionColumns+` FROM cairn.agent_session WHERE agent_id=$1 AND owner=$2 AND ($3='' OR repo=$3) AND (visibility='hosted' OR $4)`+rowLock, ref.AgentID, owner, repo, dest.AllowLocal))
	if errors.Is(err, pgx.ErrNoRows) {
		return a, failure("NOT_FOUND", "session is not registered to this profile")
	}
	if err == nil && (a.ExecutionID != ref.ExecutionID || a.DatabaseGeneration != generation) {
		return a, failure("STALE_SESSION", "execution was replaced or predates restore; resume registration")
	}
	return a, err
}

func activeAgent(a AgentInstance) error {
	if a.Stopped {
		return failure("STALE_SESSION", "session has left; resume registration")
	}
	return nil
}

func (s *Store) UpdateAgent(ctx context.Context, req UpdateAgentRequest, dest Destination) (AgentInstance, error) {
	if err := validEventDestination(dest); err != nil {
		return AgentInstance{}, err
	}
	if err := req.Session.Validate(); err != nil {
		return AgentInstance{}, err
	}
	if err := req.Metadata.validate(); err != nil {
		return AgentInstance{}, err
	}
	if req.ExpectedRevision < 1 {
		return AgentInstance{}, failure("INVALID_REQUEST", "expected context revision required")
	}
	var current AgentInstance
	guard := func(tx pgx.Tx) error {
		var err error
		current, err = currentAgentSession(ctx, tx, req.Session, s.channel.Principal, s.channel.Repo, true, dest)
		if err != nil {
			return err
		}
		return activeAgent(current)
	}
	return mutate(ctx, s, "agent-context", req.RequestID, struct {
		Request     UpdateAgentRequest
		Destination Destination
	}{req, dest}, func(tx pgx.Tx) (AgentInstance, error) {
		if current.ContextRevision != req.ExpectedRevision {
			return AgentInstance{}, failure("VERSION_CONFLICT", "agent context changed; read current presence before updating")
		}
		return scanAgentSession(tx.QueryRow(ctx, `UPDATE cairn.agent_session SET metadata=$2,context_revision=context_revision+1,last_seen=clock_timestamp(),expires_at=clock_timestamp()+interval '90 seconds' WHERE agent_id=$1 RETURNING `+agentSessionColumns, req.Session.AgentID, req.Metadata))
	}, guard)
}

func (s *Store) HeartbeatAgent(ctx context.Context, ref AgentSessionRef, dest Destination) (AgentInstance, error) {
	return s.agentPresence(ctx, ref, false, dest)
}

func (s *Store) LeaveAgent(ctx context.Context, ref AgentSessionRef, dest Destination) (AgentInstance, error) {
	return s.agentPresence(ctx, ref, true, dest)
}

func (s *Store) agentPresence(ctx context.Context, ref AgentSessionRef, leaving bool, dest Destination) (AgentInstance, error) {
	if err := validEventDestination(dest); err != nil {
		return AgentInstance{}, err
	}
	if err := ref.Validate(); err != nil {
		return AgentInstance{}, err
	}
	tx, err := s.begin(ctx)
	if err != nil {
		return AgentInstance{}, err
	}
	defer tx.Rollback(ctx)
	a, err := currentAgentSession(ctx, tx, ref, s.channel.Principal, s.channel.Repo, true, dest)
	if err != nil {
		return a, err
	}
	if !leaving {
		if err = activeAgent(a); err != nil {
			return a, err
		}
	}
	a, err = scanAgentSession(tx.QueryRow(ctx, `UPDATE cairn.agent_session SET stopped=$2,last_seen=clock_timestamp(),expires_at=clock_timestamp()+CASE WHEN $2 THEN interval '0 seconds' ELSE interval '90 seconds' END WHERE agent_id=$1 RETURNING `+agentSessionColumns, ref.AgentID, leaving))
	if err != nil {
		return a, err
	}
	return a, tx.Commit(ctx)
}

func (s *Store) AgentDirectory(ctx context.Context, req AgentDirectoryQuery, dest Destination) (AgentDirectoryPage, error) {
	out := AgentDirectoryPage{Agents: []AgentInstance{}}
	if err := validEventDestination(dest); err != nil {
		return out, err
	}
	repo, err := s.eventScope(req.Repo, "")
	if err != nil {
		return out, err
	}
	limit, err := eventLimit(req.Limit)
	if err != nil {
		return out, err
	}
	if req.After < 0 || (req.AgentID != "" && validID(req.AgentID) != nil) || (req.Harness != "" && !eventName.MatchString(req.Harness)) || (req.Workspace != "" && !filepath.IsAbs(req.Workspace)) || (req.State != "" && req.State != "idle" && req.State != "busy") || (req.DeliveryMode != "" && req.DeliveryMode != "existing-session" && req.DeliveryMode != "fresh-worker") {
		return out, failure("INVALID_REQUEST", "invalid agent directory selector")
	}
	for _, text := range []string{req.Project, req.Model, req.Workspace} {
		if len(text) > 4096 || strings.ContainsRune(text, 0) || (text != "" && strings.TrimSpace(text) == "") {
			return out, failure("INVALID_REQUEST", "invalid agent directory text")
		}
	}
	tx, err := s.begin(ctx)
	if err != nil {
		return out, err
	}
	defer tx.Rollback(ctx)
	generation, err := retrievalGeneration(ctx, tx)
	if err != nil {
		return out, err
	}
	rows, err := tx.Query(ctx, `SELECT `+agentSessionColumns+` FROM cairn.agent_session
	 WHERE repo=$1 AND (visibility='hosted' OR $2)
	 AND ($4 OR (NOT stopped AND expires_at>clock_timestamp() AND database_generation=$3))
	 AND ($5='' OR agent_id=NULLIF($5,'')::uuid)
	 AND ($6='' OR lower(metadata->>'harness')=lower($6))
	 AND ($7='' OR lower(metadata->>'project')=lower($7))
	 AND ($8='' OR COALESCE(NULLIF(metadata->>'observed_model',''),metadata->>'model')=$8)
	 AND ($9='' OR metadata->>'workspace'=$9)
	 AND ($10='' OR metadata->>'state'=$10)
	 AND ($11='' OR metadata->>'delivery_mode'=$11)
	 AND ordinal>$12 ORDER BY ordinal LIMIT $13`, repo, dest.AllowLocal, generation, req.IncludeOffline, req.AgentID, req.Harness, req.Project, req.Model, req.Workspace, req.State, req.DeliveryMode, req.After, limit+1)
	if err != nil {
		return out, err
	}
	for rows.Next() {
		a, err := scanAgentSession(rows)
		if err != nil {
			rows.Close()
			return out, err
		}
		out.Agents = append(out.Agents, a)
	}
	err = rows.Err()
	rows.Close()
	if err != nil {
		return out, err
	}
	if len(out.Agents) > limit {
		out.More = true
		out.Agents = out.Agents[:limit]
	}
	if len(out.Agents) > 0 {
		out.NextAfter = out.Agents[len(out.Agents)-1].Ordinal
	}
	return out, tx.Commit(ctx)
}
