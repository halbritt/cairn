package main

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
	"github.com/halbritt/cairn/localapi"
)

type agentSearchEntry struct {
	core.IndexEntry
	PullCommand   string             `json:"pull_command"`
	PullArguments core.ExpandRequest `json:"pull_arguments"`
}

// This view preserves semantic fields but is not itself a sealed package.
type agentSearchView struct {
	core.SemanticPackage
	Schema           string             `json:"schema"`
	Index            []agentSearchEntry `json:"index"`
	SourceSchema     string             `json:"source_schema"`
	SourceSeal       string             `json:"source_seal"`
	ReceiptID        string             `json:"receipt_id"`
	RequestID        string             `json:"request_id"`
	ExpiresAt        time.Time          `json:"expires_at"`
	CreditsRemaining int                `json:"credits_remaining"`
	BytesRemaining   int                `json:"bytes_remaining"`
}

func agentSearch(ctx context.Context, client *localapi.Client, args []string, socket, tokenFile string) (agentSearchView, error) {
	f := flags("agent search")
	repo := f.String("repo", defaultRepo(), "repository identity")
	task := f.String("task", "", "host task identity (required)")
	run := f.String("run", "", "host run identity (required)")
	request := f.String("request-id", uuid.NewString(), "index retry identity")
	tokens := f.Int("tokens", 32000, "available memory input room")
	revision := f.String("revision", "", "declared repository revision")
	workspace := f.String("workspace-sha256", "", "workspace digest")
	taskClass := f.String("task-class", "", "task category")
	binding := f.String("binding", "", "binding identity")
	capability := f.String("capability", "", "capability identity")
	if err := f.Parse(args); err != nil {
		return agentSearchView{}, invalid(err.Error())
	}
	query := strings.Join(f.Args(), " ")
	if strings.TrimSpace(query) == "" || strings.TrimSpace(*task) == "" || strings.TrimSpace(*run) == "" || *task == "*" || *run == "*" {
		return agentSearchView{}, invalid("agent search requires --task, --run and a nonempty query")
	}
	executable, err := os.Executable()
	if err != nil {
		return agentSearchView{}, err
	}
	socket, err = filepath.Abs(socket)
	if err != nil {
		return agentSearchView{}, err
	}
	tokenFile, err = filepath.Abs(tokenFile)
	if err != nil {
		return agentSearchView{}, err
	}
	var result core.IndexResult
	if err = client.Call(ctx, "index", core.CompileRequest{RequestID: *request,
		Scope: core.Scope{Repo: *repo, TaskID: *task, RunID: *run}, Query: query, Purpose: "context", AvailableTokens: *tokens,
		Context: &core.ContextPins{Revision: *revision, WorkspaceSHA256: *workspace, TaskClass: *taskClass, BindingID: *binding, CapabilityID: *capability}}, &result); err != nil {
		return agentSearchView{}, err
	}
	return presentAgentSearch(result, *request, []string{executable, "agent", "--socket", socket, "--token-file", tokenFile}, *tokens)
}

func presentAgentSearch(result core.IndexResult, request string, command []string, room int) (agentSearchView, error) {
	view := agentSearchView{SemanticPackage: result.Package.Semantic, Schema: "cairn.agent-search/1",
		SourceSchema: result.Package.Semantic.Schema, SourceSeal: result.Package.Seal, ReceiptID: result.Package.ReceiptID,
		RequestID: request, Index: []agentSearchEntry{}, ExpiresAt: result.ExpiresAt,
		CreditsRemaining: result.CreditsRemaining, BytesRemaining: result.BytesRemaining}
	type versionKey struct {
		record  string
		version int
	}
	handles := make(map[versionKey]string, len(result.Handles))
	for _, handle := range result.Handles {
		key := versionKey{handle.RecordID, handle.Version}
		if _, exists := handles[key]; exists || handle.Handle == "" {
			return agentSearchView{}, invalid("index response has duplicate or empty handles")
		}
		handles[key] = handle.Handle
	}
	for _, entry := range result.Package.Semantic.Index {
		handle, exists := handles[versionKey{entry.RecordID, entry.Version}]
		if !exists {
			return agentSearchView{}, invalid("index response has no handle for a record version")
		}
		pull := core.ExpandRequest{RequestID: uuid.NewString(), ReceiptID: result.Package.ReceiptID, Handle: handle}
		argv := append(append([]string{}, command...), "pull", "--request-id", pull.RequestID, pull.ReceiptID, pull.Handle)
		view.Index = append(view.Index, agentSearchEntry{entry, shellCommand(argv), pull})
	}
	encoded, err := json.Marshal(response{Schema: "cairn.response/1", OK: true, Status: "OK", Data: view})
	if err != nil {
		return agentSearchView{}, err
	}
	if len(encoded)+1 > room {
		return agentSearchView{}, &core.Error{Code: "BUDGET_REFUSED", Message: "search response with pull commands exceeds input room; shorten socket/token paths or increase --tokens"}
	}
	return view, nil
}

func shellCommand(args []string) string {
	words := make([]string, len(args))
	for i, word := range args {
		if word != "" && strings.Trim(word, "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-/.:@=") == "" {
			words[i] = word
		} else {
			words[i] = "'" + strings.ReplaceAll(word, "'", "'\"'\"'") + "'"
		}
	}
	return strings.Join(words, " ")
}

func agentPull(ctx context.Context, client *localapi.Client, operation string, args []string) (json.RawMessage, error) {
	f := flags("agent " + operation)
	request := f.String("request-id", uuid.NewString(), "pull retry identity")
	if err := f.Parse(args); err != nil {
		return nil, invalid(err.Error())
	}
	var payload any
	endpoint := "expand"
	if operation == "pull" {
		if f.NArg() != 2 {
			return nil, invalid("agent pull requires RECEIPT_UUID HANDLE_UUID")
		}
		payload = core.ExpandRequest{RequestID: *request, ReceiptID: f.Arg(0), Handle: f.Arg(1)}
	} else {
		if f.NArg() != 4 {
			return nil, invalid("agent pull-evidence requires RECEIPT_UUID HANDLE_UUID EVIDENCE_UUID EXPECTED_SHA256")
		}
		endpoint = "expand-evidence"
		payload = core.ExpandEvidenceRequest{RequestID: *request, ReceiptID: f.Arg(0), Handle: f.Arg(1), EvidenceID: f.Arg(2), ExpectedSHA256: f.Arg(3)}
	}
	var result json.RawMessage
	err := client.Call(ctx, endpoint, payload, &result)
	return result, err
}
