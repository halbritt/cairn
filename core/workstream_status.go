package core

import (
	"context"
	"errors"
	"regexp"
	"slices"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/jackc/pgx/v5"
)

const (
	MaxWorkstreamEntryBytes  = 2048
	maxWorkstreamShortField  = 512
	maxWorkstreamDetailField = 1024
)

var workstreamNamePattern = regexp.MustCompile(`^[A-Za-z0-9_.-]{1,64}$`)

type WorkstreamStatus struct {
	Repo              string     `json:"repo"`
	Stream            string     `json:"stream"`
	Owner             string     `json:"owner"`
	AuthorizedWriters []string   `json:"authorized_writers,omitempty"`
	Writer            string     `json:"writer"`
	Revision          int64      `json:"revision"`
	State             string     `json:"state"`
	Current           string     `json:"current,omitempty"`
	LastLanded        string     `json:"last_landed,omitempty"`
	NextCheckpoint    *time.Time `json:"next_checkpoint,omitempty"`
	Blocker           string     `json:"blocker,omitempty"`
	DecisionNeeded    string     `json:"decision_needed,omitempty"`
	Detail            string     `json:"detail,omitempty"`
	Sensitivity       string     `json:"sensitivity"`
	EntryBytes        int        `json:"entry_bytes"`
	CreatedAt         time.Time  `json:"created_at"`
	UpdatedAt         time.Time  `json:"updated_at"`
}

type SetWorkstreamStatusRequest struct {
	RequestID        string     `json:"request_id"`
	Repo             string     `json:"repo,omitempty"`
	Stream           string     `json:"stream"`
	State            string     `json:"state"`
	Current          string     `json:"current,omitempty"`
	LastLanded       string     `json:"last_landed,omitempty"`
	NextCheckpoint   *time.Time `json:"next_checkpoint,omitempty"`
	Blocker          string     `json:"blocker,omitempty"`
	DecisionNeeded   string     `json:"decision_needed,omitempty"`
	Detail           string     `json:"detail,omitempty"`
	Sensitivity      string     `json:"sensitivity,omitempty"`
	Owner            string     `json:"owner,omitempty"`
	ExpectedRevision int64      `json:"expected_revision,omitempty"`
}

type AuthorizeWorkstreamStatusRequest struct {
	RequestID     string `json:"request_id"`
	Repo          string `json:"repo,omitempty"`
	Stream        string `json:"stream"`
	Writer        string `json:"writer,omitempty"`
	Revoke        bool   `json:"revoke,omitempty"`
	TransferOwner string `json:"transfer_owner,omitempty"`
}

type GetWorkstreamStatusRequest struct {
	Repo   string `json:"repo,omitempty"`
	Stream string `json:"stream,omitempty"`
	State  string `json:"state,omitempty"`
	After  string `json:"after,omitempty"`
	Limit  int    `json:"limit,omitempty"`
}

type WorkstreamBoard struct {
	Repo           string             `json:"repo"`
	Streams        []WorkstreamStatus `json:"streams"`
	MaxStreamBytes int                `json:"max_stream_bytes"`
	NextAfter      string             `json:"next_after,omitempty"`
	More           bool               `json:"more"`
}

type WorkstreamStatusHistoryRequest struct {
	Repo           string `json:"repo,omitempty"`
	Stream         string `json:"stream"`
	BeforeRevision int64  `json:"before_revision,omitempty"`
	Limit          int    `json:"limit,omitempty"`
}

type WorkstreamStatusHistoryEntry struct {
	Repo           string     `json:"repo"`
	Stream         string     `json:"stream"`
	Revision       int64      `json:"revision"`
	Owner          string     `json:"owner"`
	Writer         string     `json:"writer"`
	State          string     `json:"state"`
	Current        string     `json:"current,omitempty"`
	LastLanded     string     `json:"last_landed,omitempty"`
	NextCheckpoint *time.Time `json:"next_checkpoint,omitempty"`
	Blocker        string     `json:"blocker,omitempty"`
	DecisionNeeded string     `json:"decision_needed,omitempty"`
	Detail         string     `json:"detail,omitempty"`
	Sensitivity    string     `json:"sensitivity"`
	EntryBytes     int        `json:"entry_bytes"`
	WrittenAt      time.Time  `json:"written_at"`
}

