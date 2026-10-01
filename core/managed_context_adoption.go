package core

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"regexp"
	"strconv"
	"time"

	"github.com/jackc/pgx/v5"
)

// MaxAdoptedContextBytes bounds the historical file an adoption reads.
const MaxAdoptedContextBytes = 8 << 20

var lowerSHA256 = regexp.MustCompile(`^[0-9a-f]{64}$`)

// AdoptContextRequest describes one explicitly selected historical context.txt
// as the instrumented filesystem owner observed it while holding the run
// directory's lock. The owner computed ObservedSHA256 from the bytes it read;
// the store compares it with the retained canonical package.
type AdoptContextRequest struct {
	RequestID       string    `json:"request_id"`
	ReceiptID       string    `json:"receipt_id"`
	Directory       string    `json:"directory"`
	DirectoryDevice string    `json:"directory_device"`
	DirectoryInode  string    `json:"directory_inode"`
	OwnershipID     string    `json:"ownership_id,omitempty"`
	ObservedBytes   int64     `json:"observed_bytes"`
	ObservedSHA256  string    `json:"observed_sha256"`
	FileDevice      string    `json:"file_device"`
	FileInode       string    `json:"file_inode"`
	FileModifiedAt  time.Time `json:"file_modified_at"`
}

// AdoptedContext is the custody row of an adopted file. The observed_* fields
// record what adoption saw; they are not a launch, delivery or completion time.
type AdoptedContext struct {
	ManagedContext
	CustodyOrigin          string    `json:"custody_origin"`
	ObservedBytes          int64     `json:"observed_bytes"`
	ObservedFileDevice     string    `json:"observed_file_device"`
	ObservedFileInode      string    `json:"observed_file_inode"`
	ObservedFileModifiedAt time.Time `json:"observed_file_modified_at"`
	AdoptedAt              time.Time `json:"adopted_at"`
	// AlreadyAdopted is true when this exact adoption was recorded earlier; the
	// call changed nothing.
	AlreadyAdopted bool `json:"already_adopted"`
}

func (req AdoptContextRequest) validate(needOwnership bool) error {
	if err := validID(req.ReceiptID); err != nil {
		return err
	}
	if needOwnership || req.OwnershipID != "" {
		if err := validID(req.OwnershipID); err != nil {
			return err
		}
	}
	if err := validateContextLocation(ManagedContext{ReceiptID: req.ReceiptID, Directory: req.Directory, DirectoryDevice: req.DirectoryDevice, DirectoryInode: req.DirectoryInode}); err != nil {
		return err
	}
	for _, id := range []string{req.FileDevice, req.FileInode} {
		if err := validateDecimal(id); err != nil {
			return failure("INVALID_REQUEST", "file identity must be observed unsigned decimal device and inode")
		}
	}
	if !lowerSHA256.MatchString(req.ObservedSHA256) || req.ObservedBytes < 0 || req.ObservedBytes > MaxAdoptedContextBytes || req.FileModifiedAt.IsZero() {
		return failure("INVALID_REQUEST", "adoption requires the observed size, SHA-256 and modification time of the file")
	}
	return nil
}

// adoptionAccess is the receipt authorization shared by a check and the
// registration: the invoking channel must own the receipt and repository, the
// receipt must not predate the restore fence, and its sealed package payload
// must not have been excluded by forgetting.
func (s *Store) adoptionAccess(ctx context.Context, tx pgx.Tx, receiptID string, lock bool) error {
	if !s.channel.Operator || !s.channel.Instrumented {
		return failure("AUTHORITY_DENIED", "context adoption requires the instrumented operator")
	}
	if err := s.receiptAccess(ctx, tx, receiptID); err != nil {
		return err
	}
	// The restore fence applies: a receipt that predates it is not admitted to new
	// custody. The effective-policy freshness that guards a launch does not: this
	// adopts a copy that already exists and authorizes no delivery.
	if err := receiptCurrentGeneration(ctx, tx, receiptID); err != nil {
		return err
	}
	if lock {
		if _, err := tx.Exec(ctx, `SELECT receipt_id FROM cairn.retrieval_receipt WHERE receipt_id=$1 FOR UPDATE`, receiptID); err != nil {
			return err
		}
	}
	return receiptPayloadAvailable(ctx, tx, receiptID)
}

