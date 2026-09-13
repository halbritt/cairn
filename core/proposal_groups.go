package core

import (
	"context"
	"strings"
	"time"

	"github.com/jackc/pgx/v5"
)

// Both docket summaries and member inspection use the same current, due
// population. A retained current pair suppresses its standalone predecessor
// even when the pair itself is deferred, dismissed or converted.
const dueProposalsSQL = `WITH due AS (
 SELECT p.*,r.scope,a.witness AS failure_witness,a.detail->>'observer' AS failure_observer,
  recovered.witness AS recovery_witness,recovered.detail->>'observer' AS recovery_observer,
  encode(sha256(convert_to(jsonb_build_array('cairn.proposal-group/1',p.repo,
   p.detail->>'task_class',p.detail->>'binding_id',p.detail->>'capability_id',
   p.detail->>'error_signature_sha256')::text,'UTF8')),'hex') AS group_key
 FROM cairn.lesson_proposal p
 JOIN cairn.retrieval_receipt r ON r.receipt_id=p.failure_receipt
 JOIN cairn.run_assessment a ON a.receipt_id=p.failure_receipt AND a.version=p.failure_version
 LEFT JOIN cairn.run_assessment recovered ON recovered.receipt_id=p.recovery_receipt AND recovered.version=p.recovery_version
 WHERE p.repo=$1 AND (p.disposition='open' OR (p.disposition='deferred' AND p.due_at<=transaction_timestamp()))
 AND p.failure_version=(SELECT max(version) FROM cairn.run_assessment WHERE receipt_id=p.failure_receipt)
 AND (p.recovery_receipt IS NULL OR p.recovery_version=(SELECT max(version) FROM cairn.run_assessment WHERE receipt_id=p.recovery_receipt))
 AND (p.recovery_receipt IS NOT NULL OR NOT EXISTS (
  SELECT 1 FROM cairn.lesson_proposal paired
  WHERE paired.failure_receipt=p.failure_receipt AND paired.failure_version=p.failure_version
  AND paired.recovery_receipt IS NOT NULL
  AND paired.recovery_version=(SELECT max(version) FROM cairn.run_assessment WHERE receipt_id=paired.recovery_receipt)
 ))) `

type ProposalGroupSummary struct {
	Key               string    `json:"group_key"`
	Repo              string    `json:"repo"`
	TaskClass         string    `json:"task_class"`
	BindingID         string    `json:"binding_id"`
	CapabilityID      string    `json:"capability_id"`
	ErrorSignature    string    `json:"error_signature_sha256"`
	ProposalCount     int       `json:"proposal_count"`
	FailureCount      int       `json:"failure_count"`
	TaskCount         int       `json:"task_count"`
	StandaloneCount   int       `json:"standalone_count"`
	InstrumentedCount int       `json:"instrumented_count"`
	TestimonyCount    int       `json:"testimony_count"`
	FirstProposedAt   time.Time `json:"first_proposed_at"`
	LastProposedAt    time.Time `json:"last_proposed_at"`
}

type dueProposalGroup struct {
	ProposalGroupSummary
	singletonID string
}

