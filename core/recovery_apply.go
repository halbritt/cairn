package core

import (
	"context"
	"sort"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
)

type RecoveryReapplyRequest struct {
	RequestID string         `json:"request_id"`
	Record    RecoveryRecord `json:"record"`
	Reason    string         `json:"reason"`
}

type RecoveryAction struct {
	RecoveryWithdrawal
	SourceDigest   string `json:"source_digest"`
	Outcome        string `json:"outcome"`
	CurrentEventID string `json:"current_event_id,omitempty"`
	DeletionID     string `json:"deletion_id,omitempty"`
}

type RecoveryApplication struct {
	ApplicationID string           `json:"application_id"`
	EventID       string           `json:"event_id"`
	SourceSHA256  string           `json:"source_sha256"`
	Actions       []RecoveryAction `json:"actions"`
	Coverage      string           `json:"coverage"`
}

// ReapplyRecovery records new restrictions under the current root's authority.
// External audit expectations are retained as expectations, never reconstructed
// as local events. The caller must keep the restore isolated through admission.
func (s *Store) ReapplyRecovery(ctx context.Context, req RecoveryReapplyRequest) (RecoveryApplication, error) {
	ctx = s.recoveryContext(ctx)
	if err := s.checkpointAccess(); err != nil {
		return RecoveryApplication{}, err
	}
	if err := req.Record.validate(); err != nil {
		return RecoveryApplication{}, err
	}
	if err := reasonValid(req.Reason); err != nil {
		return RecoveryApplication{}, err
	}
	guard := func(tx pgx.Tx) error {
		var root string
		if err := tx.QueryRow(ctx, `SELECT grant_id::text FROM cairn.authority_grant WHERE parent_id IS NULL`).Scan(&root); err != nil {
			return err
		}
		if root != req.Record.RootGrantID {
			return failure("INTEGRITY_FAILURE", "recovery source root differs from this store")
		}
		_, err := s.authorize(ctx, tx, root, "redact", "*")
		return err
	}
	return privileged(ctx, s, "recovery-reapply", req.RequestID, req, func(tx pgx.Tx) (RecoveryApplication, error) {
		result := RecoveryApplication{ApplicationID: uuid.NewString(), SourceSHA256: req.Record.SHA256, Actions: []RecoveryAction{}, Coverage: "New authorized restrictions and retained external expectations. Original missing history, source freshness, absent subjects and restore admission are not repaired or certified. Purge effects require the deletion worker."}
		// One recovery operation per root; ordinary mutations retain their existing
		// record/grant locks and Serializable conflict handling.
		if err := lock(ctx, tx, "recovery:"+req.Record.RootGrantID); err != nil {
			return result, err
		}
		current, err := captureRecoveryTx(ctx, tx)
		if err != nil {
			return result, err
		}
		// Refuse contradictory expectations before mutating anything. This also
		// bounds the combined export that the application must continue retaining.
		if err = mergeRecovery(&current, req.Record); err != nil {
			return result, err
		}
		digest := map[string]string{}
		for _, member := range req.Record.Audit {
			digest[member.EventID] = member.Digest
		}
		withdrawals := append([]RecoveryWithdrawal(nil), req.Record.Withdrawals...)
		// Retraction precedes forgetting when both target the same record. Revoking
		// descendants cannot revoke the root that authorizes this entire operation.
		sort.Slice(withdrawals, func(i, j int) bool {
			order := map[string]int{"revoke_grant": 0, "retract": 1, "forget": 2}
			if order[withdrawals[i].Kind] != order[withdrawals[j].Kind] {
				return order[withdrawals[i].Kind] < order[withdrawals[j].Kind]
			}
			if withdrawals[i].SubjectID != withdrawals[j].SubjectID {
				return withdrawals[i].SubjectID < withdrawals[j].SubjectID
			}
			return withdrawals[i].EventID < withdrawals[j].EventID
		})
		for _, w := range withdrawals {
			action, err := s.reapplyWithdrawal(ctx, tx, req.Record.RootGrantID, req.Reason, w)
			if err != nil {
				return result, err
			}
			action.SourceDigest = digest[w.EventID]
			result.Actions = append(result.Actions, action)
		}
		chain, err := s.authorize(ctx, tx, req.Record.RootGrantID, "redact", "*")
		if err != nil {
			return result, err
		}
		result.EventID, err = audit(ctx, tx, "reapply_recovery", result.ApplicationID, 0, 1, chain, req.Reason)
		if err != nil {
			return result, err
		}
		if _, err = tx.Exec(ctx, `INSERT INTO cairn.recovery_application(application_id,event_id,source_root,source_sha256,source_record,actions) VALUES($1,$2,$3,$4,$5,$6)`, result.ApplicationID, result.EventID, req.Record.RootGrantID, req.Record.SHA256, req.Record, result.Actions); err != nil {
			return result, err
		}
		for _, c := range req.Record.Contexts {
			if err = retainRecoveryContext(ctx, tx, result.ApplicationID, c); err != nil {
				return result, err
			}
		}
		// New audit events also consume the bounded export budget. Overflow rolls
		// back the restrictions rather than leave an unexportable recovery result.
		if _, err = captureRecoveryTx(ctx, tx); err != nil {
			return result, err
		}
		return result, nil
	}, guard)
}

