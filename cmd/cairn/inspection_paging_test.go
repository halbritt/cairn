package main

import (
	"context"
	"os"
	"strings"
	"testing"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
)

func TestInspectionCommandsPagePastFirstHundred(t *testing.T) {
	dsn := os.Getenv("CAIRN_TEST_DATABASE_URL")
	if dsn == "" {
		t.Skip("run make test-integration")
	}
	t.Setenv("CAIRN_DATABASE_URL", dsn)
	ctx := context.Background()
	s, err := core.Open(ctx, dsn, core.Channel{Principal: "paging-fixture"})
	if err != nil {
		t.Fatal(err)
	}
	defer s.Close()
	if err := s.Migrate(ctx); err != nil {
		t.Fatal(err)
	}
	repo := uuid.NewString()
	var record core.Record
	for i := 0; i < 101; i++ {
		record, err = s.Create(ctx, core.CreateRequest{RequestID: uuid.NewString(), Draft: core.Draft{Kind: "note", Body: uuid.NewString(), ClaimType: "self", Scope: core.Scope{Repo: repo, TaskID: "*", RunID: "*"}}})
		if err != nil {
			t.Fatal(err)
		}
	}
	invoke := func(args ...string) any {
		t.Helper()
		result, err := run(ctx, args, strings.NewReader(""))
		if err != nil {
			t.Fatal(err)
		}
		return result
	}
	first := invoke("list", repo).(core.RecordPage)
	if len(first.Records) != 100 || !first.More || first.NextOffset != 100 {
		t.Fatalf("first list page: %+v", first)
	}
	last := invoke("list", "--limit", "1", "--offset", "100", repo).(core.RecordPage)
	if len(last.Records) != 1 || last.More || last.NextOffset != 101 {
		t.Fatalf("last list page: %+v", last)
	}
	for _, r := range first.Records {
		if r.RecordID == last.Records[0].RecordID {
			t.Fatal("list repeated earlier record")
		}
	}
	for i := 0; i < 101; i++ {
		_, err := s.Compile(ctx, core.CompileRequest{RequestID: uuid.NewString(), Scope: core.Scope{Repo: repo, TaskID: "task", RunID: uuid.NewString()}, Query: record.Body, Purpose: "context", AvailableTokens: 32000}, core.Destination{Name: "local", AllowLocal: true})
		if err != nil {
			t.Fatal(err)
		}
	}
	exposures := invoke("impact", record.RecordID).(core.ImpactPage)
	if len(exposures.Uses) != 100 || !exposures.Truncated || exposures.NextOffset != 100 {
		t.Fatalf("first impact page: %+v", exposures)
	}
	remaining := invoke("impact", "--offset", "100", record.RecordID).(core.ImpactPage)
	if len(remaining.Uses) != 1 || remaining.Truncated || remaining.NextOffset != 101 {
		t.Fatalf("last impact page: %+v", remaining)
	}
	for _, r := range exposures.Uses {
		if r.ReceiptID == remaining.Uses[0].ReceiptID {
			t.Fatal("impact repeated earlier exposure")
		}
	}
	for _, args := range [][]string{{"list", "--limit", "201", repo}, {"list", "--offset", "-1", repo}, {"impact", "--offset", "-1", record.RecordID}} {
		if _, err := run(ctx, args, strings.NewReader("")); core.Code(err) != "INVALID_REQUEST" {
			t.Fatalf("invalid paging %v: %v", args, err)
		}
	}
}
