package core

import (
	"context"
	"reflect"
	"strings"
	"testing"

	"github.com/google/uuid"
)

func TestReplacePreservesMetadataHistoryAndRetry(t *testing.T) {
	ctx := context.Background()
	s := testStore(t, Channel{Principal: "agent:replacement"})
	d := projectNote(uuid.NewString())
	d.Body, d.Kind, d.Sensitivity = "Original rule.\r\nOld command.\nTrailing spaces  ", "procedure", "shareable"
	d.Pins = &Applicability{TaskClass: "build"}
	d.Entities = []EntityRef{{Kind: "file", Name: "core/replace.go"}}
	old, err := s.Create(ctx, CreateRequest{uuid.NewString(), d})
	if err != nil {
		t.Fatal(err)
	}
	evidence, err := s.CaptureEvidence(ctx, EvidenceRequest{RequestID: uuid.NewString(), Repo: d.Scope.Repo, Source: "selected fixture", Body: "source", Sensitivity: "shareable"})
	if err != nil {
		t.Fatal(err)
	}
	_, err = s.Cite(ctx, CiteRequest{RequestID: uuid.NewString(), RecordID: old.RecordID, ExpectedVersion: 1, Repo: d.Scope.Repo, EvidenceCitations: []EvidenceCitationRequest{{EvidenceID: evidence.ID, ExpectedSHA256: evidence.Digest}}})
	if err != nil {
		t.Fatal(err)
	}
	text := "日本語 command."
	req := ReplaceRequest{uuid.NewString(), old.RecordID, 2, d.Scope.Repo, "Old command.", &text}
	result, err := s.Replace(ctx, req)
	if err != nil || result != (Revision{old.RecordID, 3}) {
		t.Fatalf("replace: %+v %v", result, err)
	}
	got, err := s.Get(ctx, old.RecordID)
	want := old.Draft
	want.Body = "Original rule.\r\n日本語 command.\nTrailing spaces  "
	if err != nil || !reflect.DeepEqual(got.Draft, want) || got.Sensitivity != old.Sensitivity {
		t.Fatalf("metadata or unrelated text changed: %+v %v", got, err)
	}
	history, err := s.History(ctx, RecordHistoryRequest{RecordID: old.RecordID, Version: 2}, Destination{"hosted", false})
	if err != nil || len(history.Versions) != 1 || history.Versions[0].Body == nil || *history.Versions[0].Body != old.Body {
		t.Fatalf("lost original: %+v %v", history, err)
	}
	compiled, err := s.Compile(ctx, CompileRequest{RequestID: uuid.NewString(), Scope: Scope{d.Scope.Repo, "task", "run"}, Context: &ContextPins{TaskClass: "build"}, Purpose: "context", AvailableTokens: 64000}, Destination{"hosted", false})
	if err != nil || len(compiled.Semantic.Selected) != 1 {
		t.Fatalf("compile corrected note: %+v %v", compiled, err)
	}
	refs := compiled.Semantic.Selected[0].Evidence
	if len(refs) != 1 || refs[0].ID != evidence.ID || refs[0].Citation == nil || refs[0].Citation.SHA256 != evidence.Digest {
		t.Fatalf("lost source citation: %+v", refs)
	}
	_, err = s.Revise(ctx, ReviseRequest{uuid.NewString(), old.RecordID, 3, d.Scope.Repo, "Later deliberate edit."})
	if err != nil {
		t.Fatal(err)
	}
	retry, err := s.Replace(ctx, req)
	if err != nil || retry != result {
		t.Fatalf("retry changed with later body: %+v %v", retry, err)
	}
	text = "Different intent"
	_, err = s.Replace(ctx, req)
	requireCode(t, err, "IDEMPOTENCY_CONFLICT")
	req.RequestID = uuid.NewString()
	_, err = s.Replace(ctx, req)
	requireCode(t, err, "VERSION_CONFLICT")
}

func TestReplaceRequiresUniqueExactPassageAndNonblankResult(t *testing.T) {
	ctx := context.Background()
	s := testStore(t, Channel{Principal: "agent:replacement-matching"})
	for _, tc := range []struct{ name, body, old, replacement, want string }{
		{"missing", "Retain instructions.", "absent", "new", ""},
		{"repeated", "same then same", "same", "new", ""},
		{"overlapping", "aaa", "aa", "new", ""},
		{"blank", "only instruction", "only instruction", " \n", ""},
		{"exact", "Before\r\n日本語\nAfter  ", "日本語", "New", "Before\r\nNew\nAfter  "},
		{"delete passage", "Keep. Remove. Keep too.", " Remove.", "", "Keep. Keep too."},
		{"disambiguate", "same then same", "then same", "then corrected", "same then corrected"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			d := projectNote(uuid.NewString())
			d.Body = tc.body
			note, err := s.Create(ctx, CreateRequest{uuid.NewString(), d})
			if err != nil {
				t.Fatal(err)
			}
			req := ReplaceRequest{uuid.NewString(), note.RecordID, 1, d.Scope.Repo, tc.old, &tc.replacement}
			_, err = s.Replace(ctx, req)
			version, want := 2, tc.want
			if want == "" {
				requireCode(t, err, "INVALID_REQUEST")
				version, want = 1, tc.body
			} else if err != nil {
				t.Fatal(err)
			}
			got, err := s.Get(ctx, note.RecordID)
			if err != nil || got.Version != version || got.Body != want {
				t.Fatalf("partial or wrong replacement: %+v %v", got, err)
			}
		})
	}
}

