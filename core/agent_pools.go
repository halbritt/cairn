package core

import (
	"context"
	"errors"
	"path/filepath"
	"slices"
	"strings"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
)

// Pool selectors describe launcher requirements, not instructions or authority.
type PoolRequirements struct {
	Workspace    string   `json:"workspace"`
	Harness      string   `json:"harness,omitempty"`
	Model        string   `json:"model,omitempty"`
	Capabilities []string `json:"capabilities,omitempty"`
}
type WorkerSpec struct {
	Name                 string   `json:"name"`
	Harness              string   `json:"harness"`
	Model                string   `json:"model,omitempty"`
	Workspace            string   `json:"workspace"`
	Pools                []string `json:"pools"`
	Capabilities         []string `json:"capabilities,omitempty"`
	LaunchSpacingSeconds int      `json:"launch_spacing_seconds"`
}
type WorkerPoolConfigRequest struct {
	RequestID              string `json:"request_id"`
	Repo                   string `json:"repo"`
	Name                   string `json:"name"`
	Enabled                bool   `json:"enabled"`
	MaxPending             int    `json:"max_pending"`
	MaxPendingPerPublisher int    `json:"max_pending_per_publisher"`
	ExpectedRevision       int64  `json:"expected_revision"`
}
type WorkerPool struct {
	Repo                   string `json:"repo"`
	Name                   string `json:"name"`
	Enabled                bool   `json:"enabled"`
	MaxPending             int    `json:"max_pending"`
	MaxPendingPerPublisher int    `json:"max_pending_per_publisher"`
	Revision               int64  `json:"revision"`
}
type WorkerRegisterRequest struct {
	RequestID string     `json:"request_id"`
	Repo      string     `json:"repo,omitempty"`
	Spec      WorkerSpec `json:"spec"`
}
type WorkerSlot struct {
	Repo               string     `json:"repo"`
	Consumer           string     `json:"consumer"`
	Spec               WorkerSpec `json:"spec"`
	SupervisorID       string     `json:"supervisor_id"`
	DatabaseGeneration int64      `json:"database_generation"`
	Revision           int64      `json:"revision"`
	HeartbeatAt        time.Time  `json:"heartbeat_at"`
	ExpiresAt          time.Time  `json:"expires_at"`
	Health             string     `json:"health"`
	Reason             string     `json:"reason"`
	HealthAt           time.Time  `json:"health_at"`
	RetryAt            *time.Time `json:"retry_at,omitempty"`
	NextLaunchAt       time.Time  `json:"next_launch_at"`
	Online             bool       `json:"online"`
}
type PoolRequestStatus struct {
	Control    *WorkControl `json:"control,omitempty"`
	State      string       `json:"state"`
	DeliveryID string       `json:"delivery_id,omitempty"`
	Consumer   string       `json:"consumer,omitempty"`
	AssignedAt *time.Time   `json:"assigned_at,omitempty"`
}

func validPoolNames(names []string) bool {
	if len(names) > 16 {
		return false
	}
	seen := map[string]bool{}
	for _, name := range names {
		if !eventName.MatchString(name) || seen[name] {
			return false
		}
		seen[name] = true
	}
	return true
}
func (r PoolRequirements) validate() error {
	if !filepath.IsAbs(r.Workspace) || filepath.Clean(r.Workspace) != r.Workspace || len(r.Workspace) > 4096 || strings.ContainsRune(r.Workspace, 0) ||
		(r.Harness != "" && !eventName.MatchString(r.Harness)) || len(r.Model) > 256 || strings.ContainsRune(r.Model, 0) || !validPoolNames(r.Capabilities) {
		return failure("INVALID_REQUEST", "pool requires a canonical absolute workspace and bounded exact selectors")
	}
	return nil
}