type WorkstreamStatusHistoryPage struct {
	Repo               string                         `json:"repo"`
	Stream             string                         `json:"stream"`
	Entries            []WorkstreamStatusHistoryEntry `json:"entries"`
	NextBeforeRevision int64                          `json:"next_before_revision,omitempty"`
	More               bool                           `json:"more"`
}

func validWorkstreamState(state string) bool {
	switch state {
	case "active", "waiting", "blocked", "done":
		return true
	default:
		return false
	}
}

func validWorkstreamPrincipal(p string) bool {
	return p != "" && len(p) <= 256 && strings.TrimSpace(p) == p && utf8.ValidString(p) && !strings.ContainsAny(p, "\x00\r\n\t ")
}

func validateWorkstreamField(v string, maxBytes int, name string) error {
	if !utf8.ValidString(v) || strings.ContainsRune(v, 0) {
		return failure("INVALID_REQUEST", "invalid UTF-8 text in "+name)
	}
	if len(v) > maxBytes {
		return failure("BUDGET_REFUSED", name+" exceeds per-field byte limit")
	}
	return nil
}

const workstreamStatusColumns = `repo, stream, owner, authorized_writers, writer, revision, state, current_item, last_landed, next_checkpoint, blocker, decision_needed, detail, sensitivity, entry_bytes, created_at, updated_at`

func scanWorkstreamStatus(row pgx.Row, caller string, isOperator bool) (WorkstreamStatus, error) {
	var st WorkstreamStatus
	var writers []string
	err := row.Scan(&st.Repo, &st.Stream, &st.Owner, &writers, &st.Writer, &st.Revision, &st.State, &st.Current, &st.LastLanded, &st.NextCheckpoint, &st.Blocker, &st.DecisionNeeded, &st.Detail, &st.Sensitivity, &st.EntryBytes, &st.CreatedAt, &st.UpdatedAt)
	if err != nil {
		return st, err
	}
	if len(writers) > 0 && (isOperator || caller == st.Owner) {
		st.AuthorizedWriters = writers
	}
	return st, nil
}

func (s *Store) SetWorkstreamStatus(ctx context.Context, req SetWorkstreamStatusRequest, dest Destination) (WorkstreamStatus, error) {
	var empty WorkstreamStatus
	if err := validEventDestination(dest); err != nil {
		return empty, err
	}
	if validID(req.RequestID) != nil {
		return empty, failure("INVALID_REQUEST", "stable request UUID required")
	}
	repo, err := s.eventScope(req.Repo, "")
	if err != nil {
		return empty, err
	}
	req.Repo = repo
	if !workstreamNamePattern.MatchString(req.Stream) {
		return empty, failure("INVALID_REQUEST", "stream must be 1..64 ASCII letters, digits or _.-")
	}
	if !validWorkstreamState(req.State) {
		return empty, failure("INVALID_REQUEST", "state must be active, waiting, blocked or done")
	}
	if req.ExpectedRevision < 0 {
		return empty, failure("INVALID_REQUEST", "expected_revision must be nonnegative")
	}
	if req.Owner != "" && !validWorkstreamPrincipal(req.Owner) {
		return empty, failure("INVALID_REQUEST", "invalid owner principal")
	}
	if req.NextCheckpoint != nil && (req.NextCheckpoint.IsZero() || req.NextCheckpoint.Year() < 1 || req.NextCheckpoint.Year() > 9999) {
		return empty, failure("INVALID_REQUEST", "next_checkpoint must be a valid timestamp")
	}
	if req.Sensitivity != "" && req.Sensitivity != "shareable" && req.Sensitivity != "local" {
		return empty, failure("INVALID_REQUEST", "sensitivity must be shareable or local")
	}
	if req.Sensitivity == "local" && !dest.AllowLocal {
		return empty, failure("DESTINATION_PROHIBITED", "hosted destination cannot write local workstream status")
	}
	for _, check := range []struct {
		v   string
		max int
		n   string
	}{
		{req.Current, maxWorkstreamShortField, "current"},
		{req.LastLanded, maxWorkstreamShortField, "last_landed"},
		{req.Blocker, maxWorkstreamShortField, "blocker"},
		{req.DecisionNeeded, maxWorkstreamShortField, "decision_needed"},
		{req.Detail, maxWorkstreamDetailField, "detail"},
	} {
		if err := validateWorkstreamField(check.v, check.max, check.n); err != nil {
			return empty, err
		}
	}
	entryBytes := len(req.Stream) + len(req.State) + len(req.Current) + len(req.LastLanded) + len(req.Blocker) + len(req.DecisionNeeded) + len(req.Detail)
	if entryBytes > MaxWorkstreamEntryBytes {
		return empty, failure("BUDGET_REFUSED", "workstream entry exceeds 2048-byte per-stream budget")
	}
	input := struct {
		SetWorkstreamStatusRequest
		Destination string `json:"destination"`
		AllowLocal  bool   `json:"allow_local"`
	}{req, dest.Name, dest.AllowLocal}
	return s.applySetWorkstreamStatus(ctx, req, input, entryBytes, dest)
}

