package core

import (
	"context"
	"errors"
	"strings"
	"testing"

	"github.com/google/uuid"
)

func TestForgetExcludesCopiesBeforePurgeAndKeepsUseHistory(t *testing.T) {
	ctx := context.Background()
	s, root := testOperator(t)
	repo := uuid.NewString()
	create := CreateRequest{uuid.NewString(), projectNote(repo)}
	create.Draft.Body = "fixture_error deletion_canary_" + uuid.NewString()
	r, err := s.Create(ctx, create)
	if err != nil {
		t.Fatal(err)
	}
	compile := CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Query: "fixture_error", Purpose: "context", AvailableTokens: 64000}
	pkg, err := s.Compile(ctx, compile, Destination{"local", true})
	if err != nil || len(pkg.Semantic.Selected) != 1 {
		t.Fatalf("compile: %+v %v", pkg, err)
	}
	preview, err := s.PreviewDeletion(ctx, r.RecordID)
	if err != nil {
		t.Fatal(err)
	}
	req := ForgetRequest{RequestID: uuid.NewString(), RecordID: r.RecordID, ExpectedVersion: r.Version, GrantID: root.ID, PreviewID: preview.PreviewID}
	deletion, err := s.Forget(ctx, req)
	if err != nil {
		t.Fatal(err)
	}
	if deletion.State != "partial" {
		t.Fatalf("must not report completed before purge: %+v", deletion)
	}
	_, err = s.Get(ctx, r.RecordID)
	requireCode(t, err, "PAYLOAD_UNAVAILABLE")
	_, err = s.Create(ctx, create)
	requireCode(t, err, "PAYLOAD_UNAVAILABLE")
	_, err = s.Replay(ctx, pkg.ReceiptID)
	requireCode(t, err, "PAYLOAD_UNAVAILABLE")
	_, err = s.Recompile(ctx, RecompileRequest{pkg.ReceiptID, "fixture_error"})
	requireCode(t, err, "PAYLOAD_UNAVAILABLE")
	compile.RequestID = uuid.NewString()
	fresh, err := s.Compile(ctx, compile, Destination{"local", true})
	if err != nil || len(fresh.Semantic.Selected) != 0 {
		t.Fatalf("forgotten content selected: %+v %v", fresh, err)
	}
	impact, err := s.Impact(ctx, r.RecordID, 0)
	if err != nil || len(impact.Uses) != 1 {
		t.Fatalf("use history lost: %+v %v", impact, err)
	}
	retried, err := s.Forget(ctx, req)
	if err != nil || retried.DeletionID != deletion.DeletionID {
		t.Fatalf("retry: %+v %v", retried, err)
	}
	purged, err := s.PurgeDeletion(ctx, deletion.DeletionID)
	if err != nil {
		t.Fatal(err)
	}
	if purged.State != "limited" {
		t.Fatalf("external and storage residuals must remain explicit: %+v", purged)
	}
	for _, effect := range purged.Effects {
		if strings.HasPrefix(effect.TargetType, "db_") && effect.Status != "completed" {
			t.Fatalf("database effect incomplete: %+v", effect)
		}
	}
	var retained bool
	err = s.pool.QueryRow(ctx, `SELECT EXISTS(SELECT 1 FROM cairn.record_version WHERE record_id=$1 AND body<>'') OR EXISTS(SELECT 1 FROM cairn.retrieval_receipt WHERE receipt_id=$2 AND semantic_body IS NOT NULL) OR EXISTS(SELECT 1 FROM cairn.mutation_request WHERE request_id=$3 AND response IS NOT NULL)`, r.RecordID, pkg.ReceiptID, create.RequestID).Scan(&retained)
	if err != nil || retained {
		t.Fatalf("retained database payload: %v %v", retained, err)
	}
	again, err := s.PurgeDeletion(ctx, deletion.DeletionID)
	if err != nil || again.State != "limited" {
		t.Fatalf("purge retry: %+v %v", again, err)
	}
}

