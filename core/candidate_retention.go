package core

import (
	"context"
	"errors"
	"syscall"
	"time"

	"github.com/jackc/pgx/v5"
)

const (
	candidateRetentionSchema = "cairn.candidate-retention/1"
	// Recall-miss capture reads candidate rows after the same hook's search, so a
	// shorter window could race its own receipt: it is not supported.
	minCandidateWindowDays = 1
	// verify-restore accepts at most this many fixtures, and needs one that selected content.
	defaultKeepSelectedPerRepo = 100
)

// retainedCandidateSQL names the rows that outlive their receipt's detail. Blocked
// demand still drives the docket; gate refusals are the enforcement decisions whose
// record identity the sealed package's omission census does not carry.
const retainedCandidateSQL = `(c.escalation_blocked OR c.reason IN ('CLASS_NOT_CONSEQUENTIAL','AUTHORITY_INACTIVE','SCOPE_AUTHORITY_INACTIVE','POLICY_UNENFORCEABLE','OPEN_CONFLICT','ATTRIBUTION_UNRECONCILED','EVIDENCE_UNAVAILABLE','CONTEXT_MISSING','CURRENTNESS_MISMATCH','OUTSIDE_VALIDITY'))`

// runBoundReceiptSQL names receipts tied to an execution. Their decisions stay
// reviewable with no defined end, so their detail has no automatic lifetime.
const runBoundReceiptSQL = `(r.launch_claimed
 OR EXISTS(SELECT 1 FROM cairn.run_binding x WHERE x.receipt_id=r.receipt_id)
 OR EXISTS(SELECT 1 FROM cairn.run_outcome x WHERE x.receipt_id=r.receipt_id)
 OR EXISTS(SELECT 1 FROM cairn.run_assessment x WHERE x.receipt_id=r.receipt_id)
 OR EXISTS(SELECT 1 FROM cairn.managed_context x WHERE x.receipt_id=r.receipt_id)
 OR EXISTS(SELECT 1 FROM cairn.delivery_receipt x WHERE x.receipt_id=r.receipt_id)
 OR EXISTS(SELECT 1 FROM cairn.run_retrieval x WHERE r.receipt_id IN (x.retrieval_receipt_id,x.run_receipt_id))
 OR EXISTS(SELECT 1 FROM cairn.agent_wake_attempt x WHERE x.receipt_id=r.receipt_id))`

// newestSelectingReceiptsSQL keeps a bounded set of restore-verification fixtures:
// the newest receipts that delivered bodies, per owning channel and repository,
// because verification recompiles only receipts its own channel owns and a paused
// store cannot compile a new one. $1 is the limit for each channel and repository.
const newestSelectingReceiptsSQL = `SELECT receipt_id FROM (
 SELECT b.receipt_id,row_number() OVER (PARTITION BY b.caller,b.repo ORDER BY b.created_at DESC,b.receipt_id) AS position FROM (
  SELECT DISTINCT r.receipt_id,r.caller,r.scope->>'repo' AS repo,r.created_at FROM cairn.record_use u JOIN cairn.retrieval_receipt r USING(receipt_id)
  WHERE u.exposure_kind='body' AND r.payload_deleted_by IS NULL) b) ranked WHERE position<=$1`

// CandidateRetentionRequest expires one bounded slice of candidate detail. There
// is deliberately no default window: the duration is an operator policy.
type CandidateRetentionRequest struct {
	WindowDays          int                        `json:"window_days"`
	DryRun              bool                       `json:"dry_run,omitempty"`
	MaxReceipts         int                        `json:"max_receipts,omitempty"`
	MaxRuntimeSeconds   int                        `json:"max_runtime_seconds,omitempty"`
	PauseMilliseconds   int                        `json:"pause_ms,omitempty"`
	KeepSelectedPerRepo int                        `json:"keep_selected_per_repo,omitempty"`
	Limits              CandidateMaintenanceLimits `json:"limits,omitempty"`
}