func (s *Store) applySetWorkstreamStatus(ctx context.Context, req SetWorkstreamStatusRequest, input any, entryBytes int, dest Destination) (WorkstreamStatus, error) {
	var empty WorkstreamStatus
	guard := func(tx pgx.Tx) error {
		if err := lock(ctx, tx, "workstream-status:"+req.Repo+":"+req.Stream); err != nil {
			return err
		}
		var curOwner, curSensitivity string
		var curWriters []string
		err := tx.QueryRow(ctx, `SELECT owner, authorized_writers, sensitivity FROM cairn.workstream_status WHERE repo=$1 AND stream=$2`, req.Repo, req.Stream).
			Scan(&curOwner, &curWriters, &curSensitivity)
		if errors.Is(err, pgx.ErrNoRows) {
			return nil
		}
		if err != nil {
			return err
		}
		if curSensitivity == "local" && !dest.AllowLocal {
			caller := s.channel.Principal
			if !s.channel.Operator && caller != curOwner && !slices.Contains(curWriters, caller) {
				return failure("AUTHORITY_DENIED", "caller is not authorized to update this workstream")
			}
			return failure("AUTHORITY_DENIED", "local workstream cannot be updated from a hosted destination")
		}
		return nil
	}
	return mutate(ctx, s, "status-set", req.RequestID, input, func(tx pgx.Tx) (WorkstreamStatus, error) {
		if _, err := retrievalGeneration(ctx, tx); err != nil {
			return empty, err
		}
		if err := lock(ctx, tx, "workstream-status:"+req.Repo+":"+req.Stream); err != nil {
			return empty, err
		}
		caller := s.channel.Principal
		var curOwner, curSensitivity string
		var curWriters []string
		var curRevision int64
		err := tx.QueryRow(ctx, `SELECT owner, authorized_writers, revision, sensitivity FROM cairn.workstream_status WHERE repo=$1 AND stream=$2 FOR UPDATE`, req.Repo, req.Stream).
			Scan(&curOwner, &curWriters, &curRevision, &curSensitivity)
		if errors.Is(err, pgx.ErrNoRows) {
			if req.ExpectedRevision > 0 {
				return empty, failure("VERSION_CONFLICT", "workstream does not exist yet")
			}
			owner := caller
			if req.Owner != "" {
				if req.Owner != caller && !s.channel.Operator {
					return empty, failure("AUTHORITY_DENIED", "only an operator may assign initial ownership to another principal")
				}
				owner = req.Owner
			}
			insertSens := req.Sensitivity
			if insertSens == "" {
				insertSens = "shareable"
			}
			st, err := scanWorkstreamStatus(tx.QueryRow(ctx, `INSERT INTO cairn.workstream_status(repo, stream, owner, writer, revision, state, current_item, last_landed, next_checkpoint, blocker, decision_needed, detail, sensitivity, entry_bytes, created_at, updated_at) VALUES($1,$2,$3,$4,1,$5,$6,$7,$8,$9,$10,$11,$12,$13,clock_timestamp(),clock_timestamp()) RETURNING `+workstreamStatusColumns,
				req.Repo, req.Stream, owner, caller, req.State, req.Current, req.LastLanded, req.NextCheckpoint, req.Blocker, req.DecisionNeeded, req.Detail, insertSens, entryBytes), caller, s.channel.Operator)
			if err != nil {
				return empty, err
			}
			_, err = tx.Exec(ctx, `INSERT INTO cairn.workstream_status_history(repo, stream, revision, owner, writer, state, current_item, last_landed, next_checkpoint, blocker, decision_needed, detail, sensitivity, entry_bytes, written_at) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15)`,
				st.Repo, st.Stream, st.Revision, st.Owner, st.Writer, st.State, st.Current, st.LastLanded, st.NextCheckpoint, st.Blocker, st.DecisionNeeded, st.Detail, st.Sensitivity, st.EntryBytes, st.UpdatedAt)
			return st, err
		}
		if err != nil {
			return empty, err
		}
		isOwnerOrOperator := s.channel.Operator || caller == curOwner
		if !isOwnerOrOperator && !slices.Contains(curWriters, caller) {
			return empty, failure("AUTHORITY_DENIED", "caller is not authorized to update this workstream")
		}
		if curSensitivity == "local" && !dest.AllowLocal {
			return empty, failure("AUTHORITY_DENIED", "local workstream cannot be updated from a hosted destination")
		}
		effectiveSens := curSensitivity
		if req.Sensitivity != "" {
			effectiveSens = req.Sensitivity
		}
		if curSensitivity == "shareable" && effectiveSens == "local" {
			return empty, failure("DESTINATION_PROHIBITED", "cannot downgrade a shareable workstream to local sensitivity")
		}
		if req.ExpectedRevision > 0 && req.ExpectedRevision != curRevision {
			return empty, failure("VERSION_CONFLICT", "workstream revision changed")
		}
		newOwner := curOwner
		if req.Owner != "" && req.Owner != curOwner {
			if !isOwnerOrOperator {
				return empty, failure("AUTHORITY_DENIED", "only the workstream owner or operator may transfer ownership")
			}
			newOwner = req.Owner
		}
		nextRev := curRevision + 1
		st, err := scanWorkstreamStatus(tx.QueryRow(ctx, `UPDATE cairn.workstream_status SET owner=$3, writer=$4, revision=$5, state=$6, current_item=$7, last_landed=$8, next_checkpoint=$9, blocker=$10, decision_needed=$11, detail=$12, sensitivity=$13, entry_bytes=$14, updated_at=clock_timestamp() WHERE repo=$1 AND stream=$2 RETURNING `+workstreamStatusColumns,
			req.Repo, req.Stream, newOwner, caller, nextRev, req.State, req.Current, req.LastLanded, req.NextCheckpoint, req.Blocker, req.DecisionNeeded, req.Detail, effectiveSens, entryBytes), caller, s.channel.Operator)
		if err != nil {
			return empty, err
		}
		_, err = tx.Exec(ctx, `INSERT INTO cairn.workstream_status_history(repo, stream, revision, owner, writer, state, current_item, last_landed, next_checkpoint, blocker, decision_needed, detail, sensitivity, entry_bytes, written_at) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15)`,
			st.Repo, st.Stream, st.Revision, st.Owner, st.Writer, st.State, st.Current, st.LastLanded, st.NextCheckpoint, st.Blocker, st.DecisionNeeded, st.Detail, st.Sensitivity, st.EntryBytes, st.UpdatedAt)
		return st, err
	}, guard)
}

