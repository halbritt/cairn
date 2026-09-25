package core

import (
	"context"
	"os"
	"sync"
	"testing"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
)

func testStore(t *testing.T, channel Channel) *Store {
	t.Helper()
	dsn := os.Getenv("CAIRN_TEST_DATABASE_URL")
	if dsn == "" {
		t.Skip("PostgreSQL integration test: run make test-integration")
	}
	s, err := Open(context.Background(), dsn, channel)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(s.Close)
	if err = s.Migrate(context.Background()); err != nil {
		t.Fatal(err)
	}
	return s
}
func note() Draft {
	return Draft{Kind: "note", Body: "synthetic ordinary memory", Scope: Scope{Repo: "fixture:repo", TaskID: uuid.NewString(), RunID: uuid.NewString()}, ClaimType: "self"}
}
func requireCode(t *testing.T, err error, code string) {
	t.Helper()
	if err == nil || Code(err) != code {
		t.Fatalf("want %s; got %v", code, err)
	}
}

func TestRetryCASAndAuthorship(t *testing.T) {
	ctx := context.Background()
	s := testStore(t, Channel{Principal: "agent:writer"})
	req := CreateRequest{uuid.NewString(), note()}
	first, err := s.Create(ctx, req)
	if err != nil {
		t.Fatal(err)
	}
	retry, err := s.Create(ctx, req)
	if err != nil || retry.RecordID != first.RecordID || retry.Version != 1 {
		t.Fatalf("retry: %+v %v", retry, err)
	}
	req.Draft.Body = "changed intent"
	_, err = s.Create(ctx, req)
	requireCode(t, err, "IDEMPOTENCY_CONFLICT")
	req.RequestID = uuid.NewString()
	distinct, err := s.Create(ctx, req)
	if err != nil || distinct.RecordID == first.RecordID {
		t.Fatalf("intentional resubmit: %+v %v", distinct, err)
	}

	editor := testStore(t, Channel{Principal: "agent:editor"})
	edit := EditRequest{uuid.NewString(), first.RecordID, 1, note()}
	edit.Draft.Scope = first.Scope
	changed, err := editor.Edit(ctx, edit)
	if err != nil || changed.Version != 2 || changed.ObservedWriter != "agent:editor" {
		t.Fatalf("edit: %+v %v", changed, err)
	}
	oldWriter := ""
	err = s.pool.QueryRow(ctx, `SELECT observed_writer FROM cairn.record_version WHERE record_id=$1 AND version=1`, first.RecordID).Scan(&oldWriter)
	if err != nil || oldWriter != "agent:writer" {
		t.Fatalf("original authorship lost: %s %v", oldWriter, err)
	}
	replay, err := editor.Edit(ctx, edit)
	if err != nil || replay.Version != 2 {
		t.Fatalf("edit replay %+v %v", replay, err)
	}
	edit.RequestID = uuid.NewString()
	_, err = editor.Edit(ctx, edit)
	requireCode(t, err, "VERSION_CONFLICT")
	edit.ExpectedVersion = 2
	edit.Draft.Scope.Repo = "another:repo"
	_, err = editor.Edit(ctx, edit)
	requireCode(t, err, "AUTHORITY_DENIED")
}

func TestOrdinaryEditTreatsEmptyApplicabilityAsOmitted(t *testing.T) {
	ctx := context.Background()
	s := testStore(t, Channel{Principal: "agent:empty-applicability"})
	for _, original := range []*Applicability{nil, {}} {
		draft := note()
		draft.Pins = original
		created, err := s.Create(ctx, CreateRequest{uuid.NewString(), draft})
		if err != nil {
			t.Fatal(err)
		}
		draft.Body = "edited without applicability constraints"
		if original == nil {
			draft.Pins = &Applicability{}
		} else {
			draft.Pins = nil
		}
		updated, err := s.Edit(ctx, EditRequest{uuid.NewString(), created.RecordID, created.Version, draft})
		if err != nil || updated.Version != created.Version+1 {
			t.Fatalf("empty applicability edit: %+v %v", updated, err)
		}
	}
}

