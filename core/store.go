package core

import (
	"bytes"
	"context"
	"crypto/sha256"
	_ "embed"
	"encoding/json"
	"errors"
	"fmt"
	"strings"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

//go:embed schema/001_initial.sql
var initialSchema string

type Store struct {
	pool    *pgxpool.Pool
	channel Channel
}

// Open must be called by trusted host code. Agents must never receive the DSN
// or choose the Channel. The local CLI uses OS identity and testimony only.
func Open(ctx context.Context, dsn string, channel Channel) (*Store, error) {
	if strings.TrimSpace(channel.Principal) == "" || len(channel.Principal) > 256 {
		return nil, failure("AUTHORITY_DENIED", "trusted channel identity required")
	}
	if dsn == "" {
		return nil, failure("INVALID_REQUEST", "explicit database DSN required")
	}
	pool, err := pgxpool.New(ctx, dsn)
	if err != nil {
		return nil, err
	}
	if err = pool.Ping(ctx); err != nil {
		pool.Close()
		return nil, err
	}
	return &Store{pool, channel}, nil
}
func (s *Store) Close() { s.pool.Close() }

// Migrate requires installer credentials and a dedicated experimental database.
// Migrations and their checksums commit together; concurrent installers serialize.
func (s *Store) Migrate(ctx context.Context) error {
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return err
	}
	defer tx.Rollback(context.Background())
	if _, err = tx.Exec(ctx, `SELECT pg_advisory_xact_lock(728190041)`); err != nil {
		return err
	}
	if _, err = tx.Exec(ctx, `CREATE TABLE IF NOT EXISTS public.cairn_migration (
        version integer PRIMARY KEY, digest bytea NOT NULL, applied_at timestamptz NOT NULL DEFAULT now())`); err != nil {
		return err
	}
	digest := sha256.Sum256([]byte(initialSchema))
	var existing []byte
	err = tx.QueryRow(ctx, `SELECT digest FROM public.cairn_migration WHERE version=1`).Scan(&existing)
	switch {
	case errors.Is(err, pgx.ErrNoRows):
		if _, err = tx.Exec(ctx, initialSchema); err != nil {
			return err
		}
		if _, err = tx.Exec(ctx, `INSERT INTO public.cairn_migration(version,digest) VALUES(1,$1)`, digest[:]); err != nil {
			return err
		}
	case err != nil:
		return err
	case !bytes.Equal(existing, digest[:]):
		return failure("SCHEMA_MISMATCH", "migration checksum differs")
	}
	var newest int
	if err = tx.QueryRow(ctx, `SELECT max(version) FROM public.cairn_migration`).Scan(&newest); err != nil {
		return err
	}
	if newest != 1 {
		return failure("SCHEMA_MISMATCH", "database is newer than this binary")
	}
	return tx.Commit(ctx)
}

func (s *Store) begin(ctx context.Context) (pgx.Tx, error) {
	tx, err := s.pool.Begin(ctx)
	if err != nil {
		return nil, err
	}
	witness := "testimony"
	if s.channel.Instrumented {
		witness = "instrumented"
	}
	_, err = tx.Exec(ctx, `SELECT set_config('cairn.caller',$1,true), set_config('cairn.witness',$2,true)`, s.channel.Principal, witness)
	if err != nil {
		tx.Rollback(context.Background())
		return nil, err
	}
	return tx, nil
}

// One request lock covers lookup, effect and stored response. A lost response can
// be retried without repeating the effect; a different intent cannot reuse a key.
func mutate[T any](ctx context.Context, s *Store, operation, requestID string, request any, apply func(pgx.Tx) (T, error)) (T, error) {
	var zero T
	if err := validID(requestID); err != nil {
		return zero, err
	}
	canonical, err := json.Marshal(request)
	if err != nil {
		return zero, err
	}
	digest := sha256.Sum256(canonical)
	tx, err := s.begin(ctx)
	if err != nil {
		return zero, err
	}
	defer tx.Rollback(context.Background())
	if err = lock(ctx, tx, "request:"+s.channel.Principal+":"+operation+":"+requestID); err != nil {
		return zero, err
	}
	var previousDigest, response []byte
	err = tx.QueryRow(ctx, `SELECT request_digest,response FROM cairn.mutation_request WHERE caller=$1 AND operation=$2 AND request_id=$3`, s.channel.Principal, operation, requestID).Scan(&previousDigest, &response)
	if err == nil {
		if !bytes.Equal(previousDigest, digest[:]) {
			return zero, failure("IDEMPOTENCY_CONFLICT", "request UUID already used with different content")
		}
		var original T
		if err = json.Unmarshal(response, &original); err != nil {
			return zero, err
		}
		return original, tx.Commit(ctx)
	}
	if !errors.Is(err, pgx.ErrNoRows) {
		return zero, err
	}
	applied, err := apply(tx)
	if err != nil {
		return zero, err
	}
	response, err = json.Marshal(applied)
	if err != nil {
		return zero, err
	}
	_, err = tx.Exec(ctx, `INSERT INTO cairn.mutation_request(caller,operation,request_id,request_digest,response) VALUES($1,$2,$3,$4,$5)`, s.channel.Principal, operation, requestID, digest[:], response)
	if err != nil {
		return zero, err
	}
	if err = tx.Commit(ctx); err != nil {
		return zero, err
	}
	return applied, nil
}

func lock(ctx context.Context, tx pgx.Tx, key string) error {
	_, err := tx.Exec(ctx, `SELECT pg_advisory_xact_lock(hashtextextended($1,0))`, key)
	return err
}

