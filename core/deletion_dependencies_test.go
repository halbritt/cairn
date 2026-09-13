package core

import (
	"context"
	"errors"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgconn"
)

func deletionDependencyFixture(t *testing.T) (*Store, Grant, Record, Record) {
	t.Helper()
	s, root := testOperator(t)
	source, err := s.Create(context.Background(), CreateRequest{uuid.NewString(), projectNote(uuid.NewString())})
	if err != nil {
		t.Fatal(err)
	}
	draft := projectNote(source.Scope.Repo)
	draft.Relations = []RecordRelation{{source.RecordID, source.Version, "derived_from"}}
	dependent, err := s.Create(context.Background(), CreateRequest{uuid.NewString(), draft})
	if err != nil {
		t.Fatal(err)
	}
	return s, root, source, dependent
}

func TestOldRelationSnapshotCannotCrossAncestorForget(t *testing.T) {
	ctx := context.Background()
	s, root, source, dependent := deletionDependencyFixture(t)
	tx, err := s.beginLevel(ctx, pgx.RepeatableRead)
	if err != nil {
		t.Fatal(err)
	}
	defer tx.Rollback(ctx)
	var generation int
	if err = tx.QueryRow(ctx, `SELECT use_generation FROM cairn.memory_record WHERE record_id=$1`, dependent.RecordID).Scan(&generation); err != nil {
		t.Fatal(err)
	}
	preview, err := s.PreviewDeletion(ctx, source.RecordID)
	if err != nil {
		t.Fatal(err)
	}
	_, err = s.Forget(ctx, ForgetRequest{uuid.NewString(), source.RecordID, source.Version, root.ID, preview.PreviewID})
	if err != nil {
		t.Fatal(err)
	}
	id := uuid.NewString()
	if _, err = tx.Exec(ctx, `INSERT INTO cairn.memory_record(record_id,current_version) VALUES($1,1)`, id); err != nil {
		t.Fatal(err)
	}
	draft := projectNote(source.Scope.Repo)
	draft.Relations = []RecordRelation{{dependent.RecordID, dependent.Version, "derived_from"}}
	_, err = insertVersion(ctx, tx, id, 1, draft)
	var pgErr *pgconn.PgError
	if !errors.As(err, &pgErr) || pgErr.Code != "40001" {
		t.Fatalf("old relation snapshot crossed committed source forgetting: %v", err)
	}
}

func TestAncestorForgetWaitsForRelationWriter(t *testing.T) {
	ctx := context.Background()
	s, root, source, dependent := deletionDependencyFixture(t)
	preview, err := s.PreviewDeletion(ctx, source.RecordID)
	if err != nil {
		t.Fatal(err)
	}
	tx, err := s.begin(ctx)
	if err != nil {
		t.Fatal(err)
	}
	defer tx.Rollback(ctx)
	if _, err = tx.Exec(ctx, `SELECT record_id FROM cairn.memory_record WHERE record_id=$1 FOR UPDATE`, dependent.RecordID); err != nil {
		t.Fatal(err)
	}
	request := ForgetRequest{uuid.NewString(), source.RecordID, source.Version, root.ID, preview.PreviewID}
	blocked, cancel := context.WithTimeout(ctx, 150*time.Millisecond)
	defer cancel()
	_, err = s.Forget(blocked, request)
	if err == nil || blocked.Err() != context.DeadlineExceeded {
		t.Fatalf("forgetting crossed an in-flight dependent writer: %v", err)
	}
	id := uuid.NewString()
	if _, err = tx.Exec(ctx, `INSERT INTO cairn.memory_record(record_id,current_version) VALUES($1,1)`, id); err != nil {
		t.Fatal(err)
	}
	draft := projectNote(source.Scope.Repo)
	draft.Relations = []RecordRelation{{dependent.RecordID, dependent.Version, "derived_from"}}
	if _, err = insertVersion(ctx, tx, id, 1, draft); err != nil {
		t.Fatal(err)
	}
	if err = tx.Commit(ctx); err != nil {
		t.Fatal(err)
	}
	_, err = s.Forget(ctx, request)
	requireCode(t, err, "STALE_PREVIEW")
	preview, err = s.PreviewDeletion(ctx, source.RecordID)
	if err != nil {
		t.Fatal(err)
	}
	request.RequestID, request.PreviewID = uuid.NewString(), preview.PreviewID
	if _, err = s.Forget(ctx, request); err != nil {
		t.Fatal(err)
	}
	pkg, err := s.Compile(ctx, CompileRequest{RequestID: uuid.NewString(), Scope: Scope{source.Scope.Repo, "task", "run"}, Purpose: "context", AvailableTokens: 64000}, Destination{"local", true})
	if err != nil || len(pkg.Semantic.Selected) != 0 {
		t.Fatalf("refreshed deletion lost the new descendant: %+v %v", pkg, err)
	}
}