func TestConcurrentEditsAndDuplicateDelivery(t *testing.T) {
	ctx := context.Background()
	s := testStore(t, Channel{Principal: "agent:race"})
	req := CreateRequest{uuid.NewString(), note()}
	var wg sync.WaitGroup
	ids := make(chan string, 12)
	errs := make(chan error, 12)
	for range 12 {
		wg.Add(1)
		go func() { defer wg.Done(); r, e := s.Create(ctx, req); ids <- r.RecordID; errs <- e }()
	}
	wg.Wait()
	close(ids)
	close(errs)
	for err := range errs {
		if err != nil {
			t.Fatal(err)
		}
	}
	first := ""
	for id := range ids {
		if first == "" {
			first = id
		}
		if id != first {
			t.Fatal("duplicate request created two records")
		}
	}
	errorsByEdit := make(chan error, 2)
	for range 2 {
		wg.Add(1)
		go func() {
			defer wg.Done()
			_, err := s.Edit(ctx, EditRequest{uuid.NewString(), first, 1, req.Draft})
			errorsByEdit <- err
		}()
	}
	wg.Wait()
	close(errorsByEdit)
	successes, conflicts := 0, 0
	for err := range errorsByEdit {
		if err == nil {
			successes++
		} else if Code(err) == "VERSION_CONFLICT" {
			conflicts++
		} else {
			t.Fatal(err)
		}
	}
	if successes != 1 || conflicts != 1 {
		t.Fatalf("successes=%d conflicts=%d", successes, conflicts)
	}
}

func TestStampingAndTransactionIsolation(t *testing.T) {
	ctx := context.Background()
	s := testStore(t, Channel{Principal: "agent:trusted"})
	tx, err := s.begin(ctx)
	if err != nil {
		t.Fatal(err)
	}
	id := uuid.NewString()
	_, err = tx.Exec(ctx, `INSERT INTO cairn.memory_record(record_id,current_version) VALUES($1,1)`, id)
	if err != nil {
		t.Fatal(err)
	}
	_, err = tx.Exec(ctx, `INSERT INTO cairn.record_version(record_id,version,kind,body,repo,task_id,run_id,claim_type,observed_writer,witness,written_at)
        VALUES($1,1,'note','fixture','r','t','u','self','forged','instrumented','2000-01-01')`, id)
	if err != nil {
		t.Fatal(err)
	}
	r, err := readRecord(ctx, tx, id)
	if err != nil || r.ObservedWriter != "agent:trusted" || r.Witness != "testimony" || r.WrittenAt.Year() == 2000 {
		t.Fatalf("stamp: %+v %v", r, err)
	}
	if err = tx.Commit(ctx); err != nil {
		t.Fatal(err)
	}

	conn, err := s.pool.Acquire(ctx)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Release()
	tx, err = conn.Begin(ctx)
	if err != nil {
		t.Fatal(err)
	}
	defer tx.Rollback(ctx)
	id = uuid.NewString()
	if _, err = tx.Exec(ctx, `INSERT INTO cairn.memory_record(record_id,current_version) VALUES($1,1)`, id); err != nil {
		t.Fatal(err)
	}
	_, err = tx.Exec(ctx, `INSERT INTO cairn.record_version(record_id,version,kind,body,repo,task_id,run_id,claim_type) VALUES($1,1,'note','fixture','r','t','u','self')`, id)
	if err == nil {
		t.Fatal("missing channel inherited prior caller or was accepted")
	}
}