func (s *Store) AuthorizeWorkstreamStatus(ctx context.Context, req AuthorizeWorkstreamStatusRequest, dest Destination) (WorkstreamStatus, error) {
	var empty WorkstreamStatus
	if err := validEventDestination(dest); err != nil {
		return empty, err
	}
	if validID(req.RequestID) != nil {
		return empty, failure("INVALID_REQUEST", "stable request UUID required")
	}
	repo, err := s.eventScope(req.Repo, "")
	if err != nil {
		return empty, err
	}
	req.Repo = repo
	if !workstreamNamePattern.MatchString(req.Stream) {
		return empty, failure("INVALID_REQUEST", "stream must be 1..64 ASCII letters, digits or _.-")
	}
	if req.Writer == "" && req.TransferOwner == "" {
		return empty, failure("INVALID_REQUEST", "supply writer and/or transfer_owner")
	}
	if req.Revoke && req.Writer == "" {
		return empty, failure("INVALID_REQUEST", "revoke requires writer")
	}
	if (req.Writer != "" && !validWorkstreamPrincipal(req.Writer)) || (req.TransferOwner != "" && !validWorkstreamPrincipal(req.TransferOwner)) {
		return empty, failure("INVALID_REQUEST", "invalid principal")
	}
	input := struct {
		AuthorizeWorkstreamStatusRequest
		Destination string `json:"destination"`
		AllowLocal  bool   `json:"allow_local"`
	}{req, dest.Name, dest.AllowLocal}
	guard := func(tx pgx.Tx) error {
		if err := lock(ctx, tx, "workstream-status:"+req.Repo+":"+req.Stream); err != nil {
			return err
		}
		var curOwner, curSensitivity string
		err := tx.QueryRow(ctx, `SELECT owner, sensitivity FROM cairn.workstream_status WHERE repo=$1 AND stream=$2`, req.Repo, req.Stream).
			Scan(&curOwner, &curSensitivity)
		if errors.Is(err, pgx.ErrNoRows) || (curSensitivity == "local" && !dest.AllowLocal) {
			return failure("NOT_FOUND", "workstream status not found")
		}
		if err != nil {
			return err
		}
		if !s.channel.Operator && s.channel.Principal != curOwner && req.TransferOwner == "" {
			return failure("AUTHORITY_DENIED", "only the workstream owner or operator may change permissions")
		}
		return nil
	}
	return mutate(ctx, s, "status-authorize", req.RequestID, input, func(tx pgx.Tx) (WorkstreamStatus, error) {
		if _, err := retrievalGeneration(ctx, tx); err != nil {
			return empty, err
		}
		if err := lock(ctx, tx, "workstream-status:"+req.Repo+":"+req.Stream); err != nil {
			return empty, err
		}
		caller := s.channel.Principal
		var curOwner, curSensitivity string
		var writers []string
		var curRevision int64
		err := tx.QueryRow(ctx, `SELECT owner, authorized_writers, revision, sensitivity FROM cairn.workstream_status WHERE repo=$1 AND stream=$2 FOR UPDATE`, req.Repo, req.Stream).
			Scan(&curOwner, &writers, &curRevision, &curSensitivity)
		if errors.Is(err, pgx.ErrNoRows) || (curSensitivity == "local" && !dest.AllowLocal) {
			return empty, failure("NOT_FOUND", "workstream status not found")
		}
		if err != nil {
			return empty, err
		}
		if !s.channel.Operator && caller != curOwner {
			return empty, failure("AUTHORITY_DENIED", "only the workstream owner or operator may change permissions")
		}
		nextWriters := slices.Clone(writers)
		if req.Writer != "" {
			if req.Revoke {
				nextWriters = slices.DeleteFunc(nextWriters, func(w string) bool { return w == req.Writer })
			} else if !slices.Contains(nextWriters, req.Writer) {
				if len(nextWriters) >= 16 {
					return empty, failure("BUDGET_REFUSED", "at most 16 authorized writers per workstream")
				}
				nextWriters = append(nextWriters, req.Writer)
				slices.Sort(nextWriters)
			}
		}
		newOwner := curOwner
		if req.TransferOwner != "" {
			newOwner = req.TransferOwner
		}
		nextRev := curRevision + 1
		st, err := scanWorkstreamStatus(tx.QueryRow(ctx, `UPDATE cairn.workstream_status SET owner=$3, authorized_writers=$4, writer=$5, revision=$6, updated_at=clock_timestamp() WHERE repo=$1 AND stream=$2 RETURNING `+workstreamStatusColumns,
			req.Repo, req.Stream, newOwner, nextWriters, caller, nextRev), caller, s.channel.Operator)
		if err != nil {
			return empty, err
		}
		_, err = tx.Exec(ctx, `INSERT INTO cairn.workstream_status_history(repo, stream, revision, owner, writer, state, current_item, last_landed, next_checkpoint, blocker, decision_needed, detail, sensitivity, entry_bytes, written_at) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15)`,
			st.Repo, st.Stream, st.Revision, st.Owner, st.Writer, st.State, st.Current, st.LastLanded, st.NextCheckpoint, st.Blocker, st.DecisionNeeded, st.Detail, st.Sensitivity, st.EntryBytes, st.UpdatedAt)
		return st, err
	}, guard)
}

