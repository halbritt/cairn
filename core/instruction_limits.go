package core

import (
	"context"
	"fmt"

	"github.com/jackc/pgx/v5"
)

type InstructionPolicy struct {
	RecordID        string `json:"record_id"`
	Version         int    `json:"version"`
	Scope           Scope  `json:"scope"`
	Lifecycle       string `json:"lifecycle"`
	Category        string `json:"category"`
	Mandatory       bool   `json:"mandatory"`
	RequiresRuntime bool   `json:"requires_runtime"`
	PolicyKey       string `json:"policy_key"`
	GrantID         string `json:"grant_id"`
}

func (s *Store) InstructionPolicy(ctx context.Context, id string) (InstructionPolicy, error) {
	if err := validID(id); err != nil {
		return InstructionPolicy{}, err
	}
	tx, err := s.beginLevel(ctx, pgx.RepeatableRead)
	if err != nil {
		return InstructionPolicy{}, err
	}
	defer tx.Rollback(ctx)
	record, err := readRecord(ctx, tx, id)
	if err != nil {
		return InstructionPolicy{}, err
	}
	if err = s.checkRepo(record.Scope.Repo); err != nil {
		return InstructionPolicy{}, err
	}
	if record.Class != "C" {
		return InstructionPolicy{}, failure("INVALID_REQUEST", "instruction policy inspection requires a C record")
	}
	result := InstructionPolicy{RecordID: id, Version: record.Version, Scope: record.Scope, Lifecycle: record.Lifecycle}
	err = tx.QueryRow(ctx, `SELECT category,mandatory,requires_runtime,policy_key,grant_id::text FROM cairn.record_authority WHERE record_id=$1 AND version=$2`, id, record.Version).Scan(&result.Category, &result.Mandatory, &result.RequiresRuntime, &result.PolicyKey, &result.GrantID)
	if err != nil {
		return result, err
	}
	return result, tx.Commit(ctx)
}

type InstructionLimit struct {
	MaxCount  int `json:"max_count"`
	MaxTokens int `json:"max_tokens"`
}

type InstructionLimits struct {
	Security   *InstructionLimit `json:"security"`
	Workflow   *InstructionLimit `json:"workflow"`
	Preference *InstructionLimit `json:"preference"`
}

func (l *InstructionLimits) validate() error {
	if l == nil {
		return nil
	}
	for _, limit := range []*InstructionLimit{l.Security, l.Workflow, l.Preference} {
		if limit == nil || limit.MaxCount < 0 || limit.MaxCount > 10000 || limit.MaxTokens < 0 || limit.MaxTokens > 1000000 {
			return failure("INVALID_REQUEST", "all three instruction categories require explicit limits: count 0-10000 and tokens 0-1000000")
		}
	}
	return nil
}

func (l *InstructionLimits) category(name string) *InstructionLimit {
	switch name {
	case "security":
		return l.Security
	case "workflow":
		return l.Workflow
	case "preference":
		return l.Preference
	}
	return nil
}

type instructionUsage struct{ count, tokens int }
type instructionBudget struct {
	limits *InstructionLimits
	used   map[string]instructionUsage
}

func newInstructionBudget(p *SemanticPackage) *instructionBudget {
	if p.Policy != "local-loop/3" {
		return nil
	}
	p.Omitted["CATEGORY_BUDGET"] = 0
	return &instructionBudget{limits: p.PolicyRevision.Rules.InstructionLimits, used: map[string]instructionUsage{}}
}

func (b *instructionBudget) admit(entry Selection, e *CandidateEvaluation, omitted map[string]int) (bool, error) {
	if b == nil || entry.Record.Class != "C" {
		return true, nil
	}
	limit := b.limits.category(entry.Category)
	if limit == nil {
		return false, failure("INTEGRITY_FAILURE", "instruction has an unsupported category")
	}
	used := b.used[entry.Category]
	// The body is immutable at this version. Index pointers reserve its full
	// UTF-8 byte cost so later expansion cannot bypass the category limit.
	used.count++
	used.tokens += len(entry.Record.Body)
	if used.count > limit.MaxCount || used.tokens > limit.MaxTokens {
		e.Reason = "CATEGORY_BUDGET"
		if entry.Mandatory {
			return false, failure("BUDGET_REFUSED", fmt.Sprintf("mandatory %s instruction limit exceeded", entry.Category))
		}
		omitted["CATEGORY_BUDGET"]++
		return false, nil
	}
	b.used[entry.Category] = used
	return true, nil
}