func TestAttributionAndRecoveredFailure(t *testing.T) {
	ctx := context.Background()
	writer := testStore(t, Channel{Principal: "agent:dispatcher"})
	service := testStore(t, Channel{Principal: "service:runner", Instrumented: true})
	draft := note()
	draft.Kind = "claim"
	draft.ClaimType = "completion"
	draft.AttributedProducer = "agent:delegate"
	draft.AttemptID = uuid.NewString()
	draft.ResultRef = "artifact:claimed"
	claim, err := writer.Create(ctx, CreateRequest{uuid.NewString(), draft})
	if err != nil || claim.AttributionState != "unreconciled" {
		t.Fatalf("missing attempt: %+v %v", claim, err)
	}
	spawn := SpawnRequest{uuid.NewString(), draft.AttemptID, "agent:dispatcher", "agent:delegate", draft.Scope}
	_, err = writer.RecordSpawn(ctx, spawn)
	requireCode(t, err, "AUTHORITY_DENIED")
	if _, err = service.RecordSpawn(ctx, spawn); err != nil {
		t.Fatal(err)
	}
	terminal := TerminalRequest{uuid.NewString(), draft.AttemptID, "failed", "artifact:partial"}
	if _, err = service.RecordTerminal(ctx, terminal); err != nil {
		t.Fatal(err)
	}
	if _, err = service.RecordTerminal(ctx, terminal); err != nil {
		t.Fatal(err)
	}
	claim, err = writer.Get(ctx, claim.RecordID)
	if err != nil || claim.AttributionState != "contradicted" {
		t.Fatalf("failed attempt: %+v %v", claim, err)
	}
	var recovered, docket int
	err = service.pool.QueryRow(ctx, `SELECT count(*) FROM cairn.memory_record WHERE recovered_attempt_id=$1`, draft.AttemptID).Scan(&recovered)
	if err != nil || recovered != 1 {
		t.Fatalf("recovered %d %v", recovered, err)
	}
	err = service.pool.QueryRow(ctx, `SELECT count(*) FROM cairn.correction_docket WHERE record_id=$1`, claim.RecordID).Scan(&docket)
	if err != nil || docket != 1 {
		t.Fatalf("docket %d %v", docket, err)
	}
	draft.ClaimType = "partial"
	partial, err := writer.Create(ctx, CreateRequest{uuid.NewString(), draft})
	if err != nil || partial.AttributionState != "unreconciled" {
		t.Fatalf("partial wrongly treated as completion: %+v %v", partial, err)
	}
	terminal.RequestID = uuid.NewString()
	terminal.State = "completed"
	_, err = service.RecordTerminal(ctx, terminal)
	requireCode(t, err, "VERSION_CONFLICT")
}

func TestSuccessfulRetryNeedsExactCorrespondence(t *testing.T) {
	ctx := context.Background()
	writer := testStore(t, Channel{Principal: "agent:correlation"})
	service := testStore(t, Channel{Principal: "service:correlation", Instrumented: true})
	scope := note().Scope
	attemptID := uuid.NewString()
	if _, err := service.RecordSpawn(ctx, SpawnRequest{uuid.NewString(), attemptID, "agent:correlation", "agent:delegate", scope}); err != nil {
		t.Fatal(err)
	}
	if _, err := service.RecordTerminal(ctx, TerminalRequest{uuid.NewString(), attemptID, "completed", "artifact:actual"}); err != nil {
		t.Fatal(err)
	}
	for _, scenario := range []string{"exact", "wrong-result", "wrong-run", "wrong-attempt", "wrong-delegate", "wrong-writer"} {
		t.Run(scenario, func(t *testing.T) {
			draft := Draft{Kind: "claim", Body: "synthetic completion", Scope: scope, ClaimType: "completion", AttributedProducer: "agent:delegate", AttemptID: attemptID, ResultRef: "artifact:actual"}
			actor := writer
			switch scenario {
			case "wrong-result":
				draft.ResultRef = "artifact:other"
			case "wrong-run":
				draft.Scope.RunID = uuid.NewString()
			case "wrong-attempt":
				draft.AttemptID = uuid.NewString()
			case "wrong-delegate":
				draft.AttributedProducer = "agent:other"
			case "wrong-writer":
				actor = testStore(t, Channel{Principal: "agent:other"})
			}
			r, err := actor.Create(ctx, CreateRequest{uuid.NewString(), draft})
			expected := "unreconciled"
			if scenario == "exact" {
				expected = "reconciled"
			}
			if err != nil || r.AttributionState != expected {
				t.Fatalf("%s: %+v %v", scenario, r, err)
			}
		})
	}
}

