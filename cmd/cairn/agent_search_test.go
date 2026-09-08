package main

import (
	"context"
	"encoding/json"
	"net"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
)

func TestAgentSearchKeepsContextAndPairsPullCommands(t *testing.T) {
	directory, err := os.MkdirTemp("/tmp", "cairn-search-test-")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		if err := os.RemoveAll(directory); err != nil {
			t.Error(err)
		}
	})
	tokenFile, socket := filepath.Join(directory, "agent.token"), filepath.Join(directory, "api.sock")
	if err = os.WriteFile(tokenFile, []byte("synthetic-agent"), 0600); err != nil {
		t.Fatal(err)
	}
	listener, err := net.Listen("unix", socket)
	if err != nil {
		t.Fatal(err)
	}
	receipt, record, handle := uuid.NewString(), uuid.NewString(), uuid.NewString()
	scope := core.Scope{Repo: "fixture:agent-search", TaskID: "task", RunID: "run"}
	semantic := core.SemanticPackage{Schema: "cairn.semantic/4", Mode: "index", Status: "READY", Scope: scope,
		Query: "sha256:query", Purpose: "context", Destination: core.Destination{Name: "hosted"},
		Policy: "local-loop/1", Ranking: "lexical-scope-recency/3", Tokenizer: "utf8-byte-upper-bound/1",
		AvailableTokens: 32000, OptionalLimit: 3200,
		Index:    []core.IndexEntry{{RecordID: record, Version: 3, Class: "B", Kind: "lesson", Summary: "source", BodySHA256: strings.Repeat("a", 64)}},
		Selected: []core.Selection{{Mandatory: true, Record: core.Record{RecordID: uuid.NewString(), Class: "C", Draft: core.Draft{Body: "mandatory instruction"}}}},
		Omitted:  map[string]int{"OUT_OF_SCOPE": 2}}
	response := core.IndexResult{Package: core.Package{ReceiptID: receipt, Semantic: semantic, Seal: "source-seal"},
		Handles:   []core.IndexHandle{{RecordID: record, Version: 3, Handle: handle}},
		ExpiresAt: time.Now().Add(time.Minute).UTC(), CreditsRemaining: 4, BytesRemaining: 24000}
	requests := make(chan core.CompileRequest, 1)
	server := &http.Server{Handler: http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/v1/index" || r.Header.Get("Authorization") != "Bearer synthetic-agent" {
			t.Error("search bypassed authenticated index")
		}
		var req core.CompileRequest
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			t.Error(err)
		}
		requests <- req
		if err := json.NewEncoder(w).Encode(map[string]any{"schema": "cairn.response/1", "ok": true, "status": "OK", "data": response}); err != nil {
			t.Error(err)
		}
	})}
	go func() {
		if err := server.Serve(listener); err != nil && err != http.ErrServerClosed {
			t.Error(err)
		}
	}()
	t.Cleanup(func() {
		if err := server.Close(); err != nil {
			t.Error(err)
		}
	})
	requestID := uuid.NewString()
	result, err := run(context.Background(), []string{"agent", "--socket", socket, "--token-file", tokenFile,
		"search", "--repo", scope.Repo, "--task", scope.TaskID, "--run", scope.RunID, "--request-id", requestID,
		"--revision", "fixture-revision", "fixture query"}, strings.NewReader(""))
	if err != nil {
		t.Fatal(err)
	}
	req := <-requests
	if req.Scope != scope || req.RequestID != requestID || req.Query != "fixture query" || req.AvailableTokens != 32000 || req.Context.Revision != "fixture-revision" {
		t.Fatalf("request changed: %+v", req)
	}
	encoded, err := json.Marshal(result)
	if err != nil {
		t.Fatal(err)
	}
	var view map[string]any
	if err = json.Unmarshal(encoded, &view); err != nil {
		t.Fatal(err)
	}
	if view["schema"] != "cairn.agent-search/1" || view["receipt_id"] != receipt || view["source_seal"] != "source-seal" || view["credits_remaining"] != float64(4) {
		t.Fatalf("missing source/session metadata: %s", encoded)
	}
	entry := view["index"].([]any)[0].(map[string]any)
	command := entry["pull_command"].(string)
	if !strings.Contains(command, "--socket "+socket) || !strings.Contains(command, "--token-file "+tokenFile) || !strings.Contains(command, " pull --request-id ") || !strings.HasSuffix(command, " "+receipt+" "+handle) {
		t.Fatalf("unpaired pull command: %s", command)
	}
	delete(entry, "pull_command")
	view["schema"] = view["source_schema"]
	for _, key := range []string{"source_schema", "source_seal", "receipt_id", "request_id", "expires_at", "credits_remaining", "bytes_remaining"} {
		delete(view, key)
	}
	want, _ := json.Marshal(semantic)
	var original map[string]any
	if err = json.Unmarshal(want, &original); err != nil {
		t.Fatal(err)
	}
	actual, _ := json.Marshal(view)
	want, _ = json.Marshal(original)
	if string(actual) != string(want) {
		t.Fatalf("lost canonical context fields:\ngot %s\nwant %s", actual, want)
	}
}

func TestAgentSearchCommandQuotingAndResponseLimit(t *testing.T) {
	args := []string{"", "with spaces", "quote'and\"double", "$(printf injected)", "`printf injected`", "semi;colon", "line\nbreak", "~/.local/path", "simple/path"}
	command := shellCommand(append([]string{"/usr/bin/printf", "%s\\000"}, args...))
	output, err := exec.Command("/bin/sh", "-c", command).Output()
	if err != nil {
		t.Fatal(err)
	}
	want := strings.Join(args, "\x00") + "\x00"
	if string(output) != want {
		t.Fatalf("shell changed arguments: %q", output)
	}
	result := core.IndexResult{Package: core.Package{ReceiptID: uuid.NewString(), Semantic: core.SemanticPackage{
		Schema: "cairn.semantic/4", Mode: "index", Status: "READY", AvailableTokens: 32000,
		Selected: []core.Selection{{Mandatory: true, Record: core.Record{Draft: core.Draft{Body: "mandatory"}}}},
	}}}
	view, err := presentAgentSearch(result, uuid.NewString(), []string{"cairn", "agent"}, 32000)
	if err != nil || !reflect.DeepEqual(view.Selected, result.Package.Semantic.Selected) || view.Index == nil {
		t.Fatalf("empty optional index lost mandatory context: %+v %v", view, err)
	}
	_, err = presentAgentSearch(result, uuid.NewString(), []string{"cairn", "agent"}, 256)
	if core.Code(err) != "BUDGET_REFUSED" {
		t.Fatalf("oversized mandatory context truncated or disclosed: %v", err)
	}
}

func TestAgentSearchRefusesUnpairedRecordVersions(t *testing.T) {
	record, handle := uuid.NewString(), uuid.NewString()
	result := core.IndexResult{Package: core.Package{ReceiptID: uuid.NewString(), Semantic: core.SemanticPackage{
		Schema: "cairn.semantic/4", Index: []core.IndexEntry{{RecordID: record, Version: 2}},
	}}}
	for _, handles := range [][]core.IndexHandle{
		nil,
		{{RecordID: record, Version: 1, Handle: handle}},
		{{RecordID: record, Version: 2, Handle: handle}, {RecordID: record, Version: 2, Handle: uuid.NewString()}},
	} {
		result.Handles = handles
		_, err := presentAgentSearch(result, uuid.NewString(), []string{"cairn", "agent"}, 32000)
		if core.Code(err) != "INVALID_REQUEST" {
			t.Fatalf("ambiguous source command generated: %v", err)
		}
	}
}
