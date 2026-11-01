package core

import (
	"context"
	"errors"
	"fmt"
	"strconv"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"
)

// Deleted rows do not return their space to the filesystem, and deleting most of
// this table would log each heap page twice. A rewrite copies only what retention
// keeps into a new relation, swaps the names under a short drain, and returns
// everything else when the old relation is dropped. Each phase can be repeated.
const (
	candidateReclaimResultSchema = "cairn.candidate-reclaim/1"
	candidateReclaimLock         = "cairn_candidate_reclaim"
	candidateReclaimOldTable     = candidateReclaimLock + ".retrieval_candidate_old"
	candidateReclaimNewTable     = candidateReclaimLock + ".retrieval_candidate_new"

	candidateReclaimNone     = "none"
	candidateReclaimPlanned  = "planned"
	candidateReclaimBuilt    = "built"
	candidateReclaimSwapped  = "swapped"
	candidateReclaimVerified = "verified"

	candidateReclaimChunkPages = 65536
	candidateReclaimMaxTail    = 64
)

// CandidateReclaimRequest runs one phase: status, plan, copy, build, swap, verify,
// finish or abort. Only plan chooses the window; later phases use the recorded one.
type CandidateReclaimRequest struct {
	Phase               string                     `json:"phase"`
	WindowDays          int                        `json:"window_days,omitempty"`
	KeepSelectedPerRepo int                        `json:"keep_selected_per_repo,omitempty"`
	MaxRuntimeSeconds   int                        `json:"max_runtime_seconds,omitempty"`
	PauseMilliseconds   int                        `json:"pause_ms,omitempty"`
	LockTimeoutMS       int                        `json:"lock_timeout_ms,omitempty"`
	SampleReceipts      int                        `json:"sample_receipts,omitempty"`
	ConfirmDropOld      bool                       `json:"confirm_drop_old,omitempty"`
	Limits              CandidateMaintenanceLimits `json:"limits,omitempty"`
}

type CandidateReclaimResult struct {
	Schema            string     `json:"schema"`
	Phase             string     `json:"phase"`
	State             string     `json:"state"`
	StoppedBy         string     `json:"stopped_by"`
	WindowDays        int        `json:"window_days,omitempty"`
	Cutoff            *time.Time `json:"cutoff,omitempty"`
	ExpiringReceipts  int64      `json:"expiring_receipts"`
	RetainedReceipts  int64      `json:"retained_receipts"`
	CopiedReceipts    int64      `json:"copied_receipts"`
	CopiedRows        int64      `json:"copied_rows"`
	ResidualRows      int64      `json:"residual_rows"`
	ScanBlock         int64      `json:"scan_block"`
	ScanBlocks        int64      `json:"scan_blocks"`
	WALBytes          int64      `json:"wal_bytes"`
	SwapHoldMS        int64      `json:"swap_hold_ms,omitempty"`
	MarkedExpired     int64      `json:"marked_expired,omitempty"`
	TailReceipts      int64      `json:"tail_receipts,omitempty"`
	VerifiedReceipts  int        `json:"verified_receipts,omitempty"`
	LiveTableBytes    int64      `json:"live_table_bytes"`
	WorkingTableBytes int64      `json:"working_table_bytes"`
	DatabaseBytes     int64      `json:"database_bytes"`
}

func (s *Store) candidateReclaimState(ctx context.Context) (string, error) {
	var present bool
	if err := s.pool.QueryRow(ctx, `SELECT to_regclass('`+candidateReclaimLock+`.run') IS NOT NULL`).Scan(&present); err != nil {
		return "", err
	}
	if !present {
		return candidateReclaimNone, nil
	}
	var state string
	err := s.pool.QueryRow(ctx, `SELECT state FROM `+candidateReclaimLock+`.run`).Scan(&state)
	return state, err
}

