package core

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"path/filepath"
	"strconv"
	"strings"

	"github.com/jackc/pgx/v5"
)

type ManagedContextRequest struct {
	OwnershipID     string `json:"ownership_id"`
	RequestID       string `json:"request_id"`
	ReceiptID       string `json:"receipt_id"`
	Directory       string `json:"directory"`
	DirectoryDevice string `json:"directory_device"`
	DirectoryInode  string `json:"directory_inode"`
}
type ManagedContext struct {
	OwnershipID     string `json:"ownership_id"`
	ReceiptID       string `json:"receipt_id"`
	Directory       string `json:"directory"`
	DirectoryDevice string `json:"directory_device"`
	DirectoryInode  string `json:"directory_inode"`
	BodySHA256      string `json:"body_sha256"`
}

// Registration is durable intent to write the reserved context.txt slot. The
// instrumented filesystem owner holds its directory lock across this call and
// the write; a purge must hold the same lock before observing/removing the slot.
func (s *Store) RegisterManagedContext(ctx context.Context, req ManagedContextRequest) (ManagedContext, error) {
	if !s.channel.Instrumented {
		return ManagedContext{}, failure("AUTHORITY_DENIED", "context registration requires the instrumented file owner")
	}
	if err := validID(req.OwnershipID); err != nil {
		return ManagedContext{}, err
	}
	if err := validID(req.ReceiptID); err != nil {
		return ManagedContext{}, err
	}
	if err := validateContextLocation(ManagedContext{ReceiptID: req.ReceiptID, Directory: req.Directory, DirectoryDevice: req.DirectoryDevice, DirectoryInode: req.DirectoryInode}); err != nil {
		return ManagedContext{}, err
	}
	guard := func(tx pgx.Tx) error {
		if err := s.receiptAccess(ctx, tx, req.ReceiptID); err != nil {
			return err
		}
		if err := receiptDeliveryCurrent(ctx, tx, req.ReceiptID); err != nil {
			return err
		}
		if _, err := tx.Exec(ctx, `SELECT receipt_id FROM cairn.retrieval_receipt WHERE receipt_id=$1 FOR UPDATE`, req.ReceiptID); err != nil {
			return err
		}
		return receiptPayloadAvailable(ctx, tx, req.ReceiptID)
	}
	return privileged(ctx, s, "register-context", req.RequestID, req, func(tx pgx.Tx) (ManagedContext, error) {
		var claimed bool
		if err := tx.QueryRow(ctx, `SELECT launch_claimed FROM cairn.retrieval_receipt WHERE receipt_id=$1`, req.ReceiptID).Scan(&claimed); err != nil {
			return ManagedContext{}, err
		}
		if !claimed {
			return ManagedContext{}, failure("AUTHORITY_DENIED", "context registration requires a claimed run")
		}
		pkg, err := readPackage(ctx, tx, req.ReceiptID)
		if err != nil {
			return ManagedContext{}, err
		}
		rendered, err := pkg.Render()
		if err != nil {
			return ManagedContext{}, err
		}
		sum := sha256.Sum256([]byte(rendered))
		result := ManagedContext{req.OwnershipID, req.ReceiptID, req.Directory, req.DirectoryDevice, req.DirectoryInode, hex.EncodeToString(sum[:])}
		if _, err = tx.Exec(ctx, `INSERT INTO cairn.managed_context(receipt_id,directory,directory_device,directory_inode,body_sha256,ownership_id) VALUES($1,$2,$3,$4,$5,$6)`, result.ReceiptID, result.Directory, result.DirectoryDevice, result.DirectoryInode, result.BodySHA256, result.OwnershipID); err != nil {
			return ManagedContext{}, err
		}
		// A new copy invalidates all existing impact previews for its source records.
		rows, err := tx.Query(ctx, `SELECT DISTINCT record_id::text FROM cairn.record_use WHERE receipt_id=$1 ORDER BY record_id::text`, req.ReceiptID)
		if err != nil {
			return ManagedContext{}, err
		}
		ids, err := pgx.CollectRows(rows, pgx.RowTo[string])
		if err != nil {
			return ManagedContext{}, err
		}
		for _, id := range ids {
			if _, err = tx.Exec(ctx, `UPDATE cairn.memory_record SET use_generation=use_generation+1 WHERE record_id=$1`, id); err != nil {
				return ManagedContext{}, err
			}
		}
		return result, nil
	}, guard)
}

type ContextPurgeTarget struct {
	ManagedContext
	Status string `json:"status"`
}