func (s *Store) GetWorkstreamStatus(ctx context.Context, req GetWorkstreamStatusRequest, dest Destination) (WorkstreamBoard, error) {
	out := WorkstreamBoard{Streams: []WorkstreamStatus{}, MaxStreamBytes: MaxWorkstreamEntryBytes}
	if err := validEventDestination(dest); err != nil {
		return out, err
	}
	repo, err := s.eventScope(req.Repo, "")
	if err != nil {
		return out, err
	}
	out.Repo = repo
	if req.Stream != "" && !workstreamNamePattern.MatchString(req.Stream) {
		return out, failure("INVALID_REQUEST", "invalid stream name")
	}
	if req.State != "" && !validWorkstreamState(req.State) {
		return out, failure("INVALID_REQUEST", "state must be active, waiting, blocked or done")
	}
	if req.After != "" && !workstreamNamePattern.MatchString(req.After) {
		return out, failure("INVALID_REQUEST", "invalid after cursor")
	}
	limit := req.Limit
	if limit < 0 || limit > 200 {
		return out, failure("INVALID_REQUEST", "limit must be 0..200")
	}
	if limit == 0 {
		limit = 100
	}
	tx, err := s.begin(ctx)
	if err != nil {
		return out, err
	}
	defer tx.Rollback(ctx)
	rows, err := tx.Query(ctx, `SELECT `+workstreamStatusColumns+` FROM cairn.workstream_status WHERE repo=$1 AND (sensitivity='shareable' OR $2) AND ($3='' OR stream=$3) AND ($4='' OR state=$4) AND ($5='' OR stream>$5) ORDER BY stream ASC LIMIT $6`,
		repo, dest.AllowLocal, req.Stream, req.State, req.After, limit+1)
	if err != nil {
		return out, err
	}
	defer rows.Close()
	for rows.Next() {
		st, err := scanWorkstreamStatus(rows, s.channel.Principal, s.channel.Operator)
		if err != nil {
			return out, err
		}
		if len(out.Streams) == limit {
			out.More = true
			break
		}
		out.Streams = append(out.Streams, st)
		out.NextAfter = st.Stream
	}
	if err := rows.Err(); err != nil {
		return out, err
	}
	if !out.More {
		out.NextAfter = ""
	}
	return out, nil
}

