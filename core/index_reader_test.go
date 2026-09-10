package core

import (
	"context"
	"reflect"
	"strings"
	"testing"

	"github.com/google/uuid"
)

func TestIndexObserverDesignatesExpansionReaderWithoutTransferringOwnership(t *testing.T) {
	ctx := context.Background()
	repo := uuid.NewString()
	host := testStore(t, Channel{Principal: "host:" + repo, Instrumented: true, Repo: repo})
	reader := testStore(t, Channel{Principal: "reader:" + repo, Repo: repo})
	draft := projectNote(repo)
	draft.Sensitivity = "shareable"
	if _, err := reader.Create(ctx, CreateRequest{uuid.NewString(), draft}); err != nil {
		t.Fatal(err)
	}
	req := CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Query: "compiler", Purpose: "context", AvailableTokens: 32000}
	req.ExpansionReader = reader.channel.Principal
	dest := Destination{"hosted", false}
	index, err := host.Index(ctx, req, dest)
	if err != nil {
		t.Fatal(err)
	}
	if len(index.Handles) != 1 {
		t.Fatalf("expected one handle: %+v", index)
	}
	pull := ExpandRequest{RequestID: uuid.NewString(), ReceiptID: index.Package.ReceiptID, Handle: index.Handles[0].Handle}
	got, err := reader.Expand(ctx, pull, dest)
	if err != nil || got.Selection.Record.Body != draft.Body {
		t.Fatalf("designated reader cannot retrieve indexed body: %+v %v", got, err)
	}
	again, err := reader.Expand(ctx, pull, dest)
	if err != nil || again.CreditsRemaining != got.CreditsRemaining {
		t.Fatalf("retry: %+v %v", again, err)
	}
	_, err = reader.Explain(ctx, index.Package.ReceiptID)
	requireCode(t, err, "AUTHORITY_DENIED")
	_, err = reader.RunStatus(ctx, index.Package.ReceiptID)
	requireCode(t, err, "AUTHORITY_DENIED")
	requireCode(t, reader.ClaimRun(ctx, index.Package.ReceiptID), "AUTHORITY_DENIED")
	if _, err = host.Explain(ctx, index.Package.ReceiptID); err != nil {
		t.Fatal(err)
	}
	for _, ch := range []Channel{
		{Principal: "outsider:" + repo, Repo: repo},
		{Principal: reader.channel.Principal, Repo: "foreign"},
		{Principal: reader.channel.Principal, Repo: repo, Instrumented: true},
		{Principal: reader.channel.Principal, Repo: repo, Operator: true},
	} {
		_, err := testStore(t, ch).Expand(ctx, pull, dest)
		requireCode(t, err, "AUTHORITY_DENIED")
	}
	_, err = reader.Expand(ctx, pull, Destination{"local", true})
	requireCode(t, err, "AUTHORITY_DENIED")
	_, err = reader.Index(ctx, req, dest)
	requireCode(t, err, "AUTHORITY_DENIED")
	changed := req
	changed.ExpansionReader = "other:" + repo
	_, err = host.Index(ctx, changed, dest)
	requireCode(t, err, "IDEMPOTENCY_CONFLICT")
	for _, value := range []string{"  ", " reader", strings.Repeat("x", 257), host.channel.Principal} {
		changed.RequestID, changed.ExpansionReader = uuid.NewString(), value
		_, err = host.Index(ctx, changed, dest)
		requireCode(t, err, "INVALID_REQUEST")
	}
	record := got.Selection.Record
	if _, err = reader.Revise(ctx, ReviseRequest{RequestID: uuid.NewString(), RecordID: record.RecordID, ExpectedVersion: record.Version, Repo: repo, Body: "Changed compiler source"}); err != nil {
		t.Fatal(err)
	}
	_, err = reader.Expand(ctx, pull, dest)
	requireCode(t, err, "STALE_HANDLE")
	_, err = host.RunIndex(ctx, RunPackageRequest{ReceiptID: index.Package.ReceiptID, Seal: index.Package.Seal}, dest)
	requireCode(t, err, "STALE_PACKAGE")

}