// Validate checks launcher metadata before it is registered or installed.
func (w WorkerSpec) Validate() error {
	if !eventName.MatchString(w.Name) || !eventName.MatchString(w.Harness) || !validPoolNames(w.Pools) || len(w.Pools) == 0 || w.LaunchSpacingSeconds < 1 || w.LaunchSpacingSeconds > 3600 {
		return failure("INVALID_REQUEST", "worker needs a name, harness, 1-16 pools and launch spacing of 1-3600 seconds")
	}
	return (PoolRequirements{Workspace: w.Workspace, Harness: w.Harness, Model: w.Model, Capabilities: w.Capabilities}).validate()
}
func (s *Store) ConfigureWorkerPool(ctx context.Context, req WorkerPoolConfigRequest) (WorkerPool, error) {
	if !s.channel.Operator || s.channel.Repo != "" {
		return WorkerPool{}, failure("AUTHORITY_DENIED", "pool configuration requires the existing unscoped operator channel")
	}
	var err error
	if req.Repo, err = s.eventScope(req.Repo, ""); err != nil {
		return WorkerPool{}, err
	}
	if !eventName.MatchString(req.Name) || req.MaxPending < 1 || req.MaxPending > 10000 || req.MaxPendingPerPublisher < 1 || req.MaxPendingPerPublisher > req.MaxPending || req.ExpectedRevision < 0 {
		return WorkerPool{}, failure("INVALID_REQUEST", "bounded pool limits and expected revision required")
	}
	return mutate(ctx, s, "worker-pool-configure", req.RequestID, req, func(tx pgx.Tx) (WorkerPool, error) {
		if err := lock(ctx, tx, "pool-config:"+req.Repo); err != nil {
			return WorkerPool{}, err
		}
		var rev int64
		err := tx.QueryRow(ctx, `SELECT revision FROM cairn.agent_worker_pool WHERE repo=$1 AND name=$2 FOR UPDATE`, req.Repo, req.Name).Scan(&rev)
		if err != nil && !errors.Is(err, pgx.ErrNoRows) {
			return WorkerPool{}, err
		}
		if rev != req.ExpectedRevision {
			return WorkerPool{}, failure("VERSION_CONFLICT", "pool configuration revision changed")
		}
		if rev == 0 {
			var n int
			if err = tx.QueryRow(ctx, `SELECT count(*) FROM cairn.agent_worker_pool WHERE repo=$1`, req.Repo).Scan(&n); err != nil {
				return WorkerPool{}, err
			}
			if n >= 128 {
				return WorkerPool{}, failure("BUDGET_REFUSED", "collection has 128 configured pools")
			}
		}
		out := WorkerPool{req.Repo, req.Name, req.Enabled, req.MaxPending, req.MaxPendingPerPublisher, rev + 1}
		_, err = tx.Exec(ctx, `INSERT INTO cairn.agent_worker_pool(repo,name,enabled,max_pending,max_pending_per_publisher) VALUES($1,$2,$3,$4,$5) ON CONFLICT(repo,name) DO UPDATE SET enabled=excluded.enabled,max_pending=excluded.max_pending,max_pending_per_publisher=excluded.max_pending_per_publisher,revision=agent_worker_pool.revision+1`, req.Repo, req.Name, req.Enabled, req.MaxPending, req.MaxPendingPerPublisher)
		return out, err
	})
}

const workerColumns = `repo,consumer,spec,supervisor_id::text,database_generation,revision,heartbeat_at,expires_at,health,reason,health_at,retry_at,next_launch_at,(expires_at>clock_timestamp() AND database_generation=(SELECT generation FROM cairn.retrieval_generation WHERE singleton))`