func (s *Store) Create(ctx context.Context, req CreateRequest) (Record, error) {
	if err := req.Draft.validate(); err != nil {
		return Record{}, err
	}
	return mutate(ctx, s, "create", req.RequestID, req, func(tx pgx.Tx) (Record, error) {
		if req.Draft.AttemptID != "" {
			if err := lock(ctx, tx, "attempt:"+req.Draft.AttemptID); err != nil {
				return Record{}, err
			}
		}
		id := uuid.NewString()
		if _, err := tx.Exec(ctx, `INSERT INTO cairn.memory_record(record_id,current_version) VALUES($1,1)`, id); err != nil {
			return Record{}, err
		}
		return insertVersion(ctx, tx, id, 1, req.Draft)
	})
}

func (s *Store) Edit(ctx context.Context, req EditRequest) (Record, error) {
	if err := validID(req.RecordID); err != nil {
		return Record{}, err
	}
	if err := req.Draft.validate(); err != nil {
		return Record{}, err
	}
	if req.ExpectedVersion < 1 {
		return Record{}, failure("INVALID_REQUEST", "expected_version must be positive")
	}
	return mutate(ctx, s, "edit", req.RequestID, req, func(tx pgx.Tx) (Record, error) {
		if req.Draft.AttemptID != "" {
			if err := lock(ctx, tx, "attempt:"+req.Draft.AttemptID); err != nil {
				return Record{}, err
			}
		}
		var version int
		err := tx.QueryRow(ctx, `SELECT current_version FROM cairn.memory_record WHERE record_id=$1 FOR UPDATE`, req.RecordID).Scan(&version)
		if errors.Is(err, pgx.ErrNoRows) {
			return Record{}, failure("NOT_FOUND", "record not found")
		}
		if err != nil {
			return Record{}, err
		}
		if version != req.ExpectedVersion {
			return Record{}, failure("VERSION_CONFLICT", fmt.Sprintf("current version is %d", version))
		}
		old, err := readRecord(ctx, tx, req.RecordID)
		if err != nil {
			return Record{}, err
		}
		if old.Scope != req.Draft.Scope {
			return Record{}, failure("AUTHORITY_DENIED", "scope changes require an authority path; exact scope is fixed in this slice")
		}
		if _, err = tx.Exec(ctx, `UPDATE cairn.memory_record SET current_version=current_version+1 WHERE record_id=$1 AND current_version=$2`, req.RecordID, req.ExpectedVersion); err != nil {
			return Record{}, err
		}
		return insertVersion(ctx, tx, req.RecordID, version+1, req.Draft)
	})
}

func insertVersion(ctx context.Context, tx pgx.Tx, id string, version int, draft Draft) (Record, error) {
	_, err := tx.Exec(ctx, `INSERT INTO cairn.record_version(record_id,version,kind,body,repo,task_id,run_id,attributed_producer,attempt_id,result_ref,claim_type)
        VALUES($1,$2,$3,$4,$5,$6,$7,$8,NULLIF($9,'')::uuid,$10,$11)`, id, version, draft.Kind, draft.Body, draft.Scope.Repo, draft.Scope.TaskID, draft.Scope.RunID, draft.AttributedProducer, draft.AttemptID, draft.ResultRef, draft.ClaimType)
	if err != nil {
		return Record{}, err
	}
	if err = queueContradictions(ctx, tx, draft.AttemptID); err != nil {
		return Record{}, err
	}
	return readRecord(ctx, tx, id)
}

func queueContradictions(ctx context.Context, tx pgx.Tx, attemptID string) error {
	if attemptID == "" {
		return nil
	}
	_, err := tx.Exec(ctx, `INSERT INTO cairn.correction_docket(record_id,version,reason)
        SELECT record_id,version,'ATTRIBUTION_CONTRADICTED' FROM cairn.version_attribution
        WHERE attempt_id=$1 AND attribution_state='contradicted' ON CONFLICT DO NOTHING`, attemptID)
	return err
}

func readRecord(ctx context.Context, tx pgx.Tx, id string) (Record, error) {
	var r Record
	err := tx.QueryRow(ctx, `SELECT v.record_id::text,v.version,m.class,m.lifecycle,m.sensitivity,
        v.kind,v.body,v.repo,v.task_id,v.run_id,v.attributed_producer,COALESCE(v.attempt_id::text,''),v.result_ref,v.claim_type,
        v.observed_writer,v.witness,v.written_at,v.attribution_state
        FROM cairn.memory_record m JOIN cairn.version_attribution v ON v.record_id=m.record_id AND v.version=m.current_version
        WHERE m.record_id=$1`, id).Scan(&r.RecordID, &r.Version, &r.Class, &r.Lifecycle, &r.Sensitivity, &r.Kind, &r.Body, &r.Scope.Repo, &r.Scope.TaskID, &r.Scope.RunID, &r.AttributedProducer, &r.AttemptID, &r.ResultRef, &r.ClaimType, &r.ObservedWriter, &r.Witness, &r.WrittenAt, &r.AttributionState)
	if errors.Is(err, pgx.ErrNoRows) {
		return r, failure("NOT_FOUND", "record not found")
	}
	return r, err
}

// Get is a local advisory inspection only. It does not grant destination access
// or provide inputs for planning, placement, capability or security decisions.
func (s *Store) Get(ctx context.Context, id string) (Record, error) {
	if err := validID(id); err != nil {
		return Record{}, err
	}
	tx, err := s.begin(ctx)
	if err != nil {
		return Record{}, err
	}
	defer tx.Rollback(context.Background())
	record, err := readRecord(ctx, tx, id)
	if err != nil {
		return Record{}, err
	}
	return record, tx.Commit(ctx)
}