func TestDesignatedReaderSharesEvidenceBudgetAndRechecksSourceOnRetry(t *testing.T) {
	ctx := context.Background()
	_, _, record, evidence, _, dest := evidenceExpansionFixture(t, "shareable")
	host := testStore(t, Channel{Principal: "host:" + record.Scope.Repo, Repo: record.Scope.Repo, Instrumented: true})
	reader := testStore(t, Channel{Principal: "reader:" + record.Scope.Repo, Repo: record.Scope.Repo})
	index, err := host.Index(ctx, CompileRequest{RequestID: uuid.NewString(), Scope: Scope{record.Scope.Repo, "task", "run"}, Purpose: "context", AvailableTokens: 64000, ExpansionReader: reader.channel.Principal}, dest)
	if err != nil {
		t.Fatal(err)
	}
	req := ExpandEvidenceRequest{ExpectedSHA256: evidence.Digest, RequestID: uuid.NewString(), ReceiptID: index.Package.ReceiptID, Handle: index.Handles[0].Handle, EvidenceID: evidence.ID}
	got, err := reader.ExpandEvidence(ctx, req, dest)
	if err != nil || got.CreditsRemaining != 3 || got.Evidence.Digest != evidence.Digest {
		t.Fatalf("reader source: %+v %v", got, err)
	}
	for _, s := range []*Store{host, reader, host} {
		if _, err = s.Expand(ctx, ExpandRequest{RequestID: uuid.NewString(), ReceiptID: req.ReceiptID, Handle: req.Handle}, dest); err != nil {
			t.Fatal(err)
		}
	}
	more := req
	more.RequestID = uuid.NewString()
	_, err = reader.ExpandEvidence(ctx, more, dest)
	requireCode(t, err, "BUDGET_REFUSED")
	_, err = reader.ExpandEvidence(ctx, req, dest)
	if err != nil {
		t.Fatalf("exact retry spent another credit: %v", err)
	}
	if _, err = host.pool.Exec(ctx, `UPDATE cairn.index_session SET expires_at=clock_timestamp()-interval '1 second' WHERE receipt_id=$1`, req.ReceiptID); err != nil {
		t.Fatal(err)
	}
	_, err = reader.ExpandEvidence(ctx, req, dest)
	requireCode(t, err, "STALE_HANDLE")
}

func TestRetainedIndexPreservesHandlesBudgetReaderAndExpiresBeforeLaunch(t *testing.T) {
	ctx := context.Background()
	repo := uuid.NewString()
	host := testStore(t, Channel{Principal: "host:" + repo, Instrumented: true, Repo: repo})
	reader := testStore(t, Channel{Principal: "reader:" + repo, Repo: repo})
	if _, err := host.Create(ctx, CreateRequest{uuid.NewString(), projectNote(repo)}); err != nil {
		t.Fatal(err)
	}
	req := CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Purpose: "context", AvailableTokens: 32000, ExpansionReader: reader.channel.Principal}
	dest := Destination{"local", true}
	index, err := host.Index(ctx, req, dest)
	if err != nil {
		t.Fatal(err)
	}
	ref := RunPackageRequest{index.Package.ReceiptID, index.Package.Seal}
	newNote := projectNote(repo)
	newNote.Body = "Additional optional context after the retained index"
	if _, err := host.Create(ctx, CreateRequest{uuid.NewString(), newNote}); err != nil {
		t.Fatal(err)
	}
	got, err := host.RunIndex(ctx, ref, dest)
	if err != nil || !reflect.DeepEqual(got, index) {
		t.Fatalf("retained index changed: %+v %v", got, err)
	}
	pull := ExpandRequest{RequestID: uuid.NewString(), ReceiptID: ref.ReceiptID, Handle: index.Handles[0].Handle}
	expanded, err := reader.Expand(ctx, pull, dest)
	if err != nil {
		t.Fatal(err)
	}
	got, err = host.RunIndex(ctx, ref, dest)
	if err != nil || got.CreditsRemaining != expanded.CreditsRemaining || got.BytesRemaining != expanded.BytesRemaining || !reflect.DeepEqual(got.Handles, index.Handles) || got.ExpansionReader != reader.channel.Principal {
		t.Fatalf("retained pull state: %+v %v", got, err)
	}
	_, err = reader.RunIndex(ctx, ref, dest)
	requireCode(t, err, "AUTHORITY_DENIED")
	if _, err = host.pool.Exec(ctx, `UPDATE cairn.index_session SET expires_at=clock_timestamp()-interval '1 second' WHERE receipt_id=$1`, ref.ReceiptID); err != nil {
		t.Fatal(err)
	}
	_, err = host.RunIndex(ctx, ref, dest)
	requireCode(t, err, "STALE_HANDLE")
	requireCode(t, host.ClaimRun(ctx, ref.ReceiptID), "STALE_HANDLE")
	_, err = reader.Expand(ctx, pull, dest)
	requireCode(t, err, "STALE_HANDLE")
}