// CandidateMaintenanceLimits stops work before it endangers the cluster. Zero
// fields use the defaults below; there is no way to disable a limit.
type CandidateMaintenanceLimits struct {
	MaxRunWALBytes    int64 `json:"max_run_wal_bytes,omitempty"`
	MaxWALDirBytes    int64 `json:"max_wal_dir_bytes,omitempty"`
	MaxArchiveBacklog int   `json:"max_archive_backlog_segments,omitempty"`
	MinFreeBytes      int64 `json:"min_free_bytes,omitempty"`
}

type CandidateRetentionResult struct {
	Schema            string    `json:"schema"`
	WindowDays        int       `json:"window_days"`
	Cutoff            time.Time `json:"cutoff"`
	DryRun            bool      `json:"dry_run"`
	EligibleReceipts  int       `json:"eligible_receipts"`
	ProtectedReceipts int       `json:"protected_receipts"`
	ExpiredReceipts   int       `json:"expired_receipts"`
	DeletedRows       int64     `json:"deleted_rows"`
	RetainedRows      int64     `json:"retained_rows"`
	SkippedLocked     int       `json:"skipped_locked"`
	RemainingReceipts int       `json:"remaining_receipts"`
	WALBytes          int64     `json:"wal_bytes"`
	StoppedBy         string    `json:"stopped_by"`
}

func (l CandidateMaintenanceLimits) withDefaults() CandidateMaintenanceLimits {
	if l.MaxRunWALBytes == 0 {
		l.MaxRunWALBytes = 16 << 30
	}
	if l.MaxWALDirBytes == 0 {
		l.MaxWALDirBytes = 32 << 30
	}
	if l.MaxArchiveBacklog == 0 {
		l.MaxArchiveBacklog = 256
	}
	if l.MinFreeBytes == 0 {
		l.MinFreeBytes = 40 << 30
	}
	return l
}

func (l CandidateMaintenanceLimits) validate() error {
	if l.MaxRunWALBytes < 0 || l.MaxWALDirBytes < 0 || l.MaxArchiveBacklog < 0 || l.MinFreeBytes < 0 {
		return failure("INVALID_REQUEST", "maintenance limits cannot be negative")
	}
	return nil
}

// candidateMaintenanceGuard observes cluster pressure on behalf of one run. It
// fails closed: a role that cannot read the WAL directory or the data directory's
// filesystem cannot prove the limits hold, so maintenance refuses to continue.
type candidateMaintenanceGuard struct {
	store    *Store
	limits   CandidateMaintenanceLimits
	startLSN int64
	lastLSN  int64
	dataDir  string
	// reserve is extra headroom the next step needs on top of MinFreeBytes.
	reserve int64
}

func (s *Store) newCandidateMaintenanceGuard(ctx context.Context, limits CandidateMaintenanceLimits) (*candidateMaintenanceGuard, error) {
	g := &candidateMaintenanceGuard{store: s, limits: limits.withDefaults()}
	err := s.pool.QueryRow(ctx, `SELECT pg_wal_lsn_diff(pg_current_wal_lsn(),'0/0')::bigint,current_setting('data_directory')`).Scan(&g.startLSN, &g.dataDir)
	g.lastLSN = g.startLSN
	return g, err
}

