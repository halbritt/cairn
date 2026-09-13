package indexview

import (
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
)

func TestCoreReservationWithPullCommands(t *testing.T) {
	result := core.IndexResult{Package: core.Package{ReceiptID: uuid.NewString(), Seal: "blake3:" + strings.Repeat("a", 64), Semantic: core.SemanticPackage{Schema: "cairn.semantic/8", Mode: "index", Status: "READY", AvailableTokens: 32000, OptionalLimit: 6000}}, ExpiresAt: time.Now().UTC(), CreditsRemaining: 4}
	for i := 0; i < 12; i++ {
		id := uuid.NewString()
		result.Package.Semantic.Index = append(result.Package.Semantic.Index, core.IndexEntry{RecordID: id, Version: 1, Class: "A", Kind: "note", Summary: "fixture", BodySHA256: strings.Repeat("b", 64)})
		result.Handles = append(result.Handles, core.IndexHandle{RecordID: id, Version: 1, Handle: uuid.NewString()})
	}
	rendered, err := result.Package.Render()
	if err != nil {
		t.Fatal(err)
	}
	room := len(rendered) + 160*len(result.Handles) + 512
	command := []string{"/home/fixture/.local/bin/cairn", "agent", "--socket", "/home/fixture/.local/share/cairn/api.sock", "--token-file", "/home/fixture/.local/share/cairn/hosted-agent.token"}
	_, err = Present(result, uuid.NewString(), command, room)
	if err != nil {
		t.Fatalf("core-admitted envelope (%d bytes): %v", room, err)
	}
}
