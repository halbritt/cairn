package core

import (
	"context"
	"errors"
	"time"

	"github.com/jackc/pgx/v5"
)

// RecordHistoryRequest lists retained version metadata, or reads one exact body.
// Version and paging are separate modes; BeforeVersion is an exclusive cursor.
type RecordHistoryRequest struct {
	RecordID      string           `json:"record_id"`
	Repo          string           `json:"repo,omitempty"`
	Version       int              `json:"version,omitempty"`
	BeforeVersion int              `json:"before_version,omitempty"`
	Limit         int              `json:"limit,omitempty"`
	Span          *ByteSpanRequest `json:"span,omitempty"`
}

type HistoricalVersion struct {
	Entities         []EntityRef `json:"entities,omitempty"`
	Version          int         `json:"version"`
	Class            string      `json:"class"`
	Kind             string      `json:"kind"`
	Scope            Scope       `json:"scope"`
	ObservedWriter   string      `json:"observed_writer"`
	Witness          string      `json:"witness"`
	WrittenAt        time.Time   `json:"written_at"`
	PayloadAvailable bool        `json:"payload_available"`
	BodySHA256       string      `json:"body_sha256,omitempty"`
	BodyBytes        int         `json:"body_bytes,omitempty"`
	Body             *string     `json:"body,omitempty"`
	Span             *ByteSpan   `json:"span,omitempty"`
}

type RecordHistory struct {
	Historical        bool                `json:"historical"`
	RecordID          string              `json:"record_id"`
	CurrentVersion    int                 `json:"current_version"`
	CurrentClass      string              `json:"current_class"`
	CurrentLifecycle  string              `json:"current_lifecycle"`
	Versions          []HistoricalVersion `json:"versions"`
	More              bool                `json:"more"`
	NextBeforeVersion *int                `json:"next_before_version,omitempty"`
}

// History is destination-bound inspection, not current compiler selection or
// authority. It reads retained versions without creating another stored copy.
func (s *Store) History(ctx context.Context, req RecordHistoryRequest, dest Destination) (RecordHistory, error) {
	if err := validID(req.RecordID); err != nil {
		return RecordHistory{}, err
	}
	if (dest.Name != "local" && dest.Name != "hosted") || (dest.Name == "hosted" && dest.AllowLocal) {
		return RecordHistory{}, failure("INVALID_REQUEST", "invalid history destination")
	}
	if req.Version < 0 || req.Version > 2147483647 || req.BeforeVersion < 0 || req.BeforeVersion > 2147483647 || req.Limit < 0 || req.Limit > 100 || (req.Version > 0 && (req.BeforeVersion != 0 || req.Limit != 0)) {
		return RecordHistory{}, failure("INVALID_REQUEST", "history requires a positive exact version or metadata paging with limit 1-100 and a positive before_version")
	}
	limit := req.Limit
	if req.Span != nil && (req.Version == 0 || req.Span.Offset < 0 || req.Span.Offset >= 65536 || req.Span.Length < 1 || req.Span.Length > 65536) {
		return RecordHistory{}, failure("INVALID_REQUEST", "history span requires an exact version, offset 0-65535 and length 1-65536")
	}
	if limit == 0 {
		limit = 20
	}
	if req.Version > 0 {
		limit = 1
	}
	tx, err := s.beginLevel(ctx, pgx.RepeatableRead)
	if err != nil {
		return RecordHistory{}, err
	}
	defer tx.Rollback(ctx)
	// Hold the source identity against edits/forgetting until this read completes.
	var id string
	if err = tx.QueryRow(ctx, `SELECT record_id::text FROM cairn.memory_record WHERE record_id=$1 FOR SHARE`, req.RecordID).Scan(&id); errors.Is(err, pgx.ErrNoRows) {
		return RecordHistory{}, failure("NOT_FOUND", "record not found")
	} else if err != nil {
		return RecordHistory{}, err
	}
	current, err := readRecord(ctx, tx, id)
	if err != nil {
		return RecordHistory{}, err
	}
	if err = s.checkRepo(current.Scope.Repo); err != nil {
		return RecordHistory{}, err
	}
	if req.Repo != "" && current.Scope.Repo != req.Repo {
		return RecordHistory{}, failure("AUTHORITY_DENIED", "record is outside the requested repository")
	}
	if !dest.AllowLocal && current.Sensitivity != "shareable" {
		return RecordHistory{}, failure("NOT_FOUND", "record not found")
	}
	if current.Lifecycle == "tombstoned" {
		return RecordHistory{}, failure("PAYLOAD_UNAVAILABLE", "record was forgotten")
	}
	rows, err := tx.Query(ctx, `SELECT version,version_class,kind,repo,task_id,run_id,observed_writer,witness,written_at,
 payload_deleted_by IS NULL,
 CASE WHEN payload_deleted_by IS NULL THEN encode(sha256(convert_to(body,'UTF8')),'hex') ELSE '' END,
 CASE WHEN payload_deleted_by IS NULL THEN octet_length(body) ELSE 0 END,
 CASE WHEN $2>0 AND payload_deleted_by IS NULL THEN body ELSE NULL END
 FROM cairn.record_version WHERE record_id=$1 AND ($2=0 OR version=$2) AND ($3=0 OR version<$3)
 ORDER BY version DESC LIMIT $4`, id, req.Version, req.BeforeVersion, limit+1)
	if err != nil {
		return RecordHistory{}, err
	}
	defer rows.Close()
	result := RecordHistory{Historical: true, RecordID: id, CurrentVersion: current.Version, CurrentClass: current.Class, CurrentLifecycle: current.Lifecycle, Versions: []HistoricalVersion{}}
	for rows.Next() {
		var v HistoricalVersion
		if err = rows.Scan(&v.Version, &v.Class, &v.Kind, &v.Scope.Repo, &v.Scope.TaskID, &v.Scope.RunID, &v.ObservedWriter, &v.Witness, &v.WrittenAt, &v.PayloadAvailable, &v.BodySHA256, &v.BodyBytes, &v.Body); err != nil {
			return RecordHistory{}, err
		}
		if v.Scope.Repo != current.Scope.Repo {
			return RecordHistory{}, failure("AUTHORITY_DENIED", "historical version is outside the current repository")
		}
		if req.Version > 0 && !v.PayloadAvailable {
			return RecordHistory{}, failure("PAYLOAD_UNAVAILABLE", "historical version payload was excluded by deletion")
		}
		if len(result.Versions) == limit {
			result.More = true
			before := result.Versions[len(result.Versions)-1].Version
			result.NextBeforeVersion = &before
			break
		}
		result.Versions = append(result.Versions, v)
	}
	if err = rows.Err(); err != nil {
		return RecordHistory{}, err
	}
	rows.Close()
	if req.Version > 0 && len(result.Versions) == 0 {
		return RecordHistory{}, failure("NOT_FOUND", "retained record version not found")
	}
	if req.Version > 0 {
		if req.Span != nil {
			v := &result.Versions[0]
			if req.Span.Offset >= v.BodyBytes {
				return RecordHistory{}, failure("INVALID_REQUEST", "history span offset must be before the end of the retained body")
			}
			span := selectByteSpan([]byte(*v.Body), *req.Span)
			v.Span = &span
			v.Body = nil
		}
		result.Versions[0].Entities, err = readEntities(ctx, tx, id, req.Version)
		if err != nil {
			return RecordHistory{}, err
		}
	}
	if err = tx.Commit(ctx); err != nil {
		return RecordHistory{}, err
	}
	return result, nil
}
