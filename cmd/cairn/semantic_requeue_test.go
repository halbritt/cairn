package main

import (
	"context"
	"fmt"
	"os"
	"strings"
	"testing"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
)

// The operator command reaches the store method with one bounded JSON request,
// replays the same request and refuses an invalid reason.
func TestSemanticRequeueOperatorCommand(t *testing.T) {
	dsn := os.Getenv("CAIRN_TEST_DATABASE_URL")
	if dsn == "" {
		t.Skip("run make test-integration")
	}
	t.Setenv("CAIRN_DATABASE_URL", dsn)
	ctx := context.Background()
	s, err := core.Open(ctx, dsn, core.Channel{Principal: "requeue-cli-fixture"})
	if err != nil {
		t.Fatal(err)
	}
	defer s.Close()
	if err := s.Migrate(ctx); err != nil {
		t.Fatal(err)
	}
	if _, err := s.Create(ctx, core.CreateRequest{RequestID: uuid.NewString(), Draft: core.Draft{Kind: "note", Body: uuid.NewString(), ClaimType: "self", Scope: core.Scope{Repo: uuid.NewString(), TaskID: "*", RunID: "*"}}}); err != nil {
		t.Fatal(err)
	}
	request := fmt.Sprintf(`{"request_id":%q,"reason":"full semantic rebuild"}`, uuid.NewString())
	first, err := run(ctx, []string{"semantic-requeue"}, strings.NewReader(request))
	if err != nil {
		t.Fatal(err)
	}
	result, ok := first.(core.SemanticRequeue)
	if !ok || result.Eligible < 1 || result.Queued+result.AlreadyQueued != result.Eligible {
		t.Fatalf("requeue result: %#v", first)
	}
	replay, err := run(ctx, []string{"semantic-requeue"}, strings.NewReader(request))
	if err != nil || replay != first {
		t.Fatalf("requeue replay: %#v %v", replay, err)
	}
	if _, err := run(ctx, []string{"semantic-requeue"}, strings.NewReader(fmt.Sprintf(`{"request_id":%q,"reason":"short"}`, uuid.NewString()))); core.Code(err) != "INVALID_REQUEST" {
		t.Fatalf("short reason: %v", err)
	}
	if _, err := run(ctx, []string{"semantic-requeue"}, strings.NewReader(fmt.Sprintf(`{"request_id":%q,"reason":"full semantic rebuild","model":"x"}`, uuid.NewString()))); core.Code(err) != "INVALID_REQUEST" {
		t.Fatalf("unknown field: %v", err)
	}
}