func TestForgetRefusesNewCitationsAndBlocksExistingDependents(t *testing.T) {
	ctx := context.Background()
	s, root := testOperator(t)
	repo := uuid.NewString()
	source, err := s.Create(ctx, CreateRequest{uuid.NewString(), projectNote(repo)})
	if err != nil {
		t.Fatal(err)
	}
	draft := projectNote(repo)
	draft.Body = "fixture_error advice derived from a source"
	draft.Relations = []RecordRelation{{source.RecordID, 1, "derived_from"}}
	dependent, err := s.Create(ctx, CreateRequest{uuid.NewString(), draft})
	if err != nil {
		t.Fatal(err)
	}
	preview, err := s.PreviewDeletion(ctx, source.RecordID)
	if err != nil {
		t.Fatal(err)
	}
	_, err = s.Forget(ctx, ForgetRequest{uuid.NewString(), source.RecordID, 1, root.ID, preview.PreviewID})
	if err != nil {
		t.Fatal(err)
	}
	_, err = s.Create(ctx, CreateRequest{uuid.NewString(), draft})
	requireCode(t, err, "PAYLOAD_UNAVAILABLE")
	pkg, err := s.Compile(ctx, CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Query: "fixture_error", Purpose: "context", AvailableTokens: 64000}, Destination{"local", true})
	if err != nil {
		t.Fatal(err)
	}
	if len(pkg.Semantic.Selected) != 0 {
		t.Fatalf("dependent without available source selected: %+v", pkg.Semantic.Selected)
	}
	retained, err := s.Get(ctx, dependent.RecordID)
	if err != nil || retained.Body != draft.Body {
		t.Fatalf("related record erased instead of flagged: %+v %v", retained, err)
	}
}

func TestNewDependentInheritsForgottenSourceExclusion(t *testing.T) {
	ctx := context.Background()
	s, root := testOperator(t)
	repo := uuid.NewString()
	source, err := s.Create(ctx, CreateRequest{uuid.NewString(), projectNote(repo)})
	if err != nil {
		t.Fatal(err)
	}
	draft := projectNote(repo)
	draft.Relations = []RecordRelation{{source.RecordID, source.Version, "derived_from"}}
	dependent, err := s.Create(ctx, CreateRequest{uuid.NewString(), draft})
	if err != nil {
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
	draft.Body = "new_dep_after_forgetting retains a link to unavailable support"
	draft.Relations = []RecordRelation{{dependent.RecordID, dependent.Version, "derived_from"}}
	later, err := s.Create(ctx, CreateRequest{uuid.NewString(), draft})
	if err != nil {
		t.Fatal(err)
	}
	pkg, err := s.Compile(ctx, CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Query: "new_dep_after_forgetting", Purpose: "context", AvailableTokens: 64000}, Destination{"local", true})
	if err != nil {
		t.Fatal(err)
	}
	for _, entry := range pkg.Semantic.Selected {
		if entry.Record.RecordID == later.RecordID {
			t.Fatalf("new relation bypassed forgotten-source exclusion: %+v", entry)
		}
	}
	// An independently revised version without the withdrawn dependency is
	// distinct from the historical version that still cites unavailable support.
	independent, err := s.Edit(ctx, EditRequest{RequestID: uuid.NewString(), RecordID: dependent.RecordID, ExpectedVersion: dependent.Version, Draft: projectNote(repo)})
	if err != nil {
		t.Fatal(err)
	}
	draft.Body = "independent_revision_fixture has no retained withdrawn dependency"
	draft.Relations = []RecordRelation{{independent.RecordID, independent.Version, "derived_from"}}
	fresh, err := s.Create(ctx, CreateRequest{uuid.NewString(), draft})
	if err != nil {
		t.Fatal(err)
	}
	pkg, err = s.Compile(ctx, CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Query: "independent_revision_fixture", Purpose: "context", AvailableTokens: 64000}, Destination{"local", true})
	if err != nil {
		t.Fatal(err)
	}
	for _, entry := range pkg.Semantic.Selected {
		if entry.Record.RecordID == fresh.RecordID {
			return
		}
	}
	t.Fatal("version-specific exclusion leaked onto an independently revised source")
}

