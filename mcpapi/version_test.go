package mcpapi

import (
	"context"
	"testing"

	"github.com/halbritt/cairn/core"
	"github.com/halbritt/cairn/internal/buildinfo"
)

func TestInitializationReportsFacadeBuildIdentity(t *testing.T) {
	// Initialization reports the facade binary without contacting the API.
	server, err := NewServer(nil, Config{Scope: core.Scope{Repo: "fixture:version", TaskID: "task", RunID: "run"}, AvailableTokens: 32000})
	if err != nil {
		t.Fatal(err)
	}
	session := connect(t, context.Background(), server)
	info := session.InitializeResult().ServerInfo
	if info.Name != "cairn" || info.Version != buildinfo.Read().Label() {
		t.Fatalf("MCP implementation identity: %+v", info)
	}
}