func dueProposalGroups(ctx context.Context, tx pgx.Tx, repo, key string, limit int) ([]dueProposalGroup, error) {
	rows, err := tx.Query(ctx, dueProposalsSQL+`
 SELECT group_key,repo,detail->>'task_class',detail->>'binding_id',detail->>'capability_id',
  detail->>'error_signature_sha256',count(*),count(DISTINCT failure_receipt),count(DISTINCT scope->>'task_id'),
  count(*) FILTER (WHERE recovery_receipt IS NULL),
  count(*) FILTER (WHERE failure_witness='instrumented'),
  count(*) FILTER (WHERE failure_witness='testimony'),
  min(created_at),max(created_at),min(proposal_id::text)
 FROM due WHERE ($2='' OR group_key=$2)
 GROUP BY group_key,repo,detail->>'task_class',detail->>'binding_id',detail->>'capability_id',detail->>'error_signature_sha256'
 ORDER BY min(created_at),group_key LIMIT $3`, repo, key, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	groups := []dueProposalGroup{}
	for rows.Next() {
		var g dueProposalGroup
		if err := rows.Scan(&g.Key, &g.Repo, &g.TaskClass, &g.BindingID, &g.CapabilityID, &g.ErrorSignature,
			&g.ProposalCount, &g.FailureCount, &g.TaskCount, &g.StandaloneCount, &g.InstrumentedCount, &g.TestimonyCount,
			&g.FirstProposedAt, &g.LastProposedAt, &g.singletonID); err != nil {
			return nil, err
		}
		groups = append(groups, g)
	}
	return groups, rows.Err()
}

type ProposalGroupRequest struct {
	Repo   string `json:"repo"`
	Key    string `json:"group_key"`
	Limit  int    `json:"limit"`
	Offset int    `json:"offset"`
}

type ProposalGroupMember struct {
	Proposal         Proposal `json:"proposal"`
	FailureScope     Scope    `json:"failure_scope"`
	FailureWitness   string   `json:"failure_witness"`
	FailureObserver  string   `json:"failure_observer"`
	RecoveryWitness  string   `json:"recovery_witness,omitempty"`
	RecoveryObserver string   `json:"recovery_observer,omitempty"`
}

type ProposalGroup struct {
	Summary        ProposalGroupSummary  `json:"summary"`
	Members        []ProposalGroupMember `json:"members"`
	More           bool                  `json:"more"`
	NextOffset     int                   `json:"next_offset"`
	Method         string                `json:"method"`
	Interpretation string                `json:"interpretation"`
}

func (s *Store) ProposalGroup(ctx context.Context, req ProposalGroupRequest) (ProposalGroup, error) {
	if req.Repo == "" || !digestValid(req.Key) || req.Limit < 1 || req.Limit > 200 || req.Offset < 0 {
		return ProposalGroup{}, failure("INVALID_REQUEST", "repo, group digest, limit 1-200 and nonnegative offset required")
	}
	if err := s.checkRepo(req.Repo); err != nil {
		return ProposalGroup{}, err
	}
	tx, err := s.beginLevel(ctx, pgx.RepeatableRead)
	if err != nil {
		return ProposalGroup{}, err
	}
	defer tx.Rollback(ctx)
	key := strings.ToLower(req.Key)
	groups, err := dueProposalGroups(ctx, tx, req.Repo, key, 1)
	if err != nil {
		return ProposalGroup{}, err
	}
	if len(groups) == 0 {
		return ProposalGroup{}, failure("NOT_FOUND", "no current due proposals in this group")
	}
	rows, err := tx.Query(ctx, dueProposalsSQL+`
 SELECT detail,version,disposition,due_at,COALESCE(result_record::text,''),scope,
  failure_witness,COALESCE(failure_observer,'unknown'),COALESCE(recovery_witness,''),COALESCE(recovery_observer,'')
 FROM due WHERE group_key=$2 ORDER BY created_at,proposal_id LIMIT $3 OFFSET $4`, req.Repo, key, req.Limit+1, req.Offset)
	if err != nil {
		return ProposalGroup{}, err
	}
	result := ProposalGroup{Summary: groups[0].ProposalGroupSummary, Members: []ProposalGroupMember{},
		Method:         "exact-failure-signature/1",
		Interpretation: "Current due proposals with identical repository, task class, binding, capability and declared error-signature digest. Counts describe review demand, not corroboration, independent failures or causal benefit. Review each source proposal under its own version and authority."}
	for rows.Next() {
		var member ProposalGroupMember
		p := &member.Proposal
		if err := rows.Scan(p, &p.Version, &p.Disposition, &p.DueAt, &p.ResultRecord, &member.FailureScope, &member.FailureWitness, &member.FailureObserver, &member.RecoveryWitness, &member.RecoveryObserver); err != nil {
			rows.Close()
			return ProposalGroup{}, err
		}
		p.SourceCurrent = true
		p.Kind = "failure_recovery"
		if p.RecoveryReceipt == "" {
			p.Kind = "failure"
		}
		result.Members = append(result.Members, member)
	}
	err = rows.Err()
	rows.Close()
	if err != nil {
		return ProposalGroup{}, err
	}
	result.More = len(result.Members) > req.Limit
	if result.More {
		result.Members = result.Members[:req.Limit]
	}
	result.NextOffset = req.Offset + len(result.Members)
	return result, tx.Commit(ctx)
}