const adoptedContextColumns = `c.receipt_id::text,c.directory,c.directory_device,c.directory_inode,c.body_sha256,c.ownership_id::text,c.custody_origin,
 COALESCE(c.observed_bytes,0),COALESCE(c.observed_file_device,''),COALESCE(c.observed_file_inode,''),COALESCE(c.observed_file_modified_at,'epoch'::timestamptz),c.registered_at`

func scanAdoptedContext(row pgx.Row) (AdoptedContext, error) {
	var a AdoptedContext
	err := row.Scan(&a.ReceiptID, &a.Directory, &a.DirectoryDevice, &a.DirectoryInode, &a.BodySHA256, &a.OwnershipID, &a.CustodyOrigin,
		&a.ObservedBytes, &a.ObservedFileDevice, &a.ObservedFileInode, &a.ObservedFileModifiedAt, &a.AdoptedAt)
	return a, err
}

// verifyAdoption applies the checks that do not depend on who holds a lock. It
// returns the identical earlier adoption, if any, after the bytes have been
// checked against the retained package again; any other custody for the receipt,
// or reuse of the ownership marker elsewhere, is a conflict.
func (s *Store) verifyAdoption(ctx context.Context, tx pgx.Tx, req AdoptContextRequest) (*AdoptedContext, error) {
	var claimed, completed bool
	if err := tx.QueryRow(ctx, `SELECT launch_claimed,EXISTS(SELECT 1 FROM cairn.run_outcome WHERE receipt_id=$1) FROM cairn.retrieval_receipt WHERE receipt_id=$1`, req.ReceiptID).Scan(&claimed, &completed); err != nil {
		return nil, err
	}
	if !claimed || !completed {
		return nil, failure("INVALID_REQUEST", "adoption requires a launched run with a recorded outcome")
	}
	var recovered bool
	if err := tx.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM cairn.recovery_context WHERE receipt_id=$1)`, req.ReceiptID).Scan(&recovered); err != nil {
		return nil, err
	}
	if recovered {
		return nil, failure("CUSTODY_CONFLICT", "this receipt already has retained recovery custody for its context file")
	}
	var existing *AdoptedContext
	found, err := scanAdoptedContext(tx.QueryRow(ctx, `SELECT `+adoptedContextColumns+` FROM cairn.managed_context c WHERE c.receipt_id=$1`, req.ReceiptID))
	switch {
	case err == nil:
		same := found.CustodyOrigin == "adopted" && found.Directory == req.Directory && found.DirectoryDevice == req.DirectoryDevice &&
			found.DirectoryInode == req.DirectoryInode && (req.OwnershipID == "" || found.OwnershipID == req.OwnershipID)
		if !same {
			return nil, failure("CUSTODY_CONFLICT", "this receipt already has different managed context custody; adoption will not replace it")
		}
		existing = &found
	case !errors.Is(err, pgx.ErrNoRows):
		return nil, err
	}
	if req.OwnershipID != "" {
		var reused bool
		if err = tx.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM cairn.managed_context WHERE ownership_id=$1 AND receipt_id<>$2)
 OR EXISTS(SELECT 1 FROM cairn.recovery_context WHERE ownership_id=$1 AND receipt_id<>$2)`, req.OwnershipID, req.ReceiptID).Scan(&reused); err != nil {
			return nil, err
		}
		if reused {
			return nil, failure("CUSTODY_CONFLICT", "the ownership marker already identifies another receipt's custody")
		}
	}
	pkg, err := readPackage(ctx, tx, req.ReceiptID)
	if err != nil {
		return nil, err
	}
	rendered, err := pkg.Render()
	if err != nil {
		return nil, err
	}
	sum := sha256.Sum256([]byte(rendered))
	if hex.EncodeToString(sum[:]) != req.ObservedSHA256 || int64(len(rendered)) != req.ObservedBytes {
		return nil, failure("ARTIFACT_MISMATCH", "the file's bytes are not the retained canonical package of this receipt; nothing was adopted")
	}
	return existing, nil
}

