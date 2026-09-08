package core

import (
	"context"
	"testing"

	"github.com/google/uuid"
)

func TestRunPackagePreservesReceiptAndRequiresOwnerRoleDestinationSeal(t *testing.T) {
	ctx := context.Background()
	repo := uuid.NewString()
	host := testStore(t, Channel{Principal: "host:" + repo, Instrumented: true, Repo: repo})
	draft := projectNote(repo)
	record, err := host.Create(ctx, CreateRequest{uuid.NewString(), draft})
	if err != nil {
		t.Fatal(err)
	}
	req := CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Query: "compiler", Purpose: "context", AvailableTokens: 32000}
	pkg, err := host.Compile(ctx, req, Destination{"local", true})
	if err != nil {
		t.Fatal(err)
	}
	ref := RunPackageRequest{pkg.ReceiptID, pkg.Seal}
	for _, scenario := range []struct {
		name        string
		channel     Channel
		destination Destination
		ref         RunPackageRequest
		code        string
	}{
		{"ordinary-channel", Channel{Principal: host.channel.Principal, Repo: repo}, Destination{"local", true}, ref, "AUTHORITY_DENIED"},
		{"other-observer", Channel{Principal: "other:" + repo, Instrumented: true, Repo: repo}, Destination{"local", true}, ref, "AUTHORITY_DENIED"},
		{"other-repository", Channel{Principal: host.channel.Principal, Instrumented: true, Repo: "other:" + repo}, Destination{"local", true}, ref, "AUTHORITY_DENIED"},
		{"hosted-destination", host.channel, Destination{"hosted", false}, ref, "AUTHORITY_DENIED"},
		{"missing-seal", host.channel, Destination{"local", true}, RunPackageRequest{pkg.ReceiptID, ""}, "INVALID_REQUEST"},
		{"wrong-seal", host.channel, Destination{"local", true}, RunPackageRequest{pkg.ReceiptID, "wrong"}, "INTEGRITY_FAILURE"},
	} {
		t.Run(scenario.name, func(t *testing.T) {
			s := testStore(t, scenario.channel)
			_, err := s.RunPackage(ctx, scenario.ref, scenario.destination)
			requireCode(t, err, scenario.code)
		})
	}
	draft.Body = "New optional compiler memory"
	if _, err = host.Create(ctx, CreateRequest{uuid.NewString(), draft}); err != nil {
		t.Fatal(err)
	}
	// Recompiling cannot implement retained delivery: this changes the seal.
	_, err = host.Compile(ctx, req, Destination{"local", true})
	requireCode(t, err, "STALE_PACKAGE")
	for i := 0; i < 2; i++ {
		retained, err := host.RunPackage(ctx, ref, Destination{"local", true})
		if err != nil || retained.ReceiptID != pkg.ReceiptID || retained.Seal != pkg.Seal || retained.Nonce != pkg.Nonce || len(retained.Semantic.Selected) != 1 {
			t.Fatalf("retained package changed: %+v, %v", retained, err)
		}
	}
	status, err := host.RunStatus(ctx, pkg.ReceiptID)
	if err != nil || status.LaunchClaimed || status.BindingObserved {
		t.Fatalf("loading a package authorized execution: %+v, %v", status, err)
	}
	var count int
	if err = host.pool.QueryRow(ctx, `SELECT count(*) FROM cairn.retrieval_receipt WHERE scope->>'repo'=$1`, repo).Scan(&count); err != nil || count != 1 {
		t.Fatalf("loading created a new retrieval: %d, %v", count, err)
	}
	draft.Body = "Corrected selected compiler guidance"
	if _, err = host.Edit(ctx, EditRequest{uuid.NewString(), record.RecordID, record.Version, draft}); err != nil {
		t.Fatal(err)
	}
	_, err = host.RunPackage(ctx, ref, Destination{"local", true})
	requireCode(t, err, "STALE_PACKAGE")
	req.Mode, req.RequestID = "index", uuid.NewString()
	index, err := host.Compile(ctx, req, Destination{"local", true})
	if err != nil {
		t.Fatal(err)
	}
	_, err = host.RunPackage(ctx, RunPackageRequest{index.ReceiptID, index.Seal}, Destination{"local", true})
	requireCode(t, err, "INVALID_REQUEST")
}