// ReclaimCandidateStorage executes one phase of an operator-driven rewrite.
func (s *Store) ReclaimCandidateStorage(ctx context.Context, req CandidateReclaimRequest) (CandidateReclaimResult, error) {
	result := CandidateReclaimResult{Schema: candidateReclaimResultSchema, Phase: req.Phase, StoppedBy: "complete"}
	if err := s.checkpointAccess(); err != nil {
		return result, err
	}
	if err := req.Limits.validate(); err != nil {
		return result, err
	}
	if req.MaxRuntimeSeconds < 0 || req.PauseMilliseconds < 0 || req.LockTimeoutMS < 0 || req.SampleReceipts < 0 || req.KeepSelectedPerRepo < 0 {
		return result, failure("INVALID_REQUEST", "reclaim bounds cannot be negative")
	}
	if req.MaxRuntimeSeconds == 0 {
		req.MaxRuntimeSeconds = 3000
	}
	if req.LockTimeoutMS == 0 {
		req.LockTimeoutMS = 3000
	}
	if req.Phase != "status" {
		release, err := s.holdCandidateMaintenance(ctx)
		if err != nil {
			return result, err
		}
		defer release()
	}
	state, err := s.candidateReclaimState(ctx)
	if err != nil {
		return result, err
	}
	result.State = state
	allowed := map[string][]string{
		"status": {candidateReclaimNone, candidateReclaimPlanned, candidateReclaimBuilt, candidateReclaimSwapped, candidateReclaimVerified},
		"plan":   {candidateReclaimNone},
		"copy":   {candidateReclaimPlanned, candidateReclaimBuilt},
		"build":  {candidateReclaimPlanned},
		"swap":   {candidateReclaimBuilt},
		"verify": {candidateReclaimSwapped},
		"finish": {candidateReclaimVerified},
		"abort":  {candidateReclaimPlanned, candidateReclaimBuilt},
	}[req.Phase]
	if allowed == nil {
		return result, failure("INVALID_REQUEST", "phase must be status, plan, copy, build, swap, verify, finish or abort")
	}
	permitted := false
	for _, candidate := range allowed {
		permitted = permitted || candidate == state
	}
	if !permitted {
		return result, failure("DEPENDENCY_CONFLICT", "phase "+req.Phase+" cannot run while the rewrite state is "+state)
	}
	deadline := time.Now().Add(time.Duration(req.MaxRuntimeSeconds) * time.Second)
	var guard *candidateMaintenanceGuard
	if req.Phase != "status" {
		if guard, err = s.newCandidateMaintenanceGuard(ctx, req.Limits); err != nil {
			return result, err
		}
	}
	switch req.Phase {
	case "plan":
		err = s.reclaimPlan(ctx, req)
	case "copy":
		result.StoppedBy, err = s.reclaimCopy(ctx, req, guard, deadline)
	case "build":
		err = s.reclaimBuild(ctx, req, &result)
	case "swap":
		err = s.reclaimSwap(ctx, req, guard, deadline, &result)
	case "verify":
		err = s.reclaimVerify(ctx, req, &result)
	case "finish":
		err = s.reclaimFinish(ctx, req)
	case "abort":
		_, err = s.pool.Exec(ctx, `DROP SCHEMA `+candidateReclaimLock+` CASCADE`)
	}
	if guard != nil {
		_, _ = guard.pressure(ctx)
		result.WALBytes = guard.walBytes()
	}
	if err != nil {
		return result, err
	}
	return result, s.reclaimReport(ctx, &result)
}

func (s *Store) reclaimReport(ctx context.Context, result *CandidateReclaimResult) error {
	state, err := s.candidateReclaimState(ctx)
	if err != nil {
		return err
	}
	result.State = state
	if err = s.pool.QueryRow(ctx, `SELECT pg_total_relation_size('cairn.retrieval_candidate'),pg_database_size(current_database())`).Scan(&result.LiveTableBytes, &result.DatabaseBytes); err != nil {
		return err
	}
	if state == candidateReclaimNone {
		return nil
	}
	var cutoff time.Time
	var working *string
	err = s.pool.QueryRow(ctx, `SELECT r.window_days,r.cutoff,r.scan_block,r.scan_blocks,r.residual_rows,
 (SELECT count(*) FROM `+candidateReclaimLock+`.expiring),
 (SELECT count(*) FROM `+candidateReclaimLock+`.copied),
 (SELECT COALESCE(sum(rows),0)::bigint FROM `+candidateReclaimLock+`.copied),
 (SELECT count(*) FROM cairn.retrieval_receipt x WHERE x.candidate_detail_expired_at IS NULL AND NOT EXISTS(SELECT 1 FROM `+candidateReclaimLock+`.expiring e WHERE e.receipt_id=x.receipt_id)),
 COALESCE(to_regclass('`+candidateReclaimNewTable+`'),to_regclass('`+candidateReclaimOldTable+`'))::text
 FROM `+candidateReclaimLock+`.run r`).Scan(&result.WindowDays, &cutoff, &result.ScanBlock, &result.ScanBlocks, &result.ResidualRows,
		&result.ExpiringReceipts, &result.CopiedReceipts, &result.CopiedRows, &result.RetainedReceipts, &working)
	if err != nil {
		return err
	}
	cutoff = cutoff.UTC()
	result.Cutoff = &cutoff
	if working != nil {
		return s.pool.QueryRow(ctx, `SELECT pg_total_relation_size($1::regclass)`, *working).Scan(&result.WorkingTableBytes)
	}
	return nil
}

