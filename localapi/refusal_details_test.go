package localapi_test

import (
	"context"
	"errors"
	"testing"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
)

func TestAuthenticatedRefusalRetainsGateWithoutWideningAccess(t *testing.T) {
	bootstrap := core.BootstrapRequest{RequestID: uuid.NewString(), Reason: "Install isolated refusal fixture authority"}
	for _, destination := range []string{"local", "hosted"} {
		t.Run(destination, func(t *testing.T) {
			client, admin, repo, _ := authenticatedHost(t, "agent", destination, nil)
			ctx := context.Background()
			root, err := admin.Bootstrap(ctx, bootstrap)
			if err != nil {
				t.Fatal(err)
			}
			instruction, err := admin.Issue(ctx, core.IssueRequest{RequestID: uuid.NewString(), Draft: core.Draft{Kind: "instruction", Body: "Required runtime check", Scope: core.Scope{Repo: repo, TaskID: "*", RunID: "*"}, ClaimType: "self", Sensitivity: "shareable"}, GrantID: root.ID, Mandatory: true, RequiresRuntime: true, PolicyKey: "runtime", Reason: "Exercise preserved refusal gate"})
			if err != nil {
				t.Fatal(err)
			}
			req := core.CompileRequest{RequestID: uuid.NewString(), Scope: core.Scope{Repo: repo, TaskID: "task", RunID: "run"}, Purpose: "context", AvailableTokens: 64000}
			var result core.Package
			err = client.Call(ctx, "compile", req, &result)
			var denied *core.Error
			if !errors.As(err, &denied) || denied.Code != "POLICY_UNENFORCEABLE" || denied.RefusalID == "" {
				t.Fatalf("missing refusal: %v", err)
			}
			var detail core.Refusal
			err = client.Call(ctx, "refusal", map[string]string{"refusal_id": denied.RefusalID}, &detail)
			if destination == "hosted" {
				if core.Code(err) != "AUTHORITY_DENIED" || len(detail.Candidates) != 0 {
					t.Fatalf("hosted inspection widened: %+v %v", detail, err)
				}
				return
			}
			if err != nil {
				t.Fatal(err)
			}
			found := false
			for _, candidate := range detail.Candidates {
				if candidate.RecordID == instruction.RecordID {
					found = true
					if !candidate.Mandatory || candidate.Reason != "POLICY_UNENFORCEABLE" || candidate.Facts != nil {
						t.Fatalf("wrong gate: %+v", candidate)
					}
				}
			}
			if !found || detail.ExplanationVersion != 2 || detail.TraceComplete {
				t.Fatalf("missing bounded detail: %+v", detail)
			}
		})
	}
}