func (s *Store) reapplyWithdrawal(ctx context.Context, tx pgx.Tx, root, reason string, w RecoveryWithdrawal) (RecoveryAction, error) {
	action := RecoveryAction{RecoveryWithdrawal: w, Outcome: "already_restricted"}
	if w.Kind == "revoke_grant" {
		var repo string
		var version int
		var revoked bool
		err := tx.QueryRow(ctx, `SELECT repo,version,revoked,event_id::text FROM cairn.authority_grant WHERE grant_id=$1`, w.SubjectID).Scan(&repo, &version, &revoked, &action.CurrentEventID)
		if err == pgx.ErrNoRows {
			action.Outcome = "absent"
			return action, nil
		}
		if err != nil {
			return action, err
		}
		if repo != w.Repo {
			return action, failure("INTEGRITY_FAILURE", "withdrawn grant scope differs from the source")
		}
		if w.SubjectID == root {
			return action, failure("AUTHORITY_DENIED", "root revocation requires a separate recovery procedure")
		}
		if !revoked {
			if _, err = s.revokeGrant(ctx, tx, RevokeGrantRequest{GrantID: w.SubjectID, AuthorityID: root, ExpectedVersion: version, Reason: reason}); err != nil {
				return action, err
			}
			if err = tx.QueryRow(ctx, `SELECT event_id::text FROM cairn.authority_grant WHERE grant_id=$1`, w.SubjectID).Scan(&action.CurrentEventID); err != nil {
				return action, err
			}
			action.Outcome = "reapplied"
		}
		return action, nil
	}
	var exists bool
	if err := tx.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM cairn.memory_record WHERE record_id=$1)`, w.SubjectID).Scan(&exists); err != nil {
		return action, err
	}
	if !exists {
		action.Outcome = "absent"
		return action, nil
	}
	current, err := readRecord(ctx, tx, w.SubjectID)
	if err != nil {
		return action, err
	}
	if current.Scope.Repo != w.Repo {
		return action, failure("INTEGRITY_FAILURE", "withdrawn record scope differs from the source")
	}
	needsAction := current.Lifecycle == "active" || (w.Kind == "forget" && current.Lifecycle != "tombstoned")
	if needsAction {
		preview, err := s.previewRetractionTx(ctx, tx, w.SubjectID, w.Kind == "forget")
		if err != nil {
			return action, err
		}
		if w.Kind == "forget" {
			deletion, err := s.forgetRecord(ctx, tx, ForgetRequest{RecordID: w.SubjectID, ExpectedVersion: current.Version, GrantID: root, PreviewID: preview.PreviewID}, current)
			if err != nil {
				return action, err
			}
			action.DeletionID, action.CurrentEventID = deletion.DeletionID, deletion.EventID
		} else {
			next, err := s.retractRecord(ctx, tx, RetractRequest{RecordID: w.SubjectID, ExpectedVersion: current.Version, GrantID: root, PreviewID: preview.PreviewID, Reason: reason}, current)
			if err != nil {
				return action, err
			}
			if err = tx.QueryRow(ctx, `SELECT event_id::text FROM cairn.record_authority WHERE record_id=$1 AND version=$2`, next.RecordID, next.Version).Scan(&action.CurrentEventID); err != nil {
				return action, err
			}
		}
		action.Outcome = "reapplied"
	}
	if w.Kind == "forget" {
		if err = tx.QueryRow(ctx, `SELECT deletion_id::text,event_id::text FROM cairn.deletion_request WHERE record_id=$1`, w.SubjectID).Scan(&action.DeletionID, &action.CurrentEventID); err != nil {
			return action, err
		}
		local := w
		local.EventID = action.CurrentEventID
		gap, err := inspectWithdrawal(ctx, tx, local)
		if err != nil {
			return action, err
		}
		if gap != "" {
			return action, failure("INTEGRITY_FAILURE", "existing deletion is incomplete: "+gap)
		}
	}
	return action, nil
}

// mergeRecovery retains exactly the union of known expectations. Contradictory
// claims about one identity cannot be resolved by whichever export arrived last.
func mergeRecovery(target *RecoveryRecord, source RecoveryRecord) error {
	if target.RootGrantID != source.RootGrantID {
		return failure("INTEGRITY_FAILURE", "recovery expectations have different roots")
	}
	audit := map[string]string{}
	for _, m := range target.Audit {
		audit[m.EventID] = m.Digest
	}
	for _, m := range source.Audit {
		if old, ok := audit[m.EventID]; ok {
			if old != m.Digest {
				return failure("INTEGRITY_FAILURE", "conflicting recovery audit expectations")
			}
		} else {
			target.Audit = append(target.Audit, m)
			audit[m.EventID] = m.Digest
		}
	}
	withdrawals := map[string]RecoveryWithdrawal{}
	for _, w := range target.Withdrawals {
		withdrawals[w.EventID] = w
	}
	for _, w := range source.Withdrawals {
		if old, ok := withdrawals[w.EventID]; ok {
			if old != w {
				return failure("INTEGRITY_FAILURE", "conflicting recovery withdrawal expectations")
			}
		} else {
			target.Withdrawals = append(target.Withdrawals, w)
			withdrawals[w.EventID] = w
		}
	}
	contexts := map[string]RecoveryContext{}
	for _, c := range target.Contexts {
		contexts[c.RecordID+":"+c.ReceiptID] = c
	}
	for _, c := range source.Contexts {
		key := c.RecordID + ":" + c.ReceiptID
		if old, ok := contexts[key]; ok {
			if old != c {
				return failure("INTEGRITY_FAILURE", "conflicting recovery custody expectations")
			}
		} else {
			target.Contexts = append(target.Contexts, c)
			contexts[key] = c
		}
	}
	if len(target.Audit) > 10000 || len(target.Withdrawals) > 10000 || len(target.Contexts) > 10000 {
		return failure("BUDGET_REFUSED", "combined recovery expectations exceed export bounds")
	}
	sort.Slice(target.Audit, func(i, j int) bool { return target.Audit[i].EventID < target.Audit[j].EventID })
	sort.Slice(target.Withdrawals, func(i, j int) bool { return target.Withdrawals[i].EventID < target.Withdrawals[j].EventID })
	sort.Slice(target.Contexts, func(i, j int) bool {
		a, b := target.Contexts[i], target.Contexts[j]
		if a.RecordID != b.RecordID {
			return a.RecordID < b.RecordID
		}
		return a.ReceiptID < b.ReceiptID
	})
	return nil
}

func retainRecoveryContext(ctx context.Context, tx pgx.Tx, applicationID string, c RecoveryContext) error {
	if err := validateContextLocation(c.ManagedContext); err != nil {
		return err
	}
	var deletion string
	err := tx.QueryRow(ctx, `SELECT deletion_id::text FROM cairn.deletion_request WHERE record_id=$1`, c.RecordID).Scan(&deletion)
	if err == pgx.ErrNoRows {
		return failure("INTEGRITY_FAILURE", "external context custody requires a retained forgotten record")
	}
	if err != nil {
		return err
	}
	// One receipt must not acquire conflicting location/ownership descriptors,
	// even across independent forgotten records or later recovery applications.
	var changed bool
	err = tx.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM (
 SELECT receipt_id,directory,directory_device,directory_inode,ownership_id,body_sha256 FROM cairn.managed_context
 UNION ALL SELECT receipt_id,directory,directory_device,directory_inode,ownership_id,body_sha256 FROM cairn.recovery_context) c
 WHERE receipt_id=$1 AND (directory<>$2 OR directory_device<>$3 OR directory_inode<>$4 OR ownership_id<>$5 OR body_sha256<>$6))`, c.ReceiptID, c.Directory, c.DirectoryDevice, c.DirectoryInode, c.OwnershipID, c.BodySHA256).Scan(&changed)
	if err != nil {
		return err
	}
	if changed {
		return failure("INTEGRITY_FAILURE", "external context descriptor conflicts with retained custody")
	}
	if _, err = tx.Exec(ctx, `INSERT INTO cairn.recovery_context(deletion_id,receipt_id,application_id,directory,directory_device,directory_inode,ownership_id,body_sha256) VALUES($1,$2,$3,$4,$5,$6,$7,$8) ON CONFLICT(deletion_id,receipt_id) DO NOTHING`, deletion, c.ReceiptID, applicationID, c.Directory, c.DirectoryDevice, c.DirectoryInode, c.OwnershipID, c.BodySHA256); err != nil {
		return err
	}
	_, err = tx.Exec(ctx, `INSERT INTO cairn.deletion_effect(deletion_id,target_type,target_id,status) VALUES($1,'managed_context',$2,'pending') ON CONFLICT DO NOTHING`, deletion, c.ReceiptID)
	return err
}
