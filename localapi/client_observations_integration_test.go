package localapi_test

import (
	"context"
	"encoding/base64"
	"net/http"
	"strings"
	"sync/atomic"
	"testing"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
	"github.com/halbritt/cairn/localapi"
)

// A declaration is transport metadata. Against a real store, writes and their
// request-ID recovery must behave exactly as without one, whatever the header says.
func TestDeclarationsNeverChangeStoreBackedOperationsOrRetryIdentity(t *testing.T) {
	var mode atomic.Value
	mode.Store("none")
	wrap := func(next http.Handler) http.Handler {
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			switch mode.Load().(string) {
			case "garbage":
				r.Header.Set(localapi.ClientDiagnosticsHeader, "@@not-a-declaration@@")
			case "duplicate":
				value := r.Header.Get(localapi.ClientDiagnosticsHeader)
				r.Header.Add(localapi.ClientDiagnosticsHeader, value)
			case "case-collision":
				r.Header.Set(localapi.ClientDiagnosticsHeader, base64.RawURLEncoding.EncodeToString([]byte(`{"schema":"cairn.client-diagnostics/1","surface":"cli","Surface":"mcp","harness":"unknown","transport_build":{"schema":"cairn.build/1","go_version":"go1.25.0","vcs_modified":null}}`)))
			case "oversized":
				r.Header.Set(localapi.ClientDiagnosticsHeader, strings.Repeat("A", 8001))
			}
			next.ServeHTTP(w, r)
		})
	}
	client, admin, repo, _ := authenticatedHost(t, "agent", "hosted", wrap)
	declared := client.WithDiagnostics(localapi.ClientDiagnostics{Surface: "cli", Harness: "unknown", RetrievalCapabilities: localapi.CurrentRetrievalCapabilities()})
	ctx := context.Background()
	draft := core.Draft{Kind: "note", Body: "diagnostics must not change this write", ClaimType: "self", Sensitivity: "shareable", Scope: core.Scope{Repo: repo, TaskID: "*", RunID: "*"}}
	request := core.CreateRequest{RequestID: uuid.NewString(), Draft: draft}
	var first core.Revision
	if err := client.Call(ctx, "create", request, &first); err != nil { // no declaration at all
		t.Fatal(err)
	}
	// The same request ID, retried with a changed (even broken) diagnostic identity, returns the committed
	// result and creates nothing new.
	for _, m := range []string{"none", "garbage", "duplicate", "oversized", "case-collision"} {
		mode.Store(m)
		var again core.Revision
		if err := declared.Call(ctx, "create", request, &again); err != nil || again != first {
			t.Fatalf("%s: retry changed the outcome: %+v %v (want %+v)", m, again, err, first)
		}
	}
	mode.Store("garbage")
	other := core.CreateRequest{RequestID: uuid.NewString(), Draft: core.Draft{Kind: "note", Body: "a second write under a broken declaration", ClaimType: "self", Sensitivity: "shareable", Scope: draft.Scope}}
	var second core.Revision
	if err := declared.Call(ctx, "create", other, &second); err != nil || second.RecordID == first.RecordID {
		t.Fatalf("a broken declaration changed admission or identity: %+v %v", second, err)
	}
	if record, err := admin.Get(ctx, first.RecordID); err != nil || record.Version != 1 || record.Body != draft.Body {
		t.Fatalf("the retried write was altered or duplicated: %+v %v", record, err)
	}
	mode.Store("none")
	var view localapi.ClientsResponse
	if err := declared.Call(ctx, "clients", localapi.ClientsRequest{}, &view); err != nil {
		t.Fatal(err)
	}
	states := map[string]int{}
	for _, row := range view.Rows {
		states[row.MetadataState]++
		if row.Principal != "host:test" {
			t.Fatalf("a row outside the caller's own scope: %+v", row)
		}
	}
	if states[localapi.MetadataPresent] != 1 || states[localapi.MetadataMissing] != 1 || states[localapi.MetadataInvalid] != 1 || view.Counters.InvalidDeclarations < 3 || view.Storage != "volatile" {
		t.Fatalf("observations from real traffic: %v %+v", states, view.Counters)
	}
}