func TestReplaceBoundsAndRefusedIntentIsReusable(t *testing.T) {
	ctx := context.Background()
	s := testStore(t, Channel{Principal: "agent:replacement-size"})
	d := projectNote(uuid.NewString())
	d.Body = strings.Repeat("x", 65533) + "end"
	note, err := s.Create(ctx, CreateRequest{uuid.NewString(), d})
	if err != nil {
		t.Fatal(err)
	}
	text := "日本"
	req := ReplaceRequest{uuid.NewString(), note.RecordID, 1, d.Scope.Repo, "end", &text}
	_, err = s.Replace(ctx, req)
	requireCode(t, err, "INVALID_REQUEST")
	text = "日"
	got, err := s.Replace(ctx, req)
	if err != nil || got.Version != 2 {
		t.Fatalf("exact byte limit after refusal: %+v %v", got, err)
	}
	for _, bad := range []ReplaceRequest{
		{uuid.NewString(), note.RecordID, 2, d.Scope.Repo, "日", nil},
		{uuid.NewString(), note.RecordID, 2, d.Scope.Repo, "", &text},
		{uuid.NewString(), note.RecordID, 2, d.Scope.Repo, strings.Repeat("x", 65537), &text},
		{uuid.NewString(), note.RecordID, 2, d.Scope.Repo, "\xff", &text},
	} {
		_, err = s.Replace(ctx, bad)
		requireCode(t, err, "INVALID_REQUEST")
	}
}

func TestReplaceRacingFullEditKeepsOneWholeWinner(t *testing.T) {
	ctx := context.Background()
	s := testStore(t, Channel{Principal: "agent:replacement-race"})
	for range 5 {
		d := projectNote(uuid.NewString())
		d.Body = "Original passage. Keep the rest."
		note, err := s.Create(ctx, CreateRequest{uuid.NewString(), d})
		if err != nil {
			t.Fatal(err)
		}
		start, results := make(chan struct{}), make(chan error, 2)
		go func() {
			<-start
			text := "Corrected passage."
			_, err := s.Replace(ctx, ReplaceRequest{uuid.NewString(), note.RecordID, 1, d.Scope.Repo, "Original passage.", &text})
			results <- err
		}()
		go func() {
			<-start
			full := d
			full.Body, full.Kind = "Complete replacement.", "procedure"
			_, err := s.Edit(ctx, EditRequest{uuid.NewString(), note.RecordID, 1, full})
			results <- err
		}()
		close(start)
		wins := 0
		for range 2 {
			if err := <-results; err == nil {
				wins++
			} else {
				requireCode(t, err, "VERSION_CONFLICT")
			}
		}
		got, err := s.Get(ctx, note.RecordID)
		valid := got.Body == "Corrected passage. Keep the rest." && got.Kind == d.Kind || got.Body == "Complete replacement." && got.Kind == "procedure"
		if err != nil || wins != 1 || got.Version != 2 || !valid {
			t.Fatalf("mixed concurrent edit: %+v wins=%d %v", got, wins, err)
		}
	}
}

func TestHostedReplaceRefusesLocalReplayAndForgottenNote(t *testing.T) {
	ctx := context.Background()
	op, root := testOperator(t)
	draft := projectNote(uuid.NewString())
	draft.Sensitivity, draft.Body = "local", "private passage"
	note, err := op.Create(ctx, CreateRequest{uuid.NewString(), draft})
	if err != nil {
		t.Fatal(err)
	}
	text := "corrected"
	req := ReplaceRequest{uuid.NewString(), note.RecordID, 1, draft.Scope.Repo, "private", &text}
	if _, err := op.Replace(ctx, req); err != nil {
		t.Fatal(err)
	}
	_, err = op.ReplaceForDestination(ctx, req, Destination{"hosted", false})
	requireCode(t, err, "NOT_FOUND")
	preview, err := op.PreviewDeletion(ctx, note.RecordID)
	if err != nil {
		t.Fatal(err)
	}
	_, err = op.Forget(ctx, ForgetRequest{RequestID: uuid.NewString(), RecordID: note.RecordID, ExpectedVersion: 2, GrantID: root.ID, PreviewID: preview.PreviewID})
	if err != nil {
		t.Fatal(err)
	}
	for _, id := range []string{req.RequestID, uuid.NewString()} {
		req.RequestID = id
		_, err = op.ReplaceForDestination(ctx, req, Destination{"hosted", false})
		requireCode(t, err, "NOT_FOUND")
	}
}

func TestReplaceRefusesOtherRepositoriesAndPrivilegedRecords(t *testing.T) {
	ctx := context.Background()
	op, root := testOperator(t)
	repo := uuid.NewString()
	instruction, err := op.Issue(ctx, IssueRequest{RequestID: uuid.NewString(), Draft: Draft{Kind: "instruction", Body: "Required rule", Scope: Scope{repo, "*", "*"}, ClaimType: "self", Sensitivity: "shareable"}, GrantID: root.ID, PolicyKey: "replace-fixture", Reason: "Test ordinary replacement authority"})
	if err != nil {
		t.Fatal(err)
	}
	text := "Unprivileged edit"
	req := ReplaceRequest{uuid.NewString(), instruction.RecordID, instruction.Version, repo, "Required rule", &text}
	_, err = op.Replace(ctx, req)
	requireCode(t, err, "AUTHORITY_DENIED")
	d := projectNote(repo)
	note, err := op.Create(ctx, CreateRequest{uuid.NewString(), d})
	if err != nil {
		t.Fatal(err)
	}
	req.RecordID, req.ExpectedVersion, req.Repo, req.OldText = note.RecordID, note.Version, "foreign", d.Body
	_, err = op.Replace(ctx, req)
	requireCode(t, err, "AUTHORITY_DENIED")
}