func (s *Store) ContextPurgeTarget(ctx context.Context, deletionID, receiptID string) (ContextPurgeTarget, error) {
	ctx = s.recoveryContext(ctx)
	if !s.channel.Operator || !s.channel.Instrumented {
		return ContextPurgeTarget{}, failure("AUTHORITY_DENIED", "context purge requires an instrumented operator")
	}
	if err := validID(deletionID); err != nil {
		return ContextPurgeTarget{}, err
	}
	if err := validID(receiptID); err != nil {
		return ContextPurgeTarget{}, err
	}
	tx, err := s.beginLevel(ctx, pgx.RepeatableRead)
	if err != nil {
		return ContextPurgeTarget{}, err
	}
	defer tx.Rollback(ctx)
	if err = s.deletionAccess(ctx, tx, deletionID); err != nil {
		return ContextPurgeTarget{}, err
	}
	var result ContextPurgeTarget
	err = tx.QueryRow(ctx, `SELECT c.receipt_id::text,c.directory,c.directory_device,c.directory_inode,c.body_sha256,c.ownership_id::text,e.status FROM (
 SELECT receipt_id,directory,directory_device,directory_inode,body_sha256,ownership_id FROM cairn.managed_context WHERE receipt_id=$2
 UNION
 SELECT receipt_id,directory,directory_device,directory_inode,body_sha256,ownership_id FROM cairn.recovery_context WHERE deletion_id=$1 AND receipt_id=$2
 ) c JOIN cairn.deletion_effect e ON e.target_id=c.receipt_id::text AND e.target_type='managed_context' WHERE e.deletion_id=$1 AND c.receipt_id=$2`, deletionID, receiptID).Scan(&result.ReceiptID, &result.Directory, &result.DirectoryDevice, &result.DirectoryInode, &result.BodySHA256, &result.OwnershipID, &result.Status)
	if errors.Is(err, pgx.ErrNoRows) {
		return result, failure("NOT_FOUND", "no authorized managed context effect")
	}
	if err != nil {
		return result, err
	}
	return result, tx.Commit(ctx)
}

type ContextPurgeResult struct {
	RequestID  string `json:"request_id"`
	DeletionID string `json:"deletion_id"`
	ReceiptID  string `json:"receipt_id"`
	ErrorCode  string `json:"error_code,omitempty"`
}

// The instrumented local worker reports only observations made while holding
// the registered directory lock. This is deliberately absent from agent APIs.
func (s *Store) RecordContextPurge(ctx context.Context, req ContextPurgeResult) (Deletion, error) {
	ctx = s.recoveryContext(ctx)
	if !s.channel.Operator || !s.channel.Instrumented {
		return Deletion{}, failure("AUTHORITY_DENIED", "file purge observation requires an instrumented operator")
	}
	if err := validID(req.DeletionID); err != nil {
		return Deletion{}, err
	}
	if err := validID(req.ReceiptID); err != nil {
		return Deletion{}, err
	}
	if len(req.ErrorCode) > 64 || strings.IndexFunc(req.ErrorCode, func(r rune) bool { return !(r >= 'A' && r <= 'Z') && r != '_' }) >= 0 {
		return Deletion{}, failure("INVALID_REQUEST", "purge observation accepts a bounded error code, not error text")
	}
	return privileged(ctx, s, "context-purge", req.RequestID, req, func(tx pgx.Tx) (Deletion, error) {
		if err := s.deletionAccess(ctx, tx, req.DeletionID); err != nil {
			return Deletion{}, err
		}
		var status string
		err := tx.QueryRow(ctx, `SELECT status FROM cairn.deletion_effect WHERE deletion_id=$1 AND target_type='managed_context' AND target_id=$2 FOR UPDATE`, req.DeletionID, req.ReceiptID).Scan(&status)
		if errors.Is(err, pgx.ErrNoRows) {
			return Deletion{}, failure("NOT_FOUND", "no authorized managed context effect")
		}
		if err != nil {
			return Deletion{}, err
		}
		if status != "completed" {
			status = "completed"
			if req.ErrorCode != "" {
				status = "failed"
			}
			if _, err = tx.Exec(ctx, `UPDATE cairn.deletion_effect SET status=$3,attempts=attempts+1,last_error=$4,completed_at=CASE WHEN $3='completed' THEN clock_timestamp() ELSE NULL END WHERE deletion_id=$1 AND target_type='managed_context' AND target_id=$2`, req.DeletionID, req.ReceiptID, status, req.ErrorCode); err != nil {
				return Deletion{}, err
			}
		}
		return readDeletion(ctx, tx, req.DeletionID)
	}, func(tx pgx.Tx) error { return s.deletionAccess(ctx, tx, req.DeletionID) })
}

func validateContextLocation(c ManagedContext) error {
	if !filepath.IsAbs(c.Directory) || filepath.Clean(c.Directory) != c.Directory || filepath.Base(c.Directory) != c.ReceiptID || len(c.Directory) > 4096 {
		return failure("INVALID_REQUEST", "context directory must be an absolute canonical path ending in its receipt UUID")
	}
	for _, id := range []string{c.DirectoryDevice, c.DirectoryInode} {
		n, err := strconv.ParseUint(id, 10, 64)
		if err != nil || strconv.FormatUint(n, 10) != id {
			return failure("INVALID_REQUEST", "directory identity must be observed unsigned decimal device and inode")
		}
	}
	return nil
}