func TestPurgeFailureIsDurableAndResumesRemainingEffects(t *testing.T) {
	ctx := context.Background()
	s, root := testOperator(t)
	repo := uuid.NewString()
	r, err := s.Create(ctx, CreateRequest{uuid.NewString(), projectNote(repo)})
	if err != nil {
		t.Fatal(err)
	}
	pkg, err := s.Compile(ctx, CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Query: "fixture_error", Purpose: "context", AvailableTokens: 64000}, Destination{"local", true})
	if err != nil {
		t.Fatal(err)
	}
	p, err := s.PreviewDeletion(ctx, r.RecordID)
	if err != nil {
		t.Fatal(err)
	}
	deletion, err := s.Forget(ctx, ForgetRequest{uuid.NewString(), r.RecordID, 1, root.ID, p.PreviewID})
	if err != nil {
		t.Fatal(err)
	}
	// Fault only this disposable receipt, at the actual SQL purge boundary.
	name := "purge_fault_" + strings.ReplaceAll(uuid.NewString(), "-", "")
	fault := `CREATE FUNCTION cairn.` + name + `() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN IF NEW.receipt_id='` + pkg.ReceiptID + `'::uuid AND NEW.semantic_body IS NULL THEN RAISE EXCEPTION 'synthetic purge fault'; END IF; RETURN NEW; END $$; CREATE TRIGGER ` + name + ` BEFORE UPDATE OF semantic_body ON cairn.retrieval_receipt FOR EACH ROW EXECUTE FUNCTION cairn.` + name + `()`
	if _, err = s.pool.Exec(ctx, fault); err != nil {
		t.Fatal(err)
	}
	cleanup := `DROP TRIGGER IF EXISTS ` + name + ` ON cairn.retrieval_receipt; DROP FUNCTION IF EXISTS cairn.` + name + `() `
	t.Cleanup(func() {
		if _, err := s.pool.Exec(ctx, cleanup); err != nil {
			t.Error(err)
		}
	})
	_, err = s.PurgeDeletion(ctx, deletion.DeletionID)
	if err == nil {
		t.Fatal("injected purge failure disappeared")
	}
	status, err := s.DeletionStatus(ctx, deletion.DeletionID)
	if err != nil {
		t.Fatal(err)
	}
	completed, failed := 0, 0
	for _, e := range status.Effects {
		if e.Status == "completed" {
			completed++
		}
		if e.Status == "failed" && e.Attempts == 1 && e.LastError != "" {
			failed++
		}
	}
	if status.State != "partial" || completed != 2 || failed != 1 {
		t.Fatalf("partial effects not retained: %+v", status)
	}
	if _, err = s.pool.Exec(ctx, cleanup); err != nil {
		t.Fatal(err)
	}
	resumed, err := s.PurgeDeletion(ctx, deletion.DeletionID)
	if err != nil || resumed.State != "limited" {
		t.Fatalf("resume: %+v %v", resumed, err)
	}
	for _, e := range resumed.Effects {
		if e.TargetType == "db_retrieval_package" && (e.Attempts != 2 || e.LastError != "") {
			t.Fatalf("recovery observation: %+v", e)
		}
		if e.TargetType == "db_record_bodies" && e.Attempts != 1 {
			t.Fatalf("completed effect repeated: %+v", e)
		}
	}
}

