package semantic

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"math"
	"strconv"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

type EmbeddingIdentity struct {
	ModelSHA256 string `json:"model_sha256"`
	Algorithm   string `json:"algorithm"`
	Dimensions  int    `json:"dimensions"`
}
type EmbeddedPassage struct {
	Span   core.ByteSpanRequest `json:"span"`
	Vector []float32            `json:"vector"`
}

// Embedder is local trusted-host configuration, not an agent-supplied endpoint.
// Identity binds both query and document encoding for this owner's lifetime.
type Embedder interface {
	Identity(context.Context) (EmbeddingIdentity, error)
	Query(context.Context, string) ([]float32, error)
	Document(context.Context, string) ([]EmbeddedPassage, error)
}

type Index struct {
	pool     *pgxpool.Pool
	model    Embedder
	identity EmbeddingIdentity
}

func OpenIndex(ctx context.Context, dsn string, model Embedder) (*Index, error) {
	identity, err := model.Identity(ctx)
	if err != nil {
		return nil, fmt.Errorf("embedding identity: %w", err)
	}
	decoded, err := hex.DecodeString(identity.ModelSHA256)
	if err != nil || len(decoded) != 32 || identity.ModelSHA256 != strings.ToLower(identity.ModelSHA256) || identity.Dimensions != 384 || identity.Algorithm == "" || len(identity.Algorithm) > 128 {
		return nil, fmt.Errorf("invalid embedding identity")
	}
	pool, err := pgxpool.New(ctx, dsn)
	if err != nil {
		return nil, err
	}
	i := &Index{pool: pool, model: model, identity: identity}
	// The durable source set is the rebuild authority; opening after a model
	// change or restored snapshot schedules missing current derivations.
	_, err = pool.Exec(ctx, `INSERT INTO cairn.semantic_job(record_id)
 SELECT m.record_id FROM cairn.memory_record m JOIN cairn.record_version v ON v.record_id=m.record_id AND v.version=m.current_version
 LEFT JOIN cairn.semantic_document d ON d.record_id=m.record_id AND d.version=v.version AND d.model_sha256=$1
 WHERE m.lifecycle='active' AND v.payload_deleted_by IS NULL AND d.record_id IS NULL
 ON CONFLICT(record_id) DO NOTHING`, identity.ModelSHA256)
	if err != nil {
		pool.Close()
		return nil, err
	}
	return i, nil
}
func (i *Index) Close() { i.pool.Close() }