// pressure returns "" while work may continue, otherwise the limit that stopped it.
func (g *candidateMaintenanceGuard) pressure(ctx context.Context) (string, error) {
	var walDir int64
	var backlog int
	err := g.store.pool.QueryRow(ctx, `SELECT pg_wal_lsn_diff(pg_current_wal_lsn(),'0/0')::bigint,
 (SELECT COALESCE(sum(size),0)::bigint FROM pg_ls_waldir()),
 (SELECT count(*) FROM pg_ls_archive_statusdir() WHERE name LIKE '%.ready')`).Scan(&g.lastLSN, &walDir, &backlog)
	if err != nil {
		if ctx.Err() != nil {
			return "", ctx.Err()
		}
		return "", failure("DEPENDENCY_CONFLICT", "candidate maintenance cannot observe WAL pressure; its role needs pg_monitor")
	}
	var fs syscall.Statfs_t
	if err = syscall.Statfs(g.dataDir, &fs); err != nil {
		return "", failure("DEPENDENCY_CONFLICT", "candidate maintenance must run on the database host: cannot observe "+g.dataDir)
	}
	switch {
	case g.lastLSN-g.startLSN > g.limits.MaxRunWALBytes:
		return "max_run_wal_bytes", nil
	case walDir > g.limits.MaxWALDirBytes:
		return "max_wal_dir_bytes", nil
	case backlog > g.limits.MaxArchiveBacklog:
		return "max_archive_backlog_segments", nil
	case int64(fs.Bavail)*int64(fs.Bsize) < g.limits.MinFreeBytes+g.reserve:
		return "min_free_bytes", nil
	}
	return "", nil
}

func (g *candidateMaintenanceGuard) walBytes() int64 { return g.lastLSN - g.startLSN }

// holdCandidateMaintenance admits one maintenance runner at a time for the whole
// store. A session advisory lock releases itself if the runner dies.
func (s *Store) holdCandidateMaintenance(ctx context.Context) (func(), error) {
	conn, err := s.pool.Acquire(ctx)
	if err != nil {
		return nil, err
	}
	var held bool
	if err = conn.QueryRow(ctx, `SELECT pg_try_advisory_lock(728190042)`).Scan(&held); err != nil {
		conn.Release()
		return nil, err
	}
	if !held {
		conn.Release()
		return nil, failure("MAINTENANCE_ACTIVE", "another candidate maintenance run is active")
	}
	return func() {
		_, _ = conn.Exec(context.Background(), `SELECT pg_advisory_unlock(728190042)`)
		conn.Release()
	}, nil
}

func candidateDetailAvailable(ctx context.Context, tx pgx.Tx, receiptID string) error {
	var expired *time.Time
	if err := tx.QueryRow(ctx, `SELECT candidate_detail_expired_at FROM cairn.retrieval_receipt WHERE receipt_id=$1`, receiptID).Scan(&expired); err != nil {
		return err
	}
	if expired != nil {
		return failure("CANDIDATE_DETAIL_EXPIRED", "candidate detail for this receipt expired at "+expired.UTC().Format(time.RFC3339)+" under the retention policy; its seal, saved-byte replay and use history remain available")
	}
	return nil
}