func TestForgetPreviewAuthorityConflictAndExpansionBoundaries(t *testing.T) {
	ctx := context.Background()
	s, root := testOperator(t)
	repo := uuid.NewString()
	r, err := s.Create(ctx, CreateRequest{uuid.NewString(), projectNote(repo)})
	if err != nil {
		t.Fatal(err)
	}
	ordinary, err := s.PreviewRetraction(ctx, r.RecordID)
	if err != nil {
		t.Fatal(err)
	}
	req := ForgetRequest{uuid.NewString(), r.RecordID, 1, root.ID, ordinary.PreviewID}
	_, err = s.Forget(ctx, req)
	requireCode(t, err, "IMPACT_PREVIEW_REQUIRED")
	preview, err := s.PreviewDeletion(ctx, r.RecordID)
	if err != nil {
		t.Fatal(err)
	}
	req.PreviewID = preview.PreviewID
	idx, err := s.Index(ctx, CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Query: "fixture_error", Purpose: "context", AvailableTokens: 64000}, Destination{"local", true})
	if err != nil || len(idx.Handles) != 1 {
		t.Fatalf("index: %+v %v", idx, err)
	}
	_, err = s.Forget(ctx, req)
	requireCode(t, err, "STALE_PREVIEW")
	pull := ExpandRequest{uuid.NewString(), idx.Package.ReceiptID, idx.Handles[0].Handle}
	_, err = s.Expand(ctx, pull, Destination{"local", true})
	if err != nil {
		t.Fatal(err)
	}
	other, err := s.Create(ctx, CreateRequest{uuid.NewString(), projectNote(repo)})
	if err != nil {
		t.Fatal(err)
	}
	conflict, err := s.Dispute(ctx, DisputeRequest{uuid.NewString(), []string{r.RecordID, other.RecordID}, "Synthetic unresolved conflicting claims"})
	if err != nil {
		t.Fatal(err)
	}
	preview, err = s.PreviewDeletion(ctx, r.RecordID)
	if err != nil {
		t.Fatal(err)
	}
	req.PreviewID = preview.PreviewID
	_, err = s.Forget(ctx, req)
	requireCode(t, err, "OPEN_CONFLICT")
	var refusal *Error
	if !errors.As(err, &refusal) || refusal.RefusalID == "" {
		t.Fatal("forget refusal was not retained")
	}
	if _, err = s.Resolve(ctx, ResolveRequest{uuid.NewString(), conflict.ID, conflict.Version, root.ID, "Resolve synthetic conflict before forgetting"}); err != nil {
		t.Fatal(err)
	}
	outsider := testStore(t, Channel{Principal: "deletion-outsider:" + repo, Operator: true, Repo: "different:" + repo})
	_, err = outsider.PreviewDeletion(ctx, r.RecordID)
	requireCode(t, err, "AUTHORITY_DENIED")
	agent := testStore(t, Channel{Principal: "deletion-agent:" + repo, Repo: repo})
	_, err = agent.Forget(ctx, req)
	requireCode(t, err, "AUTHORITY_DENIED")
	preview, err = s.PreviewDeletion(ctx, r.RecordID)
	if err != nil {
		t.Fatal(err)
	}
	req.PreviewID = preview.PreviewID
	forgotten, err := s.Forget(ctx, req)
	if err != nil {
		t.Fatal(err)
	}
	_, err = s.Expand(ctx, pull, Destination{"local", true})
	requireCode(t, err, "PAYLOAD_UNAVAILABLE")
	if _, err = s.PurgeDeletion(ctx, forgotten.DeletionID); err != nil {
		t.Fatal(err)
	}
	_, err = s.Expand(ctx, pull, Destination{"local", true})
	requireCode(t, err, "PAYLOAD_UNAVAILABLE")
	_, err = outsider.DeletionStatus(ctx, forgotten.DeletionID)
	requireCode(t, err, "AUTHORITY_DENIED")
	checkpoint, err := s.Checkpoint(ctx, CheckpointRequest{uuid.NewString(), "fixture:deletion-backup"})
	if err != nil {
		t.Fatal(err)
	}
	var included bool
	for _, member := range checkpoint.Members {
		if member.EventID == forgotten.EventID {
			included = true
		}
	}
	if !included {
		t.Fatal("A forgetting D event omitted from checkpoint")
	}
}

