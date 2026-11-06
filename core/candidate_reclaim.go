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
//
// Expiring receipts are marked first, in small transactions, while their rows are
// still in place: a marked receipt refuses, so it needs no rows, and an unmarked
// receipt is copied in full. The swap therefore has no bulk work to do.
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
	candidateReclaimAborting = "aborting"

	candidateReclaimChunkPages = 65536
	// A receipt holds at most one row per note, so this bounds the work under the drain.
	candidateReclaimMaxTail = 4
	// Attempts that held the drain and still failed have paused ordinary work.
	candidateReclaimMaxStalls = 3
)

// candidateReclaimHoldLimit bounds each statement while the drain is held.
var candidateReclaimHoldLimit = "3s"

// reclaimDrainHeldHook lets tests act at the moment the drain is held.
var reclaimDrainHeldHook func()

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
	MarkedReceipts    int64      `json:"marked_receipts"`
	RetainedReceipts  int64      `json:"retained_receipts"`
	CopiedReceipts    int64      `json:"copied_receipts"`
	CopiedRows        int64      `json:"copied_rows"`
	ResidualRows      int64      `json:"residual_rows"`
	ScanBlock         int64      `json:"scan_block"`
	ScanBlocks        int64      `json:"scan_blocks"`
	WALBytes          int64      `json:"wal_bytes"`
	SwapHoldMS        int64      `json:"swap_hold_ms,omitempty"`
	SwapAttempts      int        `json:"swap_attempts,omitempty"`
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
	allowed := map[string][]string{
		"status": {candidateReclaimNone, candidateReclaimPlanned, candidateReclaimBuilt, candidateReclaimSwapped, candidateReclaimVerified, candidateReclaimAborting},
		"plan":   {candidateReclaimNone},
		"copy":   {candidateReclaimPlanned, candidateReclaimBuilt},
		"build":  {candidateReclaimPlanned},
		"swap":   {candidateReclaimBuilt},
		"verify": {candidateReclaimSwapped},
		"finish": {candidateReclaimVerified},
		"abort":  {candidateReclaimPlanned, candidateReclaimBuilt, candidateReclaimAborting},
	}[req.Phase]
	if allowed == nil {
		return result, failure("INVALID_REQUEST", "phase must be status, plan, copy, build, swap, verify, finish or abort")
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
		result.StoppedBy, err = s.reclaimBuild(ctx, req, guard)
	case "swap":
		err = s.reclaimSwap(ctx, req, guard, deadline, &result)
	case "verify":
		err = s.reclaimVerify(ctx, req, &result)
	case "finish":
		err = s.reclaimFinish(ctx, req)
	case "abort":
		err = s.reclaimAbort(ctx, req)
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
 (SELECT count(*) FROM `+candidateReclaimLock+`.expiring e JOIN cairn.retrieval_receipt x USING(receipt_id) WHERE x.candidate_detail_expired_at IS NOT NULL),
 (SELECT count(*) FROM `+candidateReclaimLock+`.copied),
 (SELECT COALESCE(sum(rows),0)::bigint FROM `+candidateReclaimLock+`.copied),
 (SELECT count(*) FROM cairn.retrieval_receipt x WHERE x.candidate_detail_expired_at IS NULL),
 COALESCE(to_regclass('`+candidateReclaimNewTable+`'),to_regclass('`+candidateReclaimOldTable+`'))::text
 FROM `+candidateReclaimLock+`.run r`).Scan(&result.WindowDays, &cutoff, &result.ScanBlock, &result.ScanBlocks, &result.ResidualRows,
		&result.ExpiringReceipts, &result.MarkedReceipts, &result.CopiedReceipts, &result.CopiedRows, &result.RetainedReceipts, &working)
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

// A swap replaces the relation, so anything attached to the old one would vanish:
// grants, triggers, rules, views, policies, publications, extra indexes or
// constraints, extended statistics, and ownership by another role. The
// migration-defined table has none. Checked at plan and again under the swap's lock.
func reclaimPreflight(ctx context.Context, tx pgx.Tx) error {
	var unexpected bool
	err := tx.QueryRow(ctx, `SELECT c.relhasrules OR c.relrowsecurity OR c.relhassubclass OR c.relkind<>'r'
 OR pg_get_userbyid(c.relowner)<>current_user
 OR EXISTS(SELECT 1 FROM aclexplode(c.relacl) a WHERE a.grantee<>c.relowner)
 OR EXISTS(SELECT 1 FROM pg_trigger t WHERE t.tgrelid=c.oid AND NOT t.tgisinternal)
 OR EXISTS(SELECT 1 FROM pg_publication_rel p WHERE p.prrelid=c.oid)
 OR EXISTS(SELECT 1 FROM pg_statistic_ext x WHERE x.stxrelid=c.oid)
 OR EXISTS(SELECT 1 FROM pg_depend d JOIN pg_rewrite w ON w.oid=d.objid WHERE d.classid='pg_rewrite'::regclass AND d.refobjid=c.oid)
 OR (SELECT count(*) FROM pg_index i WHERE i.indrelid=c.oid)<>2
 OR EXISTS(SELECT 1 FROM pg_index i JOIN pg_class x ON x.oid=i.indexrelid WHERE i.indrelid=c.oid AND x.relname NOT IN ('retrieval_candidate_pkey','blocked_demand'))
 OR (SELECT count(*) FROM pg_constraint k WHERE k.conrelid=c.oid AND k.contype<>'n')<>3
 OR EXISTS(SELECT 1 FROM pg_constraint k WHERE k.confrelid=c.oid)
 FROM pg_class c WHERE c.oid='cairn.retrieval_candidate'::regclass`).Scan(&unexpected)
	if err != nil {
		return err
	}
	if unexpected {
		return failure("DEPENDENCY_CONFLICT", "retrieval_candidate has grants, triggers, rules, views, policies, extra indexes or another owner that a rewrite would not preserve")
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
  state text NOT NULL, marking_done boolean NOT NULL DEFAULT false,
  scan_block bigint NOT NULL DEFAULT 0, scan_blocks bigint NOT NULL,
  residual_rows bigint NOT NULL DEFAULT 0, planned_at timestamptz NOT NULL DEFAULT clock_timestamp(), swapped_at timestamptz);
 CREATE TABLE `+candidateReclaimLock+`.expiring(receipt_id uuid PRIMARY KEY);
 CREATE TABLE `+candidateReclaimLock+`.copied(receipt_id uuid PRIMARY KEY, rows bigint NOT NULL);
 CREATE TABLE `+candidateReclaimNewTable+` (LIKE cairn.retrieval_candidate INCLUDING ALL EXCLUDING INDEXES)`); err != nil {
		return err
	}
	if _, err = tx.Exec(ctx, `INSERT INTO `+candidateReclaimLock+`.run(window_days,keep_selected,cutoff,state,scan_blocks)
 SELECT $1::int,$2::int,now()-make_interval(days => $1::int),$3,pg_relation_size('cairn.retrieval_candidate')/current_setting('block_size')::bigint`,
		req.WindowDays, req.KeepSelectedPerRepo, candidateReclaimPlanned); err != nil {
		return err
	}
	if _, err = tx.Exec(ctx, `WITH fixtures AS (`+newestSelectingReceiptsSQL+`)
 INSERT INTO `+candidateReclaimLock+`.expiring
 SELECT r.receipt_id FROM cairn.retrieval_receipt r, `+candidateReclaimLock+`.run p
 WHERE r.candidate_detail_expired_at IS NULL AND r.explanation_version>0 AND r.created_at<p.cutoff
 AND NOT `+runBoundReceiptSQL+` AND r.receipt_id NOT IN (SELECT receipt_id FROM fixtures)`, req.KeepSelectedPerRepo, s.channel.Principal); err != nil {
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

// reclaimCopy marks expiring receipts, scans for rows that outlive them, then copies
// every receipt left unmarked. Marking ends before the scan starts, so a receipt is
// either marked with its retained rows or copied whole, never both. Each transaction
// records its own progress, so stopping at any point loses and repeats nothing.
func (s *Store) reclaimCopy(ctx context.Context, req CandidateReclaimRequest, guard *candidateMaintenanceGuard, deadline time.Time) (string, error) {
	if stop, err := s.reclaimMark(ctx, req, guard, deadline); err != nil || stop != "complete" {
		return stop, err
	}
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
		ids, err := s.reclaimPendingReceipts(ctx, s.pool, 200)
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

// reclaimMark expires planned receipts without touching their rows. A receipt it
// cannot mark, because it is locked or became run-bound, is simply copied.
func (s *Store) reclaimMark(ctx context.Context, req CandidateReclaimRequest, guard *candidateMaintenanceGuard, deadline time.Time) (string, error) {
	var done bool
	var cutoff time.Time
	if err := s.pool.QueryRow(ctx, `SELECT marking_done,cutoff FROM `+candidateReclaimLock+`.run`).Scan(&done, &cutoff); err != nil {
		return "", err
	}
	if done {
		return "complete", nil
	}
	skipped := []string{}
	for {
		rows, err := s.pool.Query(ctx, `SELECT e.receipt_id::text FROM `+candidateReclaimLock+`.expiring e JOIN cairn.retrieval_receipt r USING(receipt_id)
 WHERE r.candidate_detail_expired_at IS NULL AND NOT (e.receipt_id=ANY($1::uuid[])) ORDER BY r.created_at,e.receipt_id LIMIT 500`, skipped)
		if err != nil {
			return "", err
		}
		ids, err := pgx.CollectRows(rows, pgx.RowTo[string])
		if err != nil {
			return "", err
		}
		if len(ids) == 0 {
			break
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
			outcome, err := s.expireReceiptDetail(ctx, id, cutoff, false)
			if err != nil {
				return "", err
			}
			if !outcome.expired {
				skipped = append(skipped, id)
			}
			if err = reclaimSleep(ctx, req.PauseMilliseconds); err != nil {
				return "", err
			}
		}
	}
	_, err := s.pool.Exec(ctx, `UPDATE `+candidateReclaimLock+`.run SET marking_done=true`)
	return "complete", err
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
 AND EXISTS(SELECT 1 FROM cairn.retrieval_receipt r WHERE r.receipt_id=c.receipt_id AND r.candidate_detail_expired_at IS NOT NULL)`,
		"("+strconv.FormatInt(block, 10)+",0)", "("+strconv.FormatInt(end, 10)+",0)")
	if err != nil {
		return false, err
	}
	if _, err = tx.Exec(ctx, `UPDATE `+candidateReclaimLock+`.run SET scan_block=$1,residual_rows=residual_rows+$2`, min(end, blocks), tag.RowsAffected()); err != nil {
		return false, err
	}
	return false, tx.Commit(ctx)
}

type candidateQueryer interface {
	Query(ctx context.Context, sql string, args ...any) (pgx.Rows, error)
}

func (s *Store) reclaimPendingReceipts(ctx context.Context, q candidateQueryer, limit int) ([]string, error) {
	rows, err := q.Query(ctx, `SELECT r.receipt_id::text FROM cairn.retrieval_receipt r
 WHERE r.candidate_detail_expired_at IS NULL
 AND NOT EXISTS(SELECT 1 FROM `+candidateReclaimLock+`.copied d WHERE d.receipt_id=r.receipt_id)
 ORDER BY r.created_at,r.receipt_id LIMIT $1`, limit)
	if err != nil {
		return nil, err
	}
	return pgx.CollectRows(rows, pgx.RowTo[string])
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
 INSERT INTO ` + candidateReclaimNewTable + ` SELECT c.* FROM cairn.retrieval_candidate c WHERE c.receipt_id=$1 RETURNING 1)
 INSERT INTO ` + candidateReclaimLock + `.copied(receipt_id,rows) SELECT $1,count(*) FROM inserted`

// reclaimRetryDDL runs a statement that needs a strong lock. While it waits, later
// readers of that table queue behind it, so attempts are capped rather than spread
// over the run's whole time budget. The phase can simply be repeated later.
func (s *Store) reclaimRetryDDL(ctx context.Context, lockTimeoutMS int, statement string) error {
	for attempt := 1; ; attempt++ {
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
		if !errors.As(err, &pgErr) || pgErr.Code != "55P03" {
			return err
		}
		if attempt >= candidateReclaimMaxStalls {
			return failure("MAINTENANCE_ACTIVE", "candidate maintenance could not get its locks; this step changed nothing, repeat the phase once other work has finished")
		}
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-time.After(time.Duration(attempt) * time.Second):
		}
	}
}

// reclaimBuild creates constraints after the bulk copy: maintaining a random-keyed
// index row by row would be slower and log far more. Nothing reads this table yet.
func (s *Store) reclaimBuild(ctx context.Context, req CandidateReclaimRequest, guard *candidateMaintenanceGuard) (string, error) {
	var block, blocks int64
	var done bool
	if err := s.pool.QueryRow(ctx, `SELECT scan_block,scan_blocks,marking_done FROM `+candidateReclaimLock+`.run`).Scan(&block, &blocks, &done); err != nil {
		return "", err
	}
	pending, err := s.reclaimPendingReceipts(ctx, s.pool, 1025)
	if err != nil {
		return "", err
	}
	if !done || block < blocks || len(pending) > 1024 {
		return "", failure("DEPENDENCY_CONFLICT", "complete the copy phase before building constraints")
	}
	// Sort spill and the index itself come to about 30% of the heap.
	var heap int64
	if err = s.pool.QueryRow(ctx, `SELECT pg_relation_size('`+candidateReclaimNewTable+`')`).Scan(&heap); err != nil {
		return "", err
	}
	guard.reserve = heap * 3 / 10
	limit, err := guard.pressure(ctx)
	if err != nil {
		return "", err
	}
	if limit != "" {
		return limit, nil
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
		return "", err
	}
	for _, option := range options {
		statements = append(statements, `ALTER TABLE `+candidateReclaimNewTable+` SET (`+option+`)`)
	}
	for _, statement := range statements {
		if err = s.reclaimRetryDDL(ctx, req.LockTimeoutMS, statement); err != nil {
			return "", err
		}
	}
	if _, err = s.pool.Exec(ctx, `ANALYZE `+candidateReclaimNewTable); err != nil {
		return "", err
	}
	_, err = s.pool.Exec(ctx, `UPDATE `+candidateReclaimLock+`.run SET state=$1`, candidateReclaimBuilt)
	return "complete", err
}

// reclaimSwap catches up outside the drain, then attempts the short transaction.
// Waiting for the drain does not delay ordinary work; holding it does, so attempts
// that held it and failed are limited separately from those that never got it.
func (s *Store) reclaimSwap(ctx context.Context, req CandidateReclaimRequest, guard *candidateMaintenanceGuard, deadline time.Time, result *CandidateReclaimResult) error {
	stalls := 0
	for attempt := 0; ; attempt++ {
		result.SwapAttempts = attempt + 1
		stop, err := s.reclaimCopy(ctx, req, guard, deadline)
		if err != nil {
			return err
		}
		if stop != "complete" {
			if attempt > 0 && stop == "max_runtime_seconds" {
				stop = "swap_lock_unavailable"
			}
			result.StoppedBy = stop
			return nil
		}
		held, err := s.reclaimSwapOnce(ctx, req, result)
		if err == nil {
			return nil
		}
		var pgErr *pgconn.PgError
		retry := Code(err) == "STALE_CURSOR" || (ctx.Err() == nil && errors.As(err, &pgErr) &&
			(pgErr.Code == "55P03" || pgErr.Code == "57014" || pgErr.Code == "40P01"))
		if !retry {
			return err
		}
		if held {
			stalls++
		}
		if stalls >= candidateReclaimMaxStalls || time.Now().After(deadline) {
			result.StoppedBy = "swap_lock_unavailable"
			return nil
		}
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-time.After(time.Duration(min(attempt+1, 5)) * time.Second):
		}
	}
}

// reclaimSwapOnce takes the restore admission row exclusively, draining
// transactions that participate in that check and blocking new participants
// until commit. Direct-pool helpers are outside this barrier. Writing the row
// also fails repeatable-read or serializable transactions already waiting behind
// it: their snapshots predate the swap and could miss the new relation's rows.
func (s *Store) reclaimSwapOnce(ctx context.Context, req CandidateReclaimRequest, result *CandidateReclaimResult) (held bool, err error) {
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return false, err
	}
	defer tx.Rollback(context.Background())
	timeout := strconv.Itoa(req.LockTimeoutMS) + "ms"
	// A row-lock wait on short overlapping sharers can outlast lock_timeout, which
	// applies to each wait, so the statement itself is bounded as well. If this client
	// stalls while holding the drain, the server ends the session and releases it.
	if _, err = tx.Exec(ctx, `SELECT set_config('lock_timeout',$1,true),set_config('statement_timeout',$1,true),set_config('idle_in_transaction_session_timeout',$2,true),set_config('cairn.caller',$3,true)`,
		timeout, candidateReclaimHoldLimit, s.channel.Principal); err != nil {
		return false, err
	}
	if err = restoreAdmission(context.WithValue(ctx, restoreTransitionKey{}, true), tx); err != nil {
		return false, err
	}
	started := time.Now()
	// Holding the drain delays participating transactions, so from here each
	// statement has a fixed limit however patient the caller is about the wait.
	if _, err = tx.Exec(ctx, `SELECT set_config('lock_timeout',$1,true),set_config('statement_timeout',$1,true)`, candidateReclaimHoldLimit); err != nil {
		return true, err
	}
	if reclaimDrainHeldHook != nil {
		reclaimDrainHeldHook()
	}
	if _, err = tx.Exec(ctx, `UPDATE cairn.restore_admission SET paused=paused WHERE singleton`); err != nil {
		return true, err
	}
	if _, err = tx.Exec(ctx, `LOCK TABLE cairn.retrieval_candidate IN ACCESS EXCLUSIVE MODE`); err != nil {
		return true, err
	}
	if err = reclaimPreflight(ctx, tx); err != nil {
		return true, err
	}
	var sameDefinition bool
	if err = tx.QueryRow(ctx, `SELECT COALESCE(a.reloptions,'{}')=COALESCE(b.reloptions,'{}') AND
 (SELECT array_agg(x.attname||':'||format_type(x.atttypid,x.atttypmod)||':'||x.attnotnull::text ORDER BY x.attnum) FROM pg_attribute x WHERE x.attrelid=a.oid AND x.attnum>0 AND NOT x.attisdropped)=
 (SELECT array_agg(x.attname||':'||format_type(x.atttypid,x.atttypmod)||':'||x.attnotnull::text ORDER BY x.attnum) FROM pg_attribute x WHERE x.attrelid=b.oid AND x.attnum>0 AND NOT x.attisdropped)
 FROM pg_class a,pg_class b WHERE a.oid='cairn.retrieval_candidate'::regclass AND b.oid='`+candidateReclaimNewTable+`'::regclass`).Scan(&sameDefinition); err != nil {
		return true, err
	}
	if !sameDefinition {
		return true, failure("DEPENDENCY_CONFLICT", "retrieval_candidate columns or storage options changed after the build phase")
	}
	ids, err := s.reclaimPendingReceipts(ctx, tx, candidateReclaimMaxTail+1)
	if err != nil {
		return true, err
	}
	if len(ids) > candidateReclaimMaxTail {
		return true, failure("STALE_CURSOR", "too many receipts arrived during the drain; catch up and retry")
	}
	for _, id := range ids {
		if _, err = tx.Exec(ctx, reclaimCopyReceiptSQL, id); err != nil {
			return true, err
		}
	}
	if err = reclaimRenameInto(ctx, tx); err != nil {
		return true, err
	}
	if _, err = tx.Exec(ctx, `UPDATE `+candidateReclaimLock+`.run SET state=$1,swapped_at=clock_timestamp()`, candidateReclaimSwapped); err != nil {
		return true, err
	}
	if err = tx.Commit(ctx); err != nil {
		return true, err
	}
	result.SwapHoldMS = time.Since(started).Milliseconds()
	result.TailReceipts = int64(len(ids))
	return true, nil
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
	names, err := pgx.CollectRows(rows, pgx.RowTo[string])
	if err != nil {
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

// reclaimVerify compares the live relation with the old one before it can be
// dropped. Per receipt, an unmarked receipt must hold every old row and a marked
// one exactly the rows that outlive expiry. A random sample also compares bytes.
func (s *Store) reclaimVerify(ctx context.Context, req CandidateReclaimRequest, result *CandidateReclaimResult) error {
	if req.SampleReceipts == 0 {
		req.SampleReceipts = 200
	}
	var mismatched int64
	if err := s.pool.QueryRow(ctx, `WITH old_rows AS (
  SELECT c.receipt_id,count(*) AS total,count(*) FILTER (WHERE `+retainedCandidateSQL+`) AS kept FROM `+candidateReclaimOldTable+` c GROUP BY 1),
 live_rows AS (
  SELECT c.receipt_id,count(*) AS rows FROM cairn.retrieval_candidate c GROUP BY 1)
 SELECT count(*) FROM old_rows o
 JOIN cairn.retrieval_receipt r USING(receipt_id)
 LEFT JOIN live_rows l USING(receipt_id)
 WHERE COALESCE(l.rows,0)<>CASE WHEN r.candidate_detail_expired_at IS NULL THEN o.total ELSE o.kept END`).Scan(&mismatched); err != nil {
		return err
	}
	if mismatched != 0 {
		return failure("INTEGRITY_FAILURE", fmt.Sprintf("%d receipts hold different row counts in the old and rewritten relations; the old relation is intact", mismatched))
	}
	rows, err := s.pool.Query(ctx, `SELECT receipt_id::text,false FROM (SELECT receipt_id FROM `+candidateReclaimLock+`.copied WHERE rows>0 ORDER BY random() LIMIT $1) whole
 UNION ALL
 SELECT receipt_id::text,true FROM (SELECT r.receipt_id FROM cairn.retrieval_receipt r
  WHERE r.candidate_detail_expired_at IS NOT NULL AND EXISTS(SELECT 1 FROM cairn.retrieval_candidate c WHERE c.receipt_id=r.receipt_id) LIMIT $1) kept`, req.SampleReceipts)
	if err != nil {
		return err
	}
	type sample struct {
		id           string
		retainedOnly bool
	}
	samples, err := pgx.CollectRows(rows, func(row pgx.CollectableRow) (sample, error) {
		var item sample
		return item, row.Scan(&item.id, &item.retainedOnly)
	})
	if err != nil {
		return err
	}
	const digest = `SELECT count(*),COALESCE(md5(string_agg(c::text,E'\n' ORDER BY record_id,version)),'') FROM %s c WHERE receipt_id=$1 AND %s`
	for _, item := range samples {
		filter := "true"
		if item.retainedOnly {
			filter = retainedCandidateSQL
		}
		var oldRows, newRows int64
		var oldDigest, newDigest string
		if err = s.pool.QueryRow(ctx, fmt.Sprintf(digest, candidateReclaimOldTable, filter), item.id).Scan(&oldRows, &oldDigest); err != nil {
			return err
		}
		if err = s.pool.QueryRow(ctx, fmt.Sprintf(digest, "cairn.retrieval_candidate", "true"), item.id).Scan(&newRows, &newDigest); err != nil {
			return err
		}
		if oldRows != newRows || oldDigest != newDigest {
			return failure("INTEGRITY_FAILURE", "receipt "+item.id+" differs between the old and rewritten relations; the old relation is intact")
		}
	}
	result.VerifiedReceipts = len(samples)
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

// reclaimAbort restores exactly what plan and marking changed. Every row of a planned
// receipt is still in the live relation until the swap, so clearing its marker makes
// it whole again. The state change stops a half-aborted rewrite from being continued.
func (s *Store) reclaimAbort(ctx context.Context, req CandidateReclaimRequest) error {
	if _, err := s.pool.Exec(ctx, `UPDATE `+candidateReclaimLock+`.run SET state=$1`, candidateReclaimAborting); err != nil {
		return err
	}
	for {
		tx, err := s.begin(ctx)
		if err != nil {
			return err
		}
		tag, err := tx.Exec(ctx, `UPDATE cairn.retrieval_receipt r SET candidate_detail_expired_at=NULL FROM (
 SELECT x.receipt_id FROM `+candidateReclaimLock+`.expiring e JOIN cairn.retrieval_receipt x USING(receipt_id)
 WHERE x.candidate_detail_expired_at IS NOT NULL LIMIT 500) p WHERE p.receipt_id=r.receipt_id`)
		if err == nil {
			err = tx.Commit(ctx)
		}
		if err != nil {
			_ = tx.Rollback(context.Background())
			return err
		}
		if tag.RowsAffected() == 0 {
			break
		}
	}
	return s.reclaimRetryDDL(ctx, req.LockTimeoutMS, `DROP SCHEMA `+candidateReclaimLock+` CASCADE`)
}