// CheckContextAdoption reads, without changing anything, whether the observed file
// could be adopted now. It returns the identical earlier adoption when there is
// one. The filesystem owner calls it before it creates the ownership marker, so
// a predictable refusal leaves the run directory untouched. It is advisory:
// AdoptManagedContext repeats every check inside its own transaction.
func (s *Store) CheckContextAdoption(ctx context.Context, req AdoptContextRequest) (*AdoptedContext, error) {
	if !s.channel.Operator || !s.channel.Instrumented {
		return nil, failure("AUTHORITY_DENIED", "context adoption requires the instrumented operator")
	}
	if err := req.validate(false); err != nil {
		return nil, err
	}
	tx, err := s.beginLevel(ctx, pgx.RepeatableRead)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback(ctx)
	if err = s.adoptionAccess(ctx, tx, req.ReceiptID, false); err != nil {
		return nil, err
	}
	existing, err := s.verifyAdoption(ctx, tx, req)
	if err != nil {
		return nil, err
	}
	return existing, tx.Commit(ctx)
}

func validateDecimal(id string) error {
	n, err := strconv.ParseUint(id, 10, 64)
	if err != nil || strconv.FormatUint(n, 10) != id {
		return failure("INVALID_REQUEST", "expected an unsigned decimal identity")
	}
	return nil
}

// AdoptManagedContext brings a verified historical context.txt into managed
// custody. The row is the same managed_context custody a new run registers, so
// forgetting inventories it and purge-deletion removes it. It creates no
// launch, delivery or outcome record and does not alter the run's history. The
// instrumented filesystem owner has already created the private ownership
// marker while holding the directory lock. An identical earlier adoption
// returns that custody unchanged.
func (s *Store) AdoptManagedContext(ctx context.Context, req AdoptContextRequest) (AdoptedContext, error) {
	if !s.channel.Operator || !s.channel.Instrumented {
		return AdoptedContext{}, failure("AUTHORITY_DENIED", "context adoption requires the instrumented operator")
	}
	if err := req.validate(true); err != nil {
		return AdoptedContext{}, err
	}
	guard := func(tx pgx.Tx) error { return s.adoptionAccess(ctx, tx, req.ReceiptID, true) }
	return privileged(ctx, s, "adopt-context", req.RequestID, req, func(tx pgx.Tx) (AdoptedContext, error) {
		existing, err := s.verifyAdoption(ctx, tx, req)
		if err != nil {
			return AdoptedContext{}, err
		}
		if existing != nil {
			existing.AlreadyAdopted = true
			return *existing, nil
		}
		row := tx.QueryRow(ctx, `INSERT INTO cairn.managed_context AS c(receipt_id,directory,directory_device,directory_inode,body_sha256,ownership_id,custody_origin,observed_bytes,observed_file_device,observed_file_inode,observed_file_modified_at)
 VALUES($1,$2,$3,$4,$5,$6,'adopted',$7,$8,$9,$10) RETURNING `+adoptedContextColumns, req.ReceiptID, req.Directory, req.DirectoryDevice, req.DirectoryInode, req.ObservedSHA256, req.OwnershipID,
			req.ObservedBytes, req.FileDevice, req.FileInode, req.FileModifiedAt.UTC())
		adopted, err := scanAdoptedContext(row)
		if err != nil {
			return AdoptedContext{}, err
		}
		// The new copy changes every deletion inventory that names a source record,
		// so earlier previews must not authorize forgetting without it.
		rows, err := tx.Query(ctx, `SELECT DISTINCT record_id::text FROM cairn.record_use WHERE receipt_id=$1 ORDER BY record_id::text`, req.ReceiptID)
		if err != nil {
			return AdoptedContext{}, err
		}
		ids, err := pgx.CollectRows(rows, pgx.RowTo[string])
		if err != nil {
			return AdoptedContext{}, err
		}
		for _, id := range ids {
			if _, err = tx.Exec(ctx, `UPDATE cairn.memory_record SET use_generation=use_generation+1 WHERE record_id=$1`, id); err != nil {
				return AdoptedContext{}, err
			}
		}
		return adopted, nil
	}, guard)
}