func TestCitationAndForgetCannotCommitAnUnreviewedDependency(t *testing.T) {
	ctx := context.Background()
	s, root := testOperator(t)
	for i := 0; i < 8; i++ {
		repo := uuid.NewString()
		source, err := s.Create(ctx, CreateRequest{uuid.NewString(), projectNote(repo)})
		if err != nil {
			t.Fatal(err)
		}
		preview, err := s.PreviewDeletion(ctx, source.RecordID)
		if err != nil {
			t.Fatal(err)
		}
		draft := projectNote(repo)
		draft.Relations = []RecordRelation{{source.RecordID, source.Version, "derived_from"}}
		start := make(chan struct{})
		cited := make(chan error, 1)
		forgotten := make(chan error, 1)
		go func() { <-start; _, err := s.Create(ctx, CreateRequest{uuid.NewString(), draft}); cited <- err }()
		go func() {
			<-start
			_, err := s.Forget(ctx, ForgetRequest{uuid.NewString(), source.RecordID, source.Version, root.ID, preview.PreviewID})
			forgotten <- err
		}()
		close(start)
		citationErr, deletionErr := <-cited, <-forgotten
		if citationErr == nil && deletionErr == nil {
			t.Fatal("deletion accepted a preview that omitted a concurrently committed citation")
		}
		if citationErr != nil && Code(citationErr) != "PAYLOAD_UNAVAILABLE" {
			t.Fatalf("unexpected citation error: %v", citationErr)
		}
		if deletionErr != nil && Code(deletionErr) != "STALE_PREVIEW" && Code(deletionErr) != "VERSION_CONFLICT" {
			t.Fatalf("unexpected deletion error: %v", deletionErr)
		}
	}
}

func TestForgettingSourceRefusesMandatoryDependentInstruction(t *testing.T) {
	ctx := context.Background()
	s, root := testOperator(t)
	repo := uuid.NewString()
	r, err := s.Create(ctx, CreateRequest{uuid.NewString(), projectNote(repo)})
	if err != nil {
		t.Fatal(err)
	}
	draft := projectNote(repo)
	draft.Kind = "instruction"
	draft.Relations = []RecordRelation{{r.RecordID, r.Version, "derived_from"}}
	instruction, err := s.Issue(ctx, IssueRequest{RequestID: uuid.NewString(), Draft: draft, GrantID: root.ID, Mandatory: true, RequiresRuntime: false, PolicyKey: "deletion-support", Reason: "Issue mandatory dependent fixture"})
	if err != nil {
		t.Fatal(err)
	}
	p, err := s.PreviewDeletion(ctx, r.RecordID)
	if err != nil {
		t.Fatal(err)
	}
	_, err = s.Forget(ctx, ForgetRequest{uuid.NewString(), r.RecordID, r.Version, root.ID, p.PreviewID})
	if err != nil {
		t.Fatal(err)
	}
	_, err = s.Compile(ctx, CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Query: "fixture_error", Purpose: "context", AvailableTokens: 64000}, Destination{"local", true})
	requireCode(t, err, "POLICY_UNENFORCEABLE")
	// Withdrawal must remain possible after support disappears. It retires the
	// instruction; it does not make a fresh citation to the forgotten source.
	preview, err := s.PreviewRetraction(ctx, instruction.RecordID)
	if err != nil {
		t.Fatal(err)
	}
	req := RetractRequest{RequestID: uuid.NewString(), RecordID: instruction.RecordID, ExpectedVersion: instruction.Version, GrantID: root.ID, PreviewID: preview.PreviewID, Reason: "Withdraw the instruction whose supporting source was forgotten"}
	retired, err := s.Retract(ctx, req)
	if err != nil || retired.Lifecycle != "retracted" {
		t.Fatalf("unable to withdraw invalid instruction: %+v %v", retired, err)
	}
	if _, err = s.Retract(ctx, req); err != nil {
		t.Fatalf("withdrawal retry: %v", err)
	}
	pkg, err := s.Compile(ctx, CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Purpose: "context", AvailableTokens: 64000}, Destination{"local", true})
	if err != nil || len(pkg.Semantic.Selected) != 0 {
		t.Fatalf("retired instruction still prevents work: %+v %v", pkg, err)
	}
	// Original source references still serve impact/history after retirement.
	var oldLinks int
	if err = s.pool.QueryRow(ctx, `SELECT count(*) FROM cairn.record_relation WHERE from_id=$1 AND from_version=$2 AND to_id=$3`, instruction.RecordID, instruction.Version, r.RecordID).Scan(&oldLinks); err != nil || oldLinks != 1 {
		t.Fatalf("original dependency erased: %d %v", oldLinks, err)
	}
}
