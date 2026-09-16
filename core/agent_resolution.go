package core

import (
	"context"
	"errors"

	"github.com/jackc/pgx/v5"
)

// AgentResolution pins one reported context. It is not a credential and it
// authorizes no action. Publication checks it again in the same transaction.
type AgentResolution struct {
	AgentID            string `json:"agent_id"`
	ExecutionID        string `json:"execution_id"`
	Repo               string `json:"repo"`
	DatabaseGeneration int64  `json:"database_generation"`
	ContextRevision    int64  `json:"context_revision"`
}

type ResolveAgentResult struct {
	State      string           `json:"state"`
	Resolution *AgentResolution `json:"resolution,omitempty"`
	Candidates []AgentInstance  `json:"candidates"`
	More       bool             `json:"more"`
}

func (r AgentResolution) validate() error {
	if err := (AgentSessionRef{r.AgentID, r.ExecutionID}).Validate(); err != nil {
		return err
	}
	if r.Repo == "" || r.DatabaseGeneration < 0 || r.ContextRevision < 1 {
		return failure("INVALID_REQUEST", "resolution requires collection, database generation and context revision")
	}
	return nil
}

func (s *Store) ResolveAgent(ctx context.Context, req AgentDirectoryQuery, dest Destination) (ResolveAgentResult, error) {
	out := ResolveAgentResult{State: "no-match", Candidates: []AgentInstance{}}
	if req.After != 0 || req.Limit != 0 || req.IncludeOffline {
		return out, failure("INVALID_REQUEST", "resolve does not accept paging or include_offline; use directory to inspect candidates")
	}
	if req.AgentID == "" && req.Harness == "" && req.Project == "" && req.Model == "" && req.Workspace == "" && req.State == "" && req.DeliveryMode == "" {
		return out, failure("INVALID_REQUEST", "resolve requires a selector")
	}
	req.Limit = 100
	page, err := s.AgentDirectory(ctx, req, dest)
	if err != nil {
		return out, err
	}
	out.Candidates, out.More = page.Agents, page.More
	switch len(page.Agents) {
	case 0:
		// Offline candidates are diagnostic only. A concurrent registration between
		// these reads can appear here; no resolution is issued without a fresh read.
		req.IncludeOffline = true
		offline, err := s.AgentDirectory(ctx, req, dest)
		if err != nil {
			return out, err
		}
		if len(offline.Agents) > 0 {
			out.State = "stale"
			out.Candidates, out.More = offline.Agents, offline.More
		}
	case 1:
		a := page.Agents[0]
		out.State = "unique"
		out.Resolution = &AgentResolution{AgentID: a.AgentID, ExecutionID: a.ExecutionID, Repo: a.Repo, DatabaseGeneration: a.DatabaseGeneration, ContextRevision: a.ContextRevision}
	default:
		out.State = "ambiguous"
	}
	return out, nil
}

func revalidateAgentResolution(ctx context.Context, tx pgx.Tx, r AgentResolution, dest Destination) error {
	generation, err := retrievalGeneration(ctx, tx)
	if err != nil {
		return err
	}
	a, err := scanAgentSession(tx.QueryRow(ctx, `SELECT `+agentSessionColumns+` FROM cairn.agent_session WHERE agent_id=$1 AND repo=$2 AND (visibility='hosted' OR $3) FOR SHARE`, r.AgentID, r.Repo, dest.AllowLocal))
	if errors.Is(err, pgx.ErrNoRows) {
		return failure("STALE_RESOLUTION", "selected agent is no longer available; resolve again")
	}
	if err != nil {
		return err
	}
	if !a.Online || a.ExecutionID != r.ExecutionID || a.ContextRevision != r.ContextRevision || a.DatabaseGeneration != r.DatabaseGeneration || generation != r.DatabaseGeneration {
		return failure("STALE_RESOLUTION", "selected agent presence or context changed; resolve again")
	}
	return nil
}