// ExpireCandidateDetail removes evaluated-set rows for receipts older than the
// window, one receipt per transaction. An interrupted run leaves every receipt
// either untouched or completely expired, so repeating the request resumes it.
func (s *Store) ExpireCandidateDetail(ctx context.Context, req CandidateRetentionRequest) (CandidateRetentionResult, error) {
	result := CandidateRetentionResult{Schema: candidateRetentionSchema, WindowDays: req.WindowDays, DryRun: req.DryRun, StoppedBy: "complete"}
	if err := s.checkpointAccess(); err != nil {
		return result, err
	}
	if req.WindowDays < minCandidateWindowDays || req.WindowDays > 36500 {
		return result, failure("INVALID_REQUEST", "window_days must be between 1 and 36500; it has no default")
	}
	if req.MaxReceipts < 0 || req.MaxRuntimeSeconds < 0 || req.PauseMilliseconds < 0 || req.KeepSelectedPerRepo < 0 {
		return result, failure("INVALID_REQUEST", "retention bounds cannot be negative")
	}
	if err := req.Limits.validate(); err != nil {
		return result, err
	}
	if req.MaxReceipts == 0 {
		req.MaxReceipts = 20000
	}
	if req.MaxRuntimeSeconds == 0 {
		req.MaxRuntimeSeconds = 3000
	}
	if req.KeepSelectedPerRepo == 0 {
		req.KeepSelectedPerRepo = defaultKeepSelectedPerRepo
	}
	deadline := time.Now().Add(time.Duration(req.MaxRuntimeSeconds) * time.Second)
	if !req.DryRun {
		release, err := s.holdCandidateMaintenance(ctx)
		if err != nil {
			return result, err
		}
		defer release()
		state, err := s.candidateReclaimState(ctx)
		if err != nil {
			return result, err
		}
		// Rows must stay as the rewrite recorded them until it finishes: verification
		// compares the live relation with the old one.
		if state != candidateReclaimNone {
			return result, failure("MAINTENANCE_ACTIVE", "a candidate storage rewrite is in progress; finish or abort it before expiring detail")
		}
	}
	if err := s.pool.QueryRow(ctx, `SELECT now()-make_interval(days => $1)`, req.WindowDays).Scan(&result.Cutoff); err != nil {
		return result, err
	}
	result.Cutoff = result.Cutoff.UTC()
	eligible, protected, err := s.candidateRetentionCensus(ctx, result.Cutoff, req.KeepSelectedPerRepo)
	if err != nil {
		return result, err
	}
	result.EligibleReceipts, result.ProtectedReceipts, result.RemainingReceipts = eligible, protected, eligible
	if req.DryRun {
		result.StoppedBy = "dry_run"
		return result, nil
	}
	guard, err := s.newCandidateMaintenanceGuard(ctx, req.Limits)
	if err != nil {
		return result, err
	}
	seen := map[string]bool{}
	for {
		ids, err := s.candidateRetentionBatch(ctx, result.Cutoff, req.KeepSelectedPerRepo, seen)
		if err != nil {
			return result, err
		}
		if len(ids) == 0 {
			break
		}
		for _, id := range ids {
			switch {
			case ctx.Err() != nil:
				return result, ctx.Err()
			case result.ExpiredReceipts >= req.MaxReceipts:
				result.StoppedBy = "max_receipts"
				return result, nil
			case time.Now().After(deadline):
				result.StoppedBy = "max_runtime_seconds"
				return result, nil
			}
			limit, err := guard.pressure(ctx)
			result.WALBytes = guard.walBytes()
			if err != nil {
				return result, err
			}
			if limit != "" {
				result.StoppedBy = limit
				return result, nil
			}
			outcome, err := s.expireReceiptDetail(ctx, id, result.Cutoff, true)
			if err != nil {
				return result, err
			}
			if outcome.locked {
				result.SkippedLocked++
			}
			if !outcome.expired {
				seen[id] = true
				continue
			}
			result.ExpiredReceipts++
			result.RemainingReceipts--
			result.DeletedRows += outcome.deleted
			result.RetainedRows += outcome.retained
			if req.PauseMilliseconds > 0 {
				select {
				case <-ctx.Done():
					return result, ctx.Err()
				case <-time.After(time.Duration(req.PauseMilliseconds) * time.Millisecond):
				}
			}
		}
	}
	return result, nil
}

func (s *Store) candidateRetentionCensus(ctx context.Context, cutoff time.Time, keep int) (eligible, protected int, err error) {
	err = s.pool.QueryRow(ctx, `WITH fixtures AS (`+newestSelectingReceiptsSQL+`)
 SELECT count(*) FILTER (WHERE NOT protected),count(*) FILTER (WHERE protected) FROM (
  SELECT `+runBoundReceiptSQL+` OR r.receipt_id IN (SELECT receipt_id FROM fixtures) AS protected
  FROM cairn.retrieval_receipt r
  WHERE r.candidate_detail_expired_at IS NULL AND r.explanation_version>0 AND r.created_at<$2) e`, keep, cutoff).Scan(&eligible, &protected)
	return eligible, protected, err
}