// Step leases one due source, embeds outside the transaction, then publishes
// only if the exact source and lease remain current. Interrupted work is due
// again after two minutes. An edit clears the lease and supersedes old work.
func (i *Index) Step(ctx context.Context) (bool, error) {
	lease := uuid.NewString()
	var id string
	err := i.pool.QueryRow(ctx, `UPDATE cairn.semantic_job SET lease_id=$1,lease_until=clock_timestamp()+interval '2 minutes'
 WHERE record_id=(SELECT record_id FROM cairn.semantic_job WHERE retry_at<=clock_timestamp() AND (lease_until IS NULL OR lease_until<clock_timestamp()) ORDER BY retry_at,record_id FOR UPDATE SKIP LOCKED LIMIT 1) RETURNING record_id::text`, lease).Scan(&id)
	if errors.Is(err, pgx.ErrNoRows) {
		return false, nil
	}
	if err != nil {
		return false, err
	}
	var version int
	var body string
	err = i.pool.QueryRow(ctx, `SELECT v.version,v.body FROM cairn.memory_record m JOIN cairn.record_version v ON v.record_id=m.record_id AND v.version=m.current_version WHERE m.record_id=$1 AND m.lifecycle='active' AND v.payload_deleted_by IS NULL`, id).Scan(&version, &body)
	if errors.Is(err, pgx.ErrNoRows) {
		_, err = i.pool.Exec(ctx, `DELETE FROM cairn.semantic_job WHERE record_id=$1 AND lease_id=$2`, id, lease)
		return true, err
	}
	if err != nil {
		return true, i.retry(ctx, id, lease, err)
	}
	passages, err := i.model.Document(ctx, body)
	if err != nil {
		return true, i.retry(ctx, id, lease, err)
	}
	if len(passages) == 0 || len(passages) > 256 {
		return true, i.retry(ctx, id, lease, fmt.Errorf("invalid passage count"))
	}
	vectors := make([]string, len(passages))
	for n, p := range passages {
		if p.Span.Offset < 0 || p.Span.Length <= 0 || p.Span.Offset > len(body) || p.Span.Length > len(body)-p.Span.Offset || !utf8.ValidString(body[p.Span.Offset:p.Span.Offset+p.Span.Length]) {
			return true, i.retry(ctx, id, lease, fmt.Errorf("invalid passage source span"))
		}
		vectors[n], err = vectorText(p.Vector)
		if err != nil {
			return true, i.retry(ctx, id, lease, err)
		}
	}
	tx, err := i.pool.Begin(ctx)
	if err != nil {
		return true, err
	}
	defer tx.Rollback(context.Background())
	var current int
	var active bool
	err = tx.QueryRow(ctx, `SELECT current_version,lifecycle='active' FROM cairn.memory_record WHERE record_id=$1 FOR UPDATE`, id).Scan(&current, &active)
	if errors.Is(err, pgx.ErrNoRows) {
		return true, nil
	}
	if err != nil {
		return true, err
	}
	var owned bool
	err = tx.QueryRow(ctx, `SELECT COALESCE(lease_id=$2 AND lease_until>clock_timestamp(),false) FROM cairn.semantic_job WHERE record_id=$1 FOR UPDATE`, id, lease).Scan(&owned)
	if errors.Is(err, pgx.ErrNoRows) {
		return true, nil
	}
	if err != nil {
		return true, err
	}
	if !owned || !active || current != version {
		return true, nil
	}
	var currentBody string
	err = tx.QueryRow(ctx, `SELECT body FROM cairn.record_version WHERE record_id=$1 AND version=$2 AND payload_deleted_by IS NULL`, id, version).Scan(&currentBody)
	if errors.Is(err, pgx.ErrNoRows) {
		return true, nil
	}
	if err != nil {
		return true, err
	}
	if currentBody != body {
		return true, fmt.Errorf("source changed without advancing its version")
	}
	if _, err = tx.Exec(ctx, `DELETE FROM cairn.semantic_document WHERE record_id=$1`, id); err != nil {
		return true, err
	}
	hash := sha256.Sum256([]byte(body))
	if _, err = tx.Exec(ctx, `INSERT INTO cairn.semantic_document(record_id,version,body_sha256,model_sha256) VALUES($1,$2,$3,$4)`, id, version, hex.EncodeToString(hash[:]), i.identity.ModelSHA256); err != nil {
		return true, err
	}
	for n, p := range passages {
		if _, err = tx.Exec(ctx, `INSERT INTO cairn.semantic_passage(record_id,ordinal,byte_offset,byte_length,embedding) VALUES($1,$2,$3,$4,$5::public.vector)`, id, n, p.Span.Offset, p.Span.Length, vectors[n]); err != nil {
			return true, err
		}
	}
	if _, err = tx.Exec(ctx, `DELETE FROM cairn.semantic_job WHERE record_id=$1 AND lease_id=$2`, id, lease); err != nil {
		return true, err
	}
	return true, tx.Commit(ctx)
}

func (i *Index) retry(ctx context.Context, id, lease string, cause error) error {
	// Retain only retry scheduling, never worker stderr or note bodies.
	_, err := i.pool.Exec(ctx, `UPDATE cairn.semantic_job SET attempts=attempts+1,retry_at=clock_timestamp()+make_interval(secs=>LEAST(300,5*(attempts+1))),lease_id=NULL,lease_until=NULL WHERE record_id=$1 AND lease_id=$2`, id, lease)
	if err != nil {
		return errors.Join(cause, fmt.Errorf("schedule embedding retry: %w", err))
	}
	return cause
}

func vectorText(vector []float32) (string, error) {
	if len(vector) != 384 {
		return "", fmt.Errorf("embedding must have 384 dimensions")
	}
	var b strings.Builder
	b.WriteByte('[')
	norm := float64(0)
	for n, v := range vector {
		if math.IsNaN(float64(v)) || math.IsInf(float64(v), 0) {
			return "", fmt.Errorf("embedding must be finite")
		}
		norm += float64(v) * float64(v)
		if n > 0 {
			b.WriteByte(',')
		}
		b.WriteString(strconv.FormatFloat(float64(v), 'g', -1, 32))
	}
	if norm == 0 {
		return "", fmt.Errorf("embedding must be nonzero")
	}
	b.WriteByte(']')
	return b.String(), nil
}