func TestFailedMutationLeavesNoPartialState(t *testing.T) {
	ctx := context.Background()
	s := testStore(t, Channel{Principal: "agent:rollback"})
	id, requestID := uuid.NewString(), uuid.NewString()
	_, err := mutate(ctx, s, "test-rollback", requestID, "intent", func(tx pgx.Tx) (string, error) {
		_, err := tx.Exec(ctx, `INSERT INTO cairn.memory_record(record_id,current_version) VALUES($1,1)`, id)
		if err != nil {
			return "", err
		}
		return "", failure("TEST_FAILURE", "injected after first write")
	})
	requireCode(t, err, "TEST_FAILURE")
	var count int
	if err = s.pool.QueryRow(ctx, `SELECT count(*) FROM cairn.memory_record WHERE record_id=$1`, id).Scan(&count); err != nil || count != 0 {
		t.Fatalf("partial record %d %v", count, err)
	}
	if err = s.pool.QueryRow(ctx, `SELECT count(*) FROM cairn.mutation_request WHERE request_id=$1`, requestID).Scan(&count); err != nil || count != 0 {
		t.Fatalf("partial receipt %d %v", count, err)
	}
}

func TestCurrentVersionMustExistAtCommit(t *testing.T) {
	ctx := context.Background()
	s := testStore(t, Channel{Principal: "agent:constraint"})
	tx, err := s.begin(ctx)
	if err != nil {
		t.Fatal(err)
	}
	defer tx.Rollback(ctx)
	if _, err = tx.Exec(ctx, `INSERT INTO cairn.memory_record(record_id,current_version) VALUES($1,1)`, uuid.NewString()); err != nil {
		t.Fatal(err)
	}
	if err = tx.Commit(ctx); err == nil {
		t.Fatal("dangling current-version pointer committed")
	}
}

func TestTerminalRacesClaimIngestion(t *testing.T) {
	ctx := context.Background()
	writer := testStore(t, Channel{Principal: "agent:terminal-race"})
	service := testStore(t, Channel{Principal: "service:terminal-race", Instrumented: true})
	draft := note()
	draft.Kind = "claim"
	draft.ClaimType = "completion"
	draft.AttributedProducer = "agent:delegate"
	draft.AttemptID = uuid.NewString()
	draft.ResultRef = "artifact:claimed"
	_, err := service.RecordSpawn(ctx, SpawnRequest{uuid.NewString(), draft.AttemptID, "agent:terminal-race", "agent:delegate", draft.Scope})
	if err != nil {
		t.Fatal(err)
	}
	var wg sync.WaitGroup
	var claim Record
	var createErr, terminalErr error
	wg.Add(2)
	go func() { defer wg.Done(); claim, createErr = writer.Create(ctx, CreateRequest{uuid.NewString(), draft}) }()
	go func() {
		defer wg.Done()
		_, terminalErr = service.RecordTerminal(ctx, TerminalRequest{uuid.NewString(), draft.AttemptID, "timeout", ""})
	}()
	wg.Wait()
	if createErr != nil || terminalErr != nil {
		t.Fatalf("create=%v terminal=%v", createErr, terminalErr)
	}
	current, err := writer.Get(ctx, claim.RecordID)
	if err != nil || current.AttributionState != "contradicted" {
		t.Fatalf("missed reconciliation: %+v %v", current, err)
	}
	var count int
	err = service.pool.QueryRow(ctx, `SELECT count(*) FROM cairn.correction_docket WHERE record_id=$1`, claim.RecordID).Scan(&count)
	if err != nil || count != 1 {
		t.Fatalf("missed docket: %d %v", count, err)
	}
}

func TestAttemptOwnershipAndRequestIsolation(t *testing.T) {
	ctx := context.Background()
	owner := testStore(t, Channel{Principal: "service:owner", Instrumented: true})
	other := testStore(t, Channel{Principal: "service:other", Instrumented: true})
	req := SpawnRequest{uuid.NewString(), uuid.NewString(), "agent:dispatcher", "agent:delegate", note().Scope}
	if _, err := owner.RecordSpawn(ctx, req); err != nil {
		t.Fatal(err)
	}
	terminal := TerminalRequest{uuid.NewString(), req.AttemptID, "completed", "artifact:result"}
	_, err := other.RecordTerminal(ctx, terminal)
	requireCode(t, err, "AUTHORITY_DENIED")
	if _, err = owner.RecordTerminal(ctx, terminal); err != nil {
		t.Fatal(err)
	}
	create := CreateRequest{uuid.NewString(), note()}
	first, err := owner.Create(ctx, create)
	if err != nil {
		t.Fatal(err)
	}
	second, err := other.Create(ctx, create)
	if err != nil || first.RecordID == second.RecordID {
		t.Fatalf("caller keys collapsed: %+v %v", second, err)
	}
}