// The seen set holds receipts this run could not expire, so a pass terminates even
// when one is repeatedly locked or became protected after it was selected.
func (s *Store) candidateRetentionBatch(ctx context.Context, cutoff time.Time, keep int, seen map[string]bool) ([]string, error) {
	excluded := make([]string, 0, len(seen))
	for id := range seen {
		excluded = append(excluded, id)
	}
	rows, err := s.pool.Query(ctx, `WITH fixtures AS (`+newestSelectingReceiptsSQL+`)
 SELECT r.receipt_id::text FROM cairn.retrieval_receipt r
 WHERE r.candidate_detail_expired_at IS NULL AND r.explanation_version>0 AND r.created_at<$2
 AND NOT `+runBoundReceiptSQL+` AND r.receipt_id NOT IN (SELECT receipt_id FROM fixtures)
 AND NOT (r.receipt_id=ANY($3::uuid[]))
 ORDER BY r.created_at,r.receipt_id LIMIT 500`, keep, cutoff, excluded)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	ids := []string{}
	for rows.Next() {
		var id string
		if err = rows.Scan(&id); err != nil {
			return nil, err
		}
		ids = append(ids, id)
	}
	return ids, rows.Err()
}

type receiptExpiry struct {
	locked, expired   bool
	deleted, retained int64
}

// expireReceiptDetail serializes with launches and new references: FOR UPDATE
// conflicts with their foreign-key locks, and protection is checked in a later
// statement whose fresh READ COMMITTED snapshot sees anything that committed first.
//
// A rewrite marks without deleting: it replaces the rows by not copying them.
func (s *Store) expireReceiptDetail(ctx context.Context, id string, cutoff time.Time, removeRows bool) (receiptExpiry, error) {
	var out receiptExpiry
	tx, err := s.begin(ctx)
	if err != nil {
		return out, err
	}
	defer tx.Rollback(context.Background())
	if _, err = tx.Exec(ctx, `SELECT set_config('lock_timeout','2s',true),set_config('statement_timeout','60s',true)`); err != nil {
		return out, err
	}
	var held bool
	err = tx.QueryRow(ctx, `SELECT true FROM cairn.retrieval_receipt WHERE receipt_id=$1 AND candidate_detail_expired_at IS NULL FOR UPDATE SKIP LOCKED`, id).Scan(&held)
	if errors.Is(err, pgx.ErrNoRows) {
		var exists bool
		if err = tx.QueryRow(ctx, `SELECT candidate_detail_expired_at IS NULL FROM cairn.retrieval_receipt WHERE receipt_id=$1`, id).Scan(&exists); err != nil {
			return out, err
		}
		out.locked = exists
		return out, nil
	}
	if err != nil {
		return out, err
	}
	var eligible bool
	if err = tx.QueryRow(ctx, `SELECT r.created_at<$2 AND r.explanation_version>0 AND NOT `+runBoundReceiptSQL+` FROM cairn.retrieval_receipt r WHERE r.receipt_id=$1`, id, cutoff).Scan(&eligible); err != nil {
		return out, err
	}
	if !eligible {
		return out, nil
	}
	if _, err = tx.Exec(ctx, `UPDATE cairn.retrieval_receipt SET candidate_detail_expired_at=clock_timestamp() WHERE receipt_id=$1`, id); err != nil {
		return out, err
	}
	if removeRows {
		tag, err := tx.Exec(ctx, `DELETE FROM cairn.retrieval_candidate c WHERE c.receipt_id=$1 AND NOT `+retainedCandidateSQL, id)
		if err != nil {
			return out, err
		}
		out.deleted = tag.RowsAffected()
		if err = tx.QueryRow(ctx, `SELECT count(*) FROM cairn.retrieval_candidate WHERE receipt_id=$1`, id).Scan(&out.retained); err != nil {
			return out, err
		}
	}
	out.expired = true
	return out, tx.Commit(ctx)
}