func scanWorker(row pgx.Row) (WorkerSlot, error) {
	var w WorkerSlot
	err := row.Scan(&w.Repo, &w.Consumer, &w.Spec, &w.SupervisorID, &w.DatabaseGeneration, &w.Revision, &w.HeartbeatAt, &w.ExpiresAt, &w.Health, &w.Reason, &w.HealthAt, &w.RetryAt, &w.NextLaunchAt, &w.Online)
	return w, err
}
func (s *Store) workerAccess(dest Destination) error {
	if err := validEventDestination(dest); err != nil {
		return err
	}
	if dest.Name != "hosted" {
		return failure("DESTINATION_PROHIBITED", "worker slots use the hosted runner")
	}
	if s.session != nil || s.channel.Instrumented {
		return failure("INVALID_REQUEST", "worker slot requires an ordinary base profile")
	}
	return nil
}
func (s *Store) RegisterWorkerSlot(ctx context.Context, req WorkerRegisterRequest, dest Destination) (WorkerSlot, error) {
	if err := s.workerAccess(dest); err != nil {
		return WorkerSlot{}, err
	}
	if err := req.Spec.Validate(); err != nil {
		return WorkerSlot{}, err
	}
	var err error
	if req.Repo, err = s.eventScope(req.Repo, ""); err != nil {
		return WorkerSlot{}, err
	}
	return mutate(ctx, s, "worker-register", req.RequestID, req, func(tx pgx.Tx) (WorkerSlot, error) {
		generation, err := retrievalGeneration(ctx, tx)
		if err != nil {
			return WorkerSlot{}, err
		}
		if err = lock(ctx, tx, "worker-register:"+req.Repo); err != nil {
			return WorkerSlot{}, err
		}
		if err = lock(ctx, tx, "wake:"+req.Repo+":"+s.channel.Principal); err != nil {
			return WorkerSlot{}, err
		}
		var busy bool
		if err = tx.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM cairn.agent_wake_attempt WHERE repo=$1 AND consumer=$2 AND finished_at IS NULL)`, req.Repo, s.channel.Principal).Scan(&busy); err != nil {
			return WorkerSlot{}, err
		}
		if busy {
			return WorkerSlot{}, failure("VERSION_CONFLICT", "reconcile the active wake before replacing its supervisor")
		}
		var n int
		if err = tx.QueryRow(ctx, `SELECT count(*) FROM cairn.agent_worker_slot WHERE repo=$1 AND consumer<>$2`, req.Repo, s.channel.Principal).Scan(&n); err != nil {
			return WorkerSlot{}, err
		}
		if n >= 128 {
			return WorkerSlot{}, failure("BUDGET_REFUSED", "collection has 128 configured worker slots")
		}
		return scanWorker(tx.QueryRow(ctx, `INSERT INTO cairn.agent_worker_slot(repo,spec,supervisor_id,database_generation) VALUES($1,$2,$3,$4) ON CONFLICT(repo,consumer) DO UPDATE SET spec=excluded.spec,supervisor_id=excluded.supervisor_id,database_generation=excluded.database_generation,revision=agent_worker_slot.revision+1,heartbeat_at=clock_timestamp(),expires_at=clock_timestamp()+interval '90 seconds' RETURNING `+workerColumns, req.Repo, req.Spec, uuid.NewString(), generation))
	})
}

// The slot row serializes health changes and claims. Re-registration also takes
// the wake lock, so an active assignment cannot change supervisor incarnations.
func (s *Store) claimWorker(ctx context.Context, tx pgx.Tx, repo, id string) (*WorkerSlot, error) {
	w, err := scanWorker(tx.QueryRow(ctx, `SELECT `+workerColumns+` FROM cairn.agent_worker_slot WHERE repo=$1 AND consumer=$2 FOR UPDATE`, repo, s.channel.Principal))
	if errors.Is(err, pgx.ErrNoRows) && id == "" {
		return nil, nil
	}
	if err != nil {
		if errors.Is(err, pgx.ErrNoRows) {
			return nil, failure("STALE_WORKER", "worker not registered")
		}
		return nil, err
	}
	if id != w.SupervisorID || !w.Online {
		return nil, failure("STALE_WORKER", "supervisor replaced, expired or fenced")
	}
	return &w, nil
}
func (s *Store) admitPool(ctx context.Context, tx pgx.Tx, req PublishEventRequest, sensitivity string) error {
	if sensitivity != "shareable" {
		return failure("DESTINATION_PROHIBITED", "fresh pool workers require shareable sources")
	}
	var enabled bool
	var max, per int
	err := tx.QueryRow(ctx, `SELECT enabled,max_pending,max_pending_per_publisher FROM cairn.agent_worker_pool WHERE repo=$1 AND name=$2 FOR UPDATE`, req.Repo, req.Destination.Name).Scan(&enabled, &max, &per)
	if errors.Is(err, pgx.ErrNoRows) {
		return failure("NOT_FOUND", "pool not configured")
	}
	if err != nil {
		return err
	}
	if !enabled {
		return failure("POOL_PAUSED", "pool admission paused")
	}
	var total, own int
	err = tx.QueryRow(ctx, `SELECT count(*),count(*) FILTER(WHERE e.publisher=$3) FROM cairn.agent_pool_request q JOIN cairn.agent_event e USING(event_id) LEFT JOIN cairn.agent_delivery d USING(delivery_id) WHERE e.repo=$1 AND e.destination_name=$2 AND q.closed_at IS NULL AND (q.delivery_id IS NULL OR d.state IN ('pending','leased'))`, req.Repo, req.Destination.Name, s.channel.Principal).Scan(&total, &own)
	if err != nil {
		return err
	}
	if total >= max || own >= per {
		return failure("POOL_FULL", "pool outstanding-work limit reached")
	}
	_, err = tx.Exec(ctx, `INSERT INTO cairn.agent_pool_publisher_service(repo,pool,publisher) VALUES($1,$2,$3) ON CONFLICT DO NOTHING`, req.Repo, req.Destination.Name, s.channel.Principal)
	return err
}

type poolCandidate struct {
	id, pool, publisher string
	position            int64
}

func selectPoolCandidate(ctx context.Context, tx pgx.Tx, w WorkerSlot) (poolCandidate, error) {
	// Lock memberships in stable order: publication/configuration takes only one.
	pools := slices.Clone(w.Spec.Pools)
	slices.Sort(pools)
	enabled := []string{}
	for _, name := range pools {
		var on bool
		err := tx.QueryRow(ctx, `SELECT enabled FROM cairn.agent_worker_pool WHERE repo=$1 AND name=$2 FOR UPDATE`, w.Repo, name).Scan(&on)
		if errors.Is(err, pgx.ErrNoRows) {
			continue
		}
		if err != nil {
			return poolCandidate{}, err
		}
		if on {
			enabled = append(enabled, name)
		}
	}
	var c poolCandidate
	err := tx.QueryRow(ctx, `SELECT e.event_id::text,e.destination_name,e.publisher,e.position FROM cairn.agent_pool_request q JOIN cairn.agent_event e USING(event_id) JOIN cairn.agent_pool_publisher_service f ON f.repo=e.repo AND f.pool=e.destination_name AND f.publisher=e.publisher WHERE q.delivery_id IS NULL AND q.closed_at IS NULL AND `+requestAdmissionOpen+` AND e.repo=$1 AND e.destination_name=ANY($2::text[]) AND e.pool_requirements->>'workspace'=$3 AND COALESCE(e.pool_requirements->>'harness','') IN ('',$4) AND COALESCE(e.pool_requirements->>'model','') IN ('',$5) AND NOT EXISTS(SELECT 1 FROM jsonb_array_elements_text(COALESCE(e.pool_requirements->'capabilities','[]'::jsonb)) cap WHERE NOT (cap=ANY(COALESCE($6::text[],ARRAY[]::text[])))) ORDER BY f.last_dispatch,e.position LIMIT 1 FOR UPDATE OF q`, w.Repo, enabled, w.Spec.Workspace, w.Spec.Harness, w.Spec.Model, w.Spec.Capabilities).Scan(&c.id, &c.pool, &c.publisher, &c.position)
	if errors.Is(err, pgx.ErrNoRows) {
		return c, nil
	}
	return c, err
}
func assignPool(ctx context.Context, tx pgx.Tx, w WorkerSlot, c poolCandidate) (string, error) {
	id := uuid.NewString()
	if _, err := tx.Exec(ctx, `INSERT INTO cairn.agent_delivery(delivery_id,event_id,consumer) VALUES($1,$2,$3)`, id, c.id, w.Consumer); err != nil {
		return "", err
	}
	if _, err := tx.Exec(ctx, `UPDATE cairn.agent_pool_request SET delivery_id=$2,assigned_at=clock_timestamp() WHERE event_id=$1 AND delivery_id IS NULL`, c.id, id); err != nil {
		return "", err
	}
	_, err := tx.Exec(ctx, `UPDATE cairn.agent_pool_publisher_service SET last_dispatch=nextval('cairn.agent_pool_dispatch_sequence') WHERE repo=$1 AND pool=$2 AND publisher=$3`, w.Repo, c.pool, c.publisher)
	return id, err
}
func readPoolStatus(ctx context.Context, tx pgx.Tx, id string) (*PoolRequestStatus, error) {
	var p PoolRequestStatus
	err := tx.QueryRow(ctx, `SELECT CASE WHEN q.closed_at IS NOT NULL THEN 'failed' WHEN q.delivery_id IS NULL THEN 'queued' ELSE 'assigned' END,COALESCE(q.delivery_id::text,''),COALESCE(d.consumer,''),q.assigned_at,CASE WHEN q.closed_at IS NULL THEN NULL ELSE jsonb_build_object('at',q.closed_at,'by',q.closed_by,'code',q.closed_code) END FROM cairn.agent_pool_request q LEFT JOIN cairn.agent_delivery d USING(delivery_id) WHERE q.event_id=$1`, id).Scan(&p.State, &p.DeliveryID, &p.Consumer, &p.AssignedAt, &p.Control)
	return &p, err
}

type WorkerHeartbeatRequest struct {
	Repo         string `json:"repo,omitempty"`
	SupervisorID string `json:"supervisor_id"`
}
type WorkerHealthRequest struct {
	RequestID        string     `json:"request_id"`
	Repo             string     `json:"repo,omitempty"`
	SupervisorID     string     `json:"supervisor_id"`
	ExpectedRevision int64      `json:"expected_revision"`
	Health           string     `json:"health"`
	Reason           string     `json:"reason"`
	RetryAt          *time.Time `json:"retry_at,omitempty"`
}

func (s *Store) HeartbeatWorker(ctx context.Context, req WorkerHeartbeatRequest, dest Destination) (WorkerSlot, error) {
	if err := s.workerAccess(dest); err != nil {
		return WorkerSlot{}, err
	}
	if validID(req.SupervisorID) != nil {
		return WorkerSlot{}, failure("INVALID_REQUEST", "supervisor UUID required")
	}
	repo, err := s.eventScope(req.Repo, "")
	if err != nil {
		return WorkerSlot{}, err
	}
	tx, err := s.begin(ctx)
	if err != nil {
		return WorkerSlot{}, err
	}
	defer tx.Rollback(ctx)
	generation, err := retrievalGeneration(ctx, tx)
	if err != nil {
		return WorkerSlot{}, err
	}
	w, err := scanWorker(tx.QueryRow(ctx, `UPDATE cairn.agent_worker_slot SET heartbeat_at=clock_timestamp(),expires_at=clock_timestamp()+interval '90 seconds' WHERE repo=$1 AND consumer=$2 AND supervisor_id=$3 AND database_generation=$4 RETURNING `+workerColumns, repo, s.channel.Principal, req.SupervisorID, generation))
	if errors.Is(err, pgx.ErrNoRows) {
		return w, failure("STALE_WORKER", "supervisor replaced or fenced")
	}
	if err != nil {
		return w, err
	}
	return w, tx.Commit(ctx)
}
func (s *Store) ChangeWorkerHealth(ctx context.Context, req WorkerHealthRequest, dest Destination) (WorkerSlot, error) {
	if err := s.workerAccess(dest); err != nil {
		return WorkerSlot{}, err
	}
	if validID(req.SupervisorID) != nil || req.ExpectedRevision < 1 || (req.Health != "available" && req.Health != "paused" && req.Health != "unavailable") || strings.TrimSpace(req.Reason) == "" || len(req.Reason) > 1024 || strings.ContainsRune(req.Reason, 0) || (req.Health == "available" && req.RetryAt != nil) {
		return WorkerSlot{}, failure("INVALID_REQUEST", "health change requires supervisor, expected revision, state and bounded reason; recovery clears retry time")
	}
	var err error
	if req.Repo, err = s.eventScope(req.Repo, ""); err != nil {
		return WorkerSlot{}, err
	}
	return mutate(ctx, s, "worker-health", req.RequestID, req, func(tx pgx.Tx) (WorkerSlot, error) {
		if _, err := retrievalGeneration(ctx, tx); err != nil {
			return WorkerSlot{}, err
		}
		w, err := s.claimWorker(ctx, tx, req.Repo, req.SupervisorID)
		if err != nil {
			return WorkerSlot{}, err
		}
		if w.Revision != req.ExpectedRevision {
			return *w, failure("VERSION_CONFLICT", "worker revision changed")
		}
		return scanWorker(tx.QueryRow(ctx, `UPDATE cairn.agent_worker_slot SET health=$3,reason=$4,retry_at=$5,health_at=clock_timestamp(),revision=revision+1 WHERE repo=$1 AND consumer=$2 RETURNING `+workerColumns, req.Repo, s.channel.Principal, req.Health, req.Reason, req.RetryAt))
	})
}

type WorkerListRequest struct {
	Repo string `json:"repo,omitempty"`
}
type WorkerList struct {
	Workers []WorkerSlot `json:"workers"`
}

func (s *Store) WorkerSlots(ctx context.Context, req WorkerListRequest, dest Destination) (WorkerList, error) {
	out := WorkerList{Workers: []WorkerSlot{}}
	if err := validEventDestination(dest); err != nil {
		return out, err
	}
	repo, err := s.eventScope(req.Repo, "")
	if err != nil {
		return out, err
	}
	tx, err := s.begin(ctx)
	if err != nil {
		return out, err
	}
	defer tx.Rollback(ctx)
	rows, err := tx.Query(ctx, `SELECT `+workerColumns+` FROM cairn.agent_worker_slot WHERE repo=$1 ORDER BY consumer LIMIT 128`, repo)
	if err != nil {
		return out, err
	}
	defer rows.Close()
	for rows.Next() {
		w, err := scanWorker(rows)
		if err != nil {
			return out, err
		}
		out.Workers = append(out.Workers, w)
	}
	return out, rows.Err()
}

type WorkerPoolList struct {
	Pools []WorkerPool `json:"pools"`
}

func (s *Store) WorkerPools(ctx context.Context, req WorkerListRequest, dest Destination) (WorkerPoolList, error) {
	out := WorkerPoolList{Pools: []WorkerPool{}}
	if err := validEventDestination(dest); err != nil {
		return out, err
	}
	repo, err := s.eventScope(req.Repo, "")
	if err != nil {
		return out, err
	}
	tx, err := s.begin(ctx)
	if err != nil {
		return out, err
	}
	defer tx.Rollback(ctx)
	rows, err := tx.Query(ctx, `SELECT repo,name,enabled,max_pending,max_pending_per_publisher,revision FROM cairn.agent_worker_pool WHERE repo=$1 ORDER BY name LIMIT 128`, repo)
	if err != nil {
		return out, err
	}
	defer rows.Close()
	for rows.Next() {
		var p WorkerPool
		if err := rows.Scan(&p.Repo, &p.Name, &p.Enabled, &p.MaxPending, &p.MaxPendingPerPublisher, &p.Revision); err != nil {
			return out, err
		}
		out.Pools = append(out.Pools, p)
	}
	return out, rows.Err()
}
func (s *Store) checkWakeWorker(ctx context.Context, tx pgx.Tx, w WakeAttempt) error {
	if w.WorkerID == "" {
		return nil
	}
	worker, err := s.claimWorker(ctx, tx, w.Delivery.Event.Repo, w.WorkerID)
	if err != nil {
		return err
	}
	if worker.Health != "available" {
		return failure("WORKER_UNAVAILABLE", "worker admission paused")
	}
	return nil
}