// The rewrite cannot carry triggers, grants, rules, views or row policies across a swap.
// The migration-defined table has none, so anything else needs operator review.
func reclaimPreflight(ctx context.Context, tx pgx.Tx) error {
	var unexpected bool
	err := tx.QueryRow(ctx, `SELECT c.relacl IS NOT NULL OR c.relhasrules OR c.relrowsecurity OR c.relhassubclass OR c.relkind<>'r'
 OR EXISTS(SELECT 1 FROM pg_trigger t WHERE t.tgrelid=c.oid AND NOT t.tgisinternal)
 OR EXISTS(SELECT 1 FROM pg_publication_rel p WHERE p.prrelid=c.oid)
 OR EXISTS(SELECT 1 FROM pg_depend d JOIN pg_rewrite w ON w.oid=d.objid WHERE d.classid='pg_rewrite'::regclass AND d.refobjid=c.oid)
 FROM pg_class c WHERE c.oid='cairn.retrieval_candidate'::regclass`).Scan(&unexpected)
	if err != nil {
		return err
	}
	if unexpected {
		return failure("DEPENDENCY_CONFLICT", "retrieval_candidate has grants, triggers, rules, views, policies or dependents that a rewrite would not preserve")
	}
	return nil
}

func (s *Store) reclaimPlan(ctx context.Context, req CandidateReclaimRequest) error {
	if req.WindowDays < minCandidateWindowDays || req.WindowDays > 36500 {
		return failure("INVALID_REQUEST", "plan requires window_days between 1 and 36500; it has no default")
	}
	if req.KeepSelectedPerRepo == 0 {
		req.KeepSelectedPerRepo = defaultKeepSelectedPerRepo
	}
	tx, err := s.begin(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback(context.Background())
	if err = reclaimPreflight(ctx, tx); err != nil {
		return err
	}
	if _, err = tx.Exec(ctx, `CREATE SCHEMA `+candidateReclaimLock+`;
 CREATE TABLE `+candidateReclaimLock+`.run(
  singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton),
  window_days integer NOT NULL, keep_selected integer NOT NULL, cutoff timestamptz NOT NULL,
  state text NOT NULL, scan_block bigint NOT NULL DEFAULT 0, scan_blocks bigint NOT NULL,
  residual_rows bigint NOT NULL DEFAULT 0, planned_at timestamptz NOT NULL DEFAULT clock_timestamp(), swapped_at timestamptz);
 CREATE TABLE `+candidateReclaimLock+`.expiring(receipt_id uuid PRIMARY KEY);
 CREATE TABLE `+candidateReclaimLock+`.copied(receipt_id uuid PRIMARY KEY, rows bigint NOT NULL);
 CREATE TABLE `+candidateReclaimNewTable+` (LIKE cairn.retrieval_candidate INCLUDING ALL EXCLUDING INDEXES)`); err != nil {
		return err
	}
	if _, err = tx.Exec(ctx, `INSERT INTO `+candidateReclaimLock+`.run(window_days,keep_selected,cutoff,state,scan_blocks)
 SELECT $1,$2,now()-make_interval(days => $1),$3,pg_relation_size('cairn.retrieval_candidate')/current_setting('block_size')::bigint`,
		req.WindowDays, req.KeepSelectedPerRepo, candidateReclaimPlanned); err != nil {
		return err
	}
	if _, err = tx.Exec(ctx, `WITH fixtures AS (`+newestSelectingReceiptsSQL+`)
 INSERT INTO `+candidateReclaimLock+`.expiring
 SELECT r.receipt_id FROM cairn.retrieval_receipt r, `+candidateReclaimLock+`.run p
 WHERE r.candidate_detail_expired_at IS NULL AND r.explanation_version>0 AND r.created_at<p.cutoff
 AND NOT `+runBoundReceiptSQL+` AND r.receipt_id NOT IN (SELECT receipt_id FROM fixtures)`, req.KeepSelectedPerRepo); err != nil {
		return err
	}
	return tx.Commit(ctx)
}

func reclaimLimitIsTransient(limit string) bool {
	return limit == "max_wal_dir_bytes" || limit == "max_archive_backlog_segments"
}

// reclaimPace waits out a draining WAL backlog; run budgets and disk headroom stop the phase.
func reclaimPace(ctx context.Context, guard *candidateMaintenanceGuard, deadline time.Time) (string, error) {
	for {
		limit, err := guard.pressure(ctx)
		if err != nil || limit == "" {
			return "", err
		}
		if !reclaimLimitIsTransient(limit) || time.Now().After(deadline) {
			return limit, nil
		}
		select {
		case <-ctx.Done():
			return "", ctx.Err()
		case <-time.After(5 * time.Second):
		}
	}
}

func reclaimSleep(ctx context.Context, pauseMS int) error {
	if pauseMS == 0 {
		return ctx.Err()
	}
	select {
	case <-ctx.Done():
		return ctx.Err()
	case <-time.After(time.Duration(pauseMS) * time.Millisecond):
		return nil
	}
}

// reclaimCopy first scans for rows that outlive expiring receipts, then copies
// retained receipts one transaction at a time. Each transaction records its own
// progress, so stopping at any point loses nothing and repeats nothing.
func (s *Store) reclaimCopy(ctx context.Context, req CandidateReclaimRequest, guard *candidateMaintenanceGuard, deadline time.Time) (string, error) {
	for {
		if time.Now().After(deadline) {
			return "max_runtime_seconds", nil
		}
		limit, err := reclaimPace(ctx, guard, deadline)
		if err != nil {
			return "", err
		}
		if limit != "" {
			return limit, nil
		}
		done, err := s.reclaimResidualChunk(ctx)
		if err != nil {
			return "", err
		}
		if done {
			break
		}
		if err = reclaimSleep(ctx, req.PauseMilliseconds); err != nil {
			return "", err
		}
	}
	for {
		ids, err := s.reclaimPendingReceipts(ctx, 200)
		if err != nil {
			return "", err
		}
		if len(ids) == 0 {
			return "complete", nil
		}
		for _, id := range ids {
			if time.Now().After(deadline) {
				return "max_runtime_seconds", nil
			}
			limit, err := reclaimPace(ctx, guard, deadline)
			if err != nil {
				return "", err
			}
			if limit != "" {
				return limit, nil
			}
			if err = s.reclaimCopyReceipt(ctx, id); err != nil {
				return "", err
			}
			if err = reclaimSleep(ctx, req.PauseMilliseconds); err != nil {
				return "", err
			}
		}
	}
}

func (s *Store) reclaimResidualChunk(ctx context.Context) (bool, error) {
	tx, err := s.begin(ctx)
	if err != nil {
		return false, err
	}
	defer tx.Rollback(context.Background())
	var block, blocks int64
	if err = tx.QueryRow(ctx, `SELECT scan_block,scan_blocks FROM `+candidateReclaimLock+`.run FOR UPDATE`).Scan(&block, &blocks); err != nil {
		return false, err
	}
	if block >= blocks {
		return true, nil
	}
	end := block + candidateReclaimChunkPages
	tag, err := tx.Exec(ctx, `INSERT INTO `+candidateReclaimNewTable+` SELECT c.* FROM cairn.retrieval_candidate c
 WHERE c.ctid>=$1::tid AND c.ctid<$2::tid AND `+retainedCandidateSQL+`
 AND (EXISTS(SELECT 1 FROM `+candidateReclaimLock+`.expiring e WHERE e.receipt_id=c.receipt_id)
  OR EXISTS(SELECT 1 FROM cairn.retrieval_receipt r WHERE r.receipt_id=c.receipt_id AND r.candidate_detail_expired_at IS NOT NULL))
 ON CONFLICT DO NOTHING`, "("+strconv.FormatInt(block, 10)+",0)", "("+strconv.FormatInt(end, 10)+",0)")
	if err != nil {
		return false, err
	}
	if _, err = tx.Exec(ctx, `UPDATE `+candidateReclaimLock+`.run SET scan_block=$1,residual_rows=residual_rows+$2`, min(end, blocks), tag.RowsAffected()); err != nil {
		return false, err
	}
	return false, tx.Commit(ctx)
}

func (s *Store) reclaimPendingReceipts(ctx context.Context, limit int) ([]string, error) {
	rows, err := s.pool.Query(ctx, `SELECT r.receipt_id::text FROM cairn.retrieval_receipt r
 WHERE r.candidate_detail_expired_at IS NULL
 AND NOT EXISTS(SELECT 1 FROM `+candidateReclaimLock+`.expiring e WHERE e.receipt_id=r.receipt_id)
 AND NOT EXISTS(SELECT 1 FROM `+candidateReclaimLock+`.copied d WHERE d.receipt_id=r.receipt_id)
 ORDER BY r.created_at,r.receipt_id LIMIT $1`, limit)
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

func (s *Store) reclaimCopyReceipt(ctx context.Context, id string) error {
	tx, err := s.begin(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback(context.Background())
	if _, err = tx.Exec(ctx, `SELECT set_config('statement_timeout','120s',true)`); err != nil {
		return err
	}
	if _, err = tx.Exec(ctx, reclaimCopyReceiptSQL, id); err != nil {
		return err
	}
	return tx.Commit(ctx)
}

const reclaimCopyReceiptSQL = `WITH inserted AS (
 INSERT INTO ` + candidateReclaimNewTable + ` SELECT c.* FROM cairn.retrieval_candidate c WHERE c.receipt_id=$1 ON CONFLICT DO NOTHING RETURNING 1)
 INSERT INTO ` + candidateReclaimLock + `.copied(receipt_id,rows) SELECT $1,count(*) FROM inserted`

func (s *Store) reclaimRetryDDL(ctx context.Context, lockTimeoutMS int, statement string) error {
	for attempt := 0; ; attempt++ {
		tx, err := s.begin(ctx)
		if err != nil {
			return err
		}
		_, err = tx.Exec(ctx, `SELECT set_config('lock_timeout',$1,true)`, strconv.Itoa(lockTimeoutMS)+"ms")
		if err == nil {
			_, err = tx.Exec(ctx, statement)
		}
		if err == nil {
			return tx.Commit(ctx)
		}
		_ = tx.Rollback(context.Background())
		var pgErr *pgconn.PgError
		if !errors.As(err, &pgErr) || pgErr.Code != "55P03" || attempt >= 20 {
			return err
		}
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-time.After(time.Duration(attempt+1) * time.Second):
		}
	}
}

// reclaimBuild creates constraints after the bulk copy: maintaining a random-keyed
// index row by row would be slower and log far more. Nothing reads this table yet.
func (s *Store) reclaimBuild(ctx context.Context, req CandidateReclaimRequest, result *CandidateReclaimResult) error {
	var block, blocks int64
	if err := s.pool.QueryRow(ctx, `SELECT scan_block,scan_blocks FROM `+candidateReclaimLock+`.run`).Scan(&block, &blocks); err != nil {
		return err
	}
	pending, err := s.reclaimPendingReceipts(ctx, candidateReclaimMaxTail*16+1)
	if err != nil {
		return err
	}
	if block < blocks || len(pending) > candidateReclaimMaxTail*16 {
		return failure("DEPENDENCY_CONFLICT", "complete the copy phase before building constraints")
	}
	statements := []string{
		`CREATE UNIQUE INDEX IF NOT EXISTS retrieval_candidate_new_pkey ON ` + candidateReclaimNewTable + `(receipt_id,record_id,version)`,
		`CREATE INDEX IF NOT EXISTS blocked_demand_new ON ` + candidateReclaimNewTable + `(record_id,version) WHERE escalation_blocked`,
		`DO $$ BEGIN IF NOT EXISTS(SELECT 1 FROM pg_constraint WHERE conrelid='` + candidateReclaimNewTable + `'::regclass AND contype='p') THEN
 ALTER TABLE ` + candidateReclaimNewTable + ` ADD CONSTRAINT retrieval_candidate_new_pkey PRIMARY KEY USING INDEX retrieval_candidate_new_pkey; END IF; END $$`,
		`DO $$ BEGIN IF NOT EXISTS(SELECT 1 FROM pg_constraint WHERE conrelid='` + candidateReclaimNewTable + `'::regclass AND conname='retrieval_candidate_receipt_id_fkey') THEN
 ALTER TABLE ` + candidateReclaimNewTable + ` ADD CONSTRAINT retrieval_candidate_receipt_id_fkey FOREIGN KEY(receipt_id) REFERENCES cairn.retrieval_receipt(receipt_id) NOT VALID; END IF; END $$`,
		`DO $$ BEGIN IF NOT EXISTS(SELECT 1 FROM pg_constraint WHERE conrelid='` + candidateReclaimNewTable + `'::regclass AND conname='retrieval_candidate_record_id_version_fkey') THEN
 ALTER TABLE ` + candidateReclaimNewTable + ` ADD CONSTRAINT retrieval_candidate_record_id_version_fkey FOREIGN KEY(record_id,version) REFERENCES cairn.record_version(record_id,version) NOT VALID; END IF; END $$`,
		`ALTER TABLE ` + candidateReclaimNewTable + ` VALIDATE CONSTRAINT retrieval_candidate_receipt_id_fkey`,
		`ALTER TABLE ` + candidateReclaimNewTable + ` VALIDATE CONSTRAINT retrieval_candidate_record_id_version_fkey`,
	}
	var options []string
	if err = s.pool.QueryRow(ctx, `SELECT COALESCE(reloptions,'{}') FROM pg_class WHERE oid='cairn.retrieval_candidate'::regclass`).Scan(&options); err != nil {
		return err
	}
	for _, option := range options {
		statements = append(statements, `ALTER TABLE `+candidateReclaimNewTable+` SET (`+option+`)`)
	}
	for _, statement := range statements {
		if err = s.reclaimRetryDDL(ctx, req.LockTimeoutMS, statement); err != nil {
			return err
		}
	}
	if _, err = s.pool.Exec(ctx, `ANALYZE `+candidateReclaimNewTable); err != nil {
		return err
	}
	_, err = s.pool.Exec(ctx, `UPDATE `+candidateReclaimLock+`.run SET state=$1`, candidateReclaimBuilt)
	return err
}

// reclaimSwap catches up outside the drain, then attempts the short transaction.
func (s *Store) reclaimSwap(ctx context.Context, req CandidateReclaimRequest, guard *candidateMaintenanceGuard, deadline time.Time, result *CandidateReclaimResult) error {
	for attempt := 0; ; attempt++ {
		if stop, err := s.reclaimCopy(ctx, req, guard, deadline); err != nil {
			return err
		} else if stop != "complete" {
			if attempt > 0 && stop == "max_runtime_seconds" {
				stop = "swap_lock_unavailable"
			}
			result.StoppedBy = stop
			return nil
		}
		err := s.reclaimSwapOnce(ctx, req, result)
		if err == nil {
			return nil
		}
		var pgErr *pgconn.PgError
		retry := errors.As(err, &pgErr) && (pgErr.Code == "55P03" || pgErr.Code == "40P01") || Code(err) == "STALE_CURSOR"
		if !retry {
			return err
		}
		if attempt >= 20 || time.Now().After(deadline) {
			result.StoppedBy = "swap_lock_unavailable"
			return nil
		}
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-time.After(time.Duration(attempt+1) * time.Second):
		}
	}
}

// reclaimSwapOnce takes the restore admission row exclusively, so every ordinary
// transaction has finished and none can start until commit. No repeatable-read
// snapshot can span the swap and read the new relation with an older view.
func (s *Store) reclaimSwapOnce(ctx context.Context, req CandidateReclaimRequest, result *CandidateReclaimResult) error {
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback(context.Background())
	if _, err = tx.Exec(ctx, `SELECT set_config('lock_timeout',$1,true),set_config('cairn.caller',$2,true)`, strconv.Itoa(req.LockTimeoutMS)+"ms", s.channel.Principal); err != nil {
		return err
	}
	if err = restoreAdmission(context.WithValue(ctx, restoreTransitionKey{}, true), tx); err != nil {
		return err
	}
	started := time.Now()
	tag, err := tx.Exec(ctx, `UPDATE cairn.retrieval_receipt r SET candidate_detail_expired_at=clock_timestamp()
 FROM `+candidateReclaimLock+`.expiring e WHERE e.receipt_id=r.receipt_id AND r.candidate_detail_expired_at IS NULL AND NOT `+runBoundReceiptSQL)
	if err != nil {
		return err
	}
	marked := tag.RowsAffected()
	if _, err = tx.Exec(ctx, `LOCK TABLE cairn.retrieval_candidate IN ACCESS EXCLUSIVE MODE`); err != nil {
		return err
	}
	// Whatever is still unexpired and uncopied: new receipts and any that gained
	// a run binding after planning. Only a short tail may run under this drain.
	ids, err := func() ([]string, error) {
		rows, err := tx.Query(ctx, `SELECT r.receipt_id::text FROM cairn.retrieval_receipt r WHERE r.candidate_detail_expired_at IS NULL
 AND NOT EXISTS(SELECT 1 FROM `+candidateReclaimLock+`.copied d WHERE d.receipt_id=r.receipt_id) LIMIT $1`, candidateReclaimMaxTail+1)
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
	}()
	if err != nil {
		return err
	}
	if len(ids) > candidateReclaimMaxTail {
		return failure("STALE_CURSOR", "too many receipts arrived during the drain; catch up and retry")
	}
	for _, id := range ids {
		if _, err = tx.Exec(ctx, reclaimCopyReceiptSQL, id); err != nil {
			return err
		}
	}
	if err = reclaimRenameInto(ctx, tx); err != nil {
		return err
	}
	if _, err = tx.Exec(ctx, `UPDATE `+candidateReclaimLock+`.run SET state=$1,swapped_at=clock_timestamp()`, candidateReclaimSwapped); err != nil {
		return err
	}
	if err = tx.Commit(ctx); err != nil {
		return err
	}
	result.SwapHoldMS = time.Since(started).Milliseconds()
	result.MarkedExpired = marked
	result.TailReceipts = int64(len(ids))
	return nil
}

// Index and constraint names return to the migration-defined ones, so later
// migrations see exactly the table the schema history describes.
func reclaimRenameInto(ctx context.Context, tx pgx.Tx) error {
	statements := []string{
		`ALTER TABLE cairn.retrieval_candidate DROP CONSTRAINT retrieval_candidate_receipt_id_fkey`,
		`ALTER TABLE cairn.retrieval_candidate DROP CONSTRAINT retrieval_candidate_record_id_version_fkey`,
		`ALTER TABLE cairn.retrieval_candidate RENAME CONSTRAINT retrieval_candidate_pkey TO retrieval_candidate_old_pkey`,
		`ALTER INDEX cairn.blocked_demand RENAME TO blocked_demand_old`,
		`ALTER TABLE cairn.retrieval_candidate RENAME TO retrieval_candidate_old`,
		`ALTER TABLE cairn.retrieval_candidate_old SET SCHEMA ` + candidateReclaimLock,
		`ALTER TABLE ` + candidateReclaimNewTable + ` SET SCHEMA cairn`,
		`ALTER TABLE cairn.retrieval_candidate_new RENAME TO retrieval_candidate`,
		`ALTER TABLE cairn.retrieval_candidate RENAME CONSTRAINT retrieval_candidate_new_pkey TO retrieval_candidate_pkey`,
		`ALTER INDEX cairn.blocked_demand_new RENAME TO blocked_demand`,
	}
	for _, statement := range statements {
		if _, err := tx.Exec(ctx, statement); err != nil {
			return fmt.Errorf("%s: %w", statement, err)
		}
	}
	// NOT NULL constraints are named after their table from PostgreSQL 18 on.
	rows, err := tx.Query(ctx, `SELECT conname FROM pg_constraint WHERE conrelid='cairn.retrieval_candidate'::regclass AND contype='n' AND conname LIKE 'retrieval_candidate_new_%'`)
	if err != nil {
		return err
	}
	names := []string{}
	for rows.Next() {
		var name string
		if err = rows.Scan(&name); err != nil {
			rows.Close()
			return err
		}
		names = append(names, name)
	}
	rows.Close()
	if err = rows.Err(); err != nil {
		return err
	}
	for _, name := range names {
		renamed := "retrieval_candidate_" + name[len("retrieval_candidate_new_"):]
		if _, err = tx.Exec(ctx, `ALTER TABLE cairn.retrieval_candidate RENAME CONSTRAINT `+pgx.Identifier{name}.Sanitize()+` TO `+pgx.Identifier{renamed}.Sanitize()); err != nil {
			return err
		}
	}
	return nil
}

// reclaimVerify compares the copied receipts with the old relation before it can
// be dropped: every count, plus a byte digest of a random sample.
func (s *Store) reclaimVerify(ctx context.Context, req CandidateReclaimRequest, result *CandidateReclaimResult) error {
	if req.SampleReceipts == 0 {
		req.SampleReceipts = 200
	}
	var mismatched, unsettled int64
	if err := s.pool.QueryRow(ctx, `WITH old_rows AS (
  SELECT c.receipt_id,count(*) AS rows FROM `+candidateReclaimOldTable+` c
  WHERE EXISTS(SELECT 1 FROM `+candidateReclaimLock+`.copied d WHERE d.receipt_id=c.receipt_id) GROUP BY 1),
 live_rows AS (
  SELECT c.receipt_id,count(*) AS rows FROM cairn.retrieval_candidate c
  WHERE EXISTS(SELECT 1 FROM `+candidateReclaimLock+`.copied d WHERE d.receipt_id=c.receipt_id) GROUP BY 1)
 SELECT count(*) FROM `+candidateReclaimLock+`.copied d
 LEFT JOIN old_rows o USING(receipt_id) LEFT JOIN live_rows l USING(receipt_id)
 WHERE COALESCE(o.rows,0)<>COALESCE(l.rows,0)`).Scan(&mismatched); err != nil {
		return err
	}
	if mismatched != 0 {
		return failure("INTEGRITY_FAILURE", fmt.Sprintf("%d rewritten receipts hold a different row count than the old relation", mismatched))
	}
	// Every planned receipt was either marked or, having become protected, copied.
	if err := s.pool.QueryRow(ctx, `SELECT count(*) FROM `+candidateReclaimLock+`.expiring e JOIN cairn.retrieval_receipt r USING(receipt_id)
 WHERE r.candidate_detail_expired_at IS NULL AND NOT EXISTS(SELECT 1 FROM `+candidateReclaimLock+`.copied d WHERE d.receipt_id=e.receipt_id)`).Scan(&unsettled); err != nil {
		return err
	}
	if unsettled != 0 {
		return failure("INTEGRITY_FAILURE", fmt.Sprintf("%d planned receipts are neither expired nor copied", unsettled))
	}
	rows, err := s.pool.Query(ctx, `SELECT receipt_id::text FROM `+candidateReclaimLock+`.copied WHERE rows>0 ORDER BY random() LIMIT $1`, req.SampleReceipts)
	if err != nil {
		return err
	}
	ids := []string{}
	for rows.Next() {
		var id string
		if err = rows.Scan(&id); err != nil {
			rows.Close()
			return err
		}
		ids = append(ids, id)
	}
	rows.Close()
	if err = rows.Err(); err != nil {
		return err
	}
	const digest = `SELECT count(*),COALESCE(md5(string_agg(t::text,E'\n' ORDER BY record_id,version)),'') FROM %s t WHERE receipt_id=$1`
	for _, id := range ids {
		var oldRows, newRows int64
		var oldDigest, newDigest string
		if err = s.pool.QueryRow(ctx, fmt.Sprintf(digest, candidateReclaimOldTable), id).Scan(&oldRows, &oldDigest); err != nil {
			return err
		}
		if err = s.pool.QueryRow(ctx, fmt.Sprintf(digest, "cairn.retrieval_candidate"), id).Scan(&newRows, &newDigest); err != nil {
			return err
		}
		if oldRows != newRows || oldDigest != newDigest {
			return failure("INTEGRITY_FAILURE", "receipt "+id+" differs between the old and rewritten relations")
		}
	}
	result.VerifiedReceipts = len(ids)
	_, err = s.pool.Exec(ctx, `UPDATE `+candidateReclaimLock+`.run SET state=$1`, candidateReclaimVerified)
	return err
}

// reclaimFinish is the only irreversible phase: it drops the old relation.
func (s *Store) reclaimFinish(ctx context.Context, req CandidateReclaimRequest) error {
	if !req.ConfirmDropOld {
		return failure("INVALID_REQUEST", "finish drops the old relation and requires confirm_drop_old")
	}
	return s.reclaimRetryDDL(ctx, req.LockTimeoutMS, `DROP SCHEMA `+candidateReclaimLock+` CASCADE`)
}