func (i *Index) Search(ctx context.Context, req core.SemanticRankRequest) (core.SemanticRetrievalResult, error) {
	result := core.SemanticRetrievalResult{ModelSHA256: i.identity.ModelSHA256, Algorithm: i.identity.Algorithm, Hits: []core.SemanticPassageHit{}}
	if len(req.Notes) > 10000 || strings.TrimSpace(req.Query) == "" || len(req.Query) > 4096 {
		return result, fmt.Errorf("indexed search exceeds input bounds")
	}
	var v []float32
	var err error
	if model, ok := i.model.(interface {
		QueryWithProjection(context.Context, string) ([]float32, *core.SemanticQueryProjection, error)
	}); ok {
		v, result.QueryProjection, err = model.QueryWithProjection(ctx, req.Query)
	} else {
		v, err = i.model.Query(ctx, req.Query)
	}
	if err == nil {
		err = core.ValidateSemanticQueryProjection(result.QueryProjection, req.Query)
	}
	if err != nil {
		return result, err
	}
	vector, err := vectorText(v)
	if err != nil {
		return result, err
	}
	// Source bodies never enter SQL parameters. Join the complete eligible set
	// before ordering, so private/out-of-scope near neighbours cannot starve it.
	refs := make([]core.SemanticScore, 0, len(req.Notes))
	for _, n := range req.Notes {
		refs = append(refs, core.SemanticScore{RecordID: n.RecordID, Version: n.Version, BodySHA256: n.BodySHA256})
	}
	rows, err := i.pool.Query(ctx, `WITH eligible AS MATERIALIZED (
 SELECT d.* FROM jsonb_to_recordset($1::jsonb) AS n(record_id uuid,version integer,body_sha256 text)
 JOIN cairn.semantic_document d ON d.record_id=n.record_id AND d.version=n.version AND d.body_sha256=n.body_sha256 AND d.model_sha256=$2
 JOIN cairn.memory_record m ON m.record_id=d.record_id AND m.current_version=d.version AND m.lifecycle='active'
 JOIN cairn.record_version v ON v.record_id=d.record_id AND v.version=d.version AND v.payload_deleted_by IS NULL
 ), best AS (
 SELECT DISTINCT ON(e.record_id) e.record_id,e.version,e.body_sha256,p.byte_offset,p.byte_length,(p.embedding <=> $3::public.vector) AS distance
 FROM eligible e JOIN cairn.semantic_passage p USING(record_id)
 ORDER BY e.record_id,distance,p.ordinal
 ), hits AS (SELECT * FROM best ORDER BY distance,record_id LIMIT 100)
 SELECT (SELECT count(*) FROM eligible),h.record_id::text,h.version,h.body_sha256,h.byte_offset,h.byte_length,h.distance
 FROM (SELECT 1) anchor LEFT JOIN hits h ON true ORDER BY h.distance,h.record_id`, refs, i.identity.ModelSHA256, vector)
	if err != nil {
		return result, err
	}
	defer rows.Close()
	for rows.Next() {
		var id, hash *string
		var version, offset, length *int
		var distance *float64
		if err = rows.Scan(&result.Indexed, &id, &version, &hash, &offset, &length, &distance); err != nil {
			return result, err
		}
		if id == nil {
			continue
		}
		if distance == nil || math.IsNaN(*distance) || math.IsInf(*distance, 0) {
			return result, fmt.Errorf("invalid stored similarity")
		}
		score := int(math.Round(max(-1, min(1, 1-*distance)) * 1000000))
		result.Hits = append(result.Hits, core.SemanticPassageHit{SemanticScore: core.SemanticScore{RecordID: *id, Version: *version, BodySHA256: *hash, Score: score}, Span: core.ByteSpanRequest{Offset: *offset, Length: *length}})
	}
	return result, rows.Err()
}

// Run owns background progress. The caller cancels and joins it before Close.
// Per-document failure has a persisted retry delay; store failures get a short
// backoff. onError reports operational failures without claiming index readiness.
func (i *Index) Run(ctx context.Context, onError func(error)) {
	for ctx.Err() == nil {
		worked, err := i.Step(ctx)
		if err != nil && ctx.Err() == nil && onError != nil {
			onError(err)
		}
		if worked && err == nil {
			continue
		}
		timer := time.NewTimer(time.Second)
		select {
		case <-ctx.Done():
			timer.Stop()
			return
		case <-timer.C:
		}
	}
}