func (s *Store) WorkstreamStatusHistory(ctx context.Context, req WorkstreamStatusHistoryRequest, dest Destination) (WorkstreamStatusHistoryPage, error) {
	out := WorkstreamStatusHistoryPage{Stream: req.Stream, Entries: []WorkstreamStatusHistoryEntry{}}
	if err := validEventDestination(dest); err != nil {
		return out, err
	}
	repo, err := s.eventScope(req.Repo, "")
	if err != nil {
		return out, err
	}
	out.Repo = repo
	if !workstreamNamePattern.MatchString(req.Stream) {
		return out, failure("INVALID_REQUEST", "stream must be 1..64 ASCII letters, digits or _.-")
	}
	if req.BeforeRevision < 0 {
		return out, failure("INVALID_REQUEST", "before_revision must be nonnegative")
	}
	limit := req.Limit
	if limit < 0 || limit > 200 {
		return out, failure("INVALID_REQUEST", "limit must be 0..200")
	}
	if limit == 0 {
		limit = 50
	}
	tx, err := s.begin(ctx)
	if err != nil {
		return out, err
	}
	defer tx.Rollback(ctx)
	rows, err := tx.Query(ctx, `SELECT repo, stream, revision, owner, writer, state, current_item, last_landed, next_checkpoint, blocker, decision_needed, detail, sensitivity, entry_bytes, written_at FROM cairn.workstream_status_history WHERE repo=$1 AND stream=$2 AND (sensitivity='shareable' OR $3) AND ($4::bigint=0 OR revision<$4) ORDER BY revision DESC LIMIT $5`,
		repo, req.Stream, dest.AllowLocal, req.BeforeRevision, limit+1)
	if err != nil {
		return out, err
	}
	defer rows.Close()
	for rows.Next() {
		var e WorkstreamStatusHistoryEntry
		if err := rows.Scan(&e.Repo, &e.Stream, &e.Revision, &e.Owner, &e.Writer, &e.State, &e.Current, &e.LastLanded, &e.NextCheckpoint, &e.Blocker, &e.DecisionNeeded, &e.Detail, &e.Sensitivity, &e.EntryBytes, &e.WrittenAt); err != nil {
			return out, err
		}
		if len(out.Entries) == limit {
			out.More = true
			break
		}
		out.Entries = append(out.Entries, e)
		out.NextBeforeRevision = e.Revision
	}
	if err := rows.Err(); err != nil {
		return out, err
	}
	if !out.More {
		out.NextBeforeRevision = 0
	}
	return out, nil
}
