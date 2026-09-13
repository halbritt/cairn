package indexview

import (
	"context"
	"encoding/json"
	"os"
	"reflect"
	"strings"
	"testing"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
)

func TestTightCompiledIndexKeepsMandatoryContextAndPullArguments(t *testing.T) {
	dsn := os.Getenv("CAIRN_TEST_DATABASE_URL")
	if dsn == "" {
		t.Skip("run make test-integration")
	}
	ctx := context.Background()
	s, err := core.Open(ctx, dsn, core.Channel{Principal: "operator:tests", Operator: true})
	if err != nil {
		t.Fatal(err)
	}
	defer s.Close()
	if err = s.Migrate(ctx); err != nil {
		t.Fatal(err)
	}
	root, err := s.Bootstrap(ctx, core.BootstrapRequest{RequestID: "82bf5bce-dc6c-4d03-a49f-1677bdcc9a32", Reason: "Install synthetic test operator root"})
	if err != nil {
		t.Fatal(err)
	}
	repo := uuid.NewString()
	draft := core.Draft{Kind: "instruction", Body: strings.Repeat("mandatory context ", 2200), Scope: core.Scope{Repo: repo, TaskID: "*", RunID: "*"}, ClaimType: "self"}
	_, err = s.Issue(ctx, core.IssueRequest{RequestID: uuid.NewString(), Draft: draft, GrantID: root.ID, Mandatory: true, PolicyKey: "budget-fixture", Reason: "Exercise mandatory context at presentation boundary"})
	if err != nil {
		t.Fatal(err)
	}
	draft.Kind = "note"
	for i := 0; i < 12; i++ {
		draft.Body = "optional " + uuid.NewString()
		if _, err = s.Create(ctx, core.CreateRequest{RequestID: uuid.NewString(), Draft: draft}); err != nil {
			t.Fatal(err)
		}
	}
	command := []string{"/home/fixture/.local/bin/cairn", "agent", "--socket", "/home/fixture/.local/share/cairn/api.sock", "--token-file", "/home/fixture/.local/share/cairn/hosted-agent.token"}
	for room := 42000; room <= 48000; room += 256 {
		req := core.CompileRequest{RequestID: uuid.NewString(), Scope: core.Scope{Repo: repo, TaskID: "task", RunID: "run"}, Purpose: "context", AvailableTokens: room}
		result, err := s.Index(ctx, req, core.Destination{Name: "local", AllowLocal: true})
		if core.Code(err) == "BUDGET_REFUSED" {
			continue
		}
		if err != nil {
			t.Fatal(err)
		}
		if len(result.Handles) < 2 {
			continue
		}
		full, err := Present(result, req.RequestID, command, 100000)
		if err != nil {
			t.Fatal(err)
		}
		encoded, err := json.Marshal(full)
		if err != nil {
			t.Fatal(err)
		}
		if len(encoded) < room {
			continue
		}
		compact, err := Present(result, req.RequestID, command, room)
		if err != nil {
			t.Fatalf("core admitted %d entries at %d bytes but view refused: %v", len(result.Handles), room, err)
		}
		if len(compact.Index) != len(result.Handles) || !reflect.DeepEqual(compact.Selected, result.Package.Semantic.Selected) || compact.SourceSeal != result.Package.Seal {
			t.Fatal("compact view changed semantic context")
		}
		for i, entry := range compact.Index {
			if entry.PullCommand != "" || entry.PullArguments.Handle == "" || entry.PullArguments.ReceiptID != result.Package.ReceiptID || entry.PullArguments.RequestID == "" || !reflect.DeepEqual(entry.IndexEntry, result.Package.Semantic.Index[i]) {
				t.Fatalf("lost structured pull or entry: %+v", entry)
			}
		}
		t.Logf("core admitted %d entries at %d bytes; full view %d bytes", len(result.Handles), room, len(encoded))
		return
	}
	t.Fatal("fixture did not reach core/presentation budget disagreement")
}
