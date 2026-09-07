package core

import (
	"context"

	"github.com/jackc/pgx/v5"
)

type DocketItem struct {
	AttemptID string `json:"attempt_id,omitempty"`
	Reason    string `json:"reason"`
	RecordID  string `json:"record_id,omitempty"`
	Version   int    `json:"version,omitempty"`
	ReceiptID string `json:"receipt_id,omitempty"`
	Action    string `json:"suggested_action"`
}
type Docket struct {
	Items     []DocketItem `json:"items"`
	Truncated bool         `json:"truncated"`
}

func (s *Store) Docket(ctx context.Context, repo string) (Docket, error) {
	if err := s.checkRepo(repo); err != nil {
		return Docket{}, err
	}
	if repo == "" {
		return Docket{}, failure("INVALID_REQUEST", "repository required")
	}
	tx, err := s.beginLevel(ctx, pgx.RepeatableRead)
	if err != nil {
		return Docket{}, err
	}
	defer tx.Rollback(ctx)
	docket := Docket{Items: []DocketItem{}}
	openRows, err := tx.Query(ctx, `SELECT a.attempt_id::text FROM cairn.delegation_attempt a
 JOIN (SELECT DISTINCT ON(observer,repo,task_id) observer,repo,task_id,state FROM cairn.task_state WHERE repo=$1 ORDER BY observer,repo,task_id,version DESC) t ON t.observer=a.observed_by AND t.repo=a.repo AND t.task_id=a.task_id
 WHERE a.terminal_state IS NULL AND t.state IN ('completed','cancelled') ORDER BY a.spawned_at,a.attempt_id LIMIT 101`, repo)
	if err != nil {
		return docket, err
	}
	for openRows.Next() {
		item := DocketItem{Reason: "OPEN_DELEGATE_AFTER_TASK", Action: "Reconcile the service-observed delegate still open after task completion."}
		if err = openRows.Scan(&item.AttemptID); err != nil {
			openRows.Close()
			return docket, err
		}
		docket.Items = append(docket.Items, item)
	}
	err = openRows.Err()
	openRows.Close()
	if err != nil {
		return docket, err
	}
	demandRows, err := tx.Query(ctx, `SELECT DISTINCT ON (c.record_id,c.version) c.record_id::text,c.version,c.receipt_id::text
 FROM cairn.retrieval_candidate c JOIN cairn.retrieval_receipt r USING(receipt_id)
 JOIN cairn.memory_record m ON m.record_id=c.record_id AND m.current_version=c.version
 WHERE r.scope->>'repo'=$1 AND c.escalation_blocked AND m.class='A' AND m.lifecycle='active'
 ORDER BY c.record_id,c.version,r.created_at DESC,c.receipt_id LIMIT 101`, repo)
	if err != nil {
		return docket, err
	}
	for demandRows.Next() {
		item := DocketItem{Reason: "ESCALATION_BLOCKED", Action: "Review supporting evidence for independent promotion; repetition does not confer authority."}
		if err = demandRows.Scan(&item.RecordID, &item.Version, &item.ReceiptID); err != nil {
			demandRows.Close()
			return docket, err
		}
		docket.Items = append(docket.Items, item)
	}
	err = demandRows.Err()
	demandRows.Close()
	if err != nil {
		return docket, err
	}
	rows, err := tx.Query(ctx, `SELECT d.record_id::text,d.version FROM cairn.correction_docket d
 JOIN cairn.memory_record m ON m.record_id=d.record_id AND m.current_version=d.version
 JOIN cairn.record_version v ON v.record_id=d.record_id AND v.version=d.version WHERE v.repo=$1 ORDER BY d.created_at,d.record_id LIMIT 101`, repo)
	if err != nil {
		return docket, err
	}
	for rows.Next() {
		item := DocketItem{Reason: "ATTRIBUTION_CONTRADICTED", Action: "Correct the attribution; inspect the recovered service failure."}
		if err = rows.Scan(&item.RecordID, &item.Version); err != nil {
			rows.Close()
			return docket, err
		}
		docket.Items = append(docket.Items, item)
	}
	err = rows.Err()
	rows.Close()
	if err != nil {
		return docket, err
	}
	rows, err = tx.Query(ctx, `SELECT r.receipt_id::text FROM cairn.retrieval_receipt r LEFT JOIN cairn.run_outcome o USING(receipt_id) WHERE r.scope->>'repo'=$1 AND r.launch_claimed AND o.outcome_id IS NULL ORDER BY r.created_at LIMIT 101`, repo)
	if err != nil {
		return docket, err
	}
	for rows.Next() {
		item := DocketItem{Reason: "UNFINISHED_RUN", Action: "May still be running. Inspect process or pending outcome before any retry."}
		if err = rows.Scan(&item.ReceiptID); err != nil {
			rows.Close()
			return docket, err
		}
		docket.Items = append(docket.Items, item)
	}
	err = rows.Err()
	rows.Close()
	if err != nil {
		return docket, err
	}
	rows, err = tx.Query(ctx, `SELECT m.record_id::text,m.current_version FROM cairn.memory_record m JOIN cairn.record_version v ON v.record_id=m.record_id AND v.version=m.current_version WHERE v.repo=$1 AND m.class='B' AND m.lifecycle='active' ORDER BY m.record_id LIMIT 201`, repo)
	if err != nil {
		return docket, err
	}
	type reference struct {
		ID      string
		Version int
	}
	refs := []reference{}
	for rows.Next() {
		var ref reference
		if err = rows.Scan(&ref.ID, &ref.Version); err != nil {
			rows.Close()
			return docket, err
		}
		refs = append(refs, ref)
	}
	err = rows.Err()
	rows.Close()
	if err != nil {
		return docket, err
	}
	if len(refs) > 200 {
		docket.Truncated = true
		refs = refs[:200]
	}
	for _, ref := range refs {
		evidence, err := supportingEvidence(ctx, tx, ref.ID, ref.Version)
		if err != nil {
			return docket, err
		}
		good := false
		for _, e := range evidence {
			if e.State == "resolvable" {
				good = true
			}
		}
		if !good {
			docket.Items = append(docket.Items, DocketItem{Reason: "EVIDENCE_UNAVAILABLE", RecordID: ref.ID, Version: ref.Version, Action: "Re-establish supporting evidence or retract the claim."})
		}
	}
	if len(docket.Items) > 100 {
		docket.Truncated = true
		docket.Items = docket.Items[:100]
	}
	return docket, tx.Commit(ctx)
}
