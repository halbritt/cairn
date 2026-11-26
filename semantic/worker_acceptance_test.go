package semantic

import (
	"context"
	"encoding/json"
	"os"
	"os/exec"
	"path/filepath"
	"testing"

	"github.com/halbritt/cairn/core"
)

func TestStreamProtocolFrameLimits(t *testing.T) {
	var inventory struct {
		Cases []struct {
			ID       string `json:"id"`
			Accepted bool   `json:"accepted"`
		} `json:"stream_limit_cases"`
	}
	raw, err := os.ReadFile("testdata/executable-harness/reference-inventory.json")
	if err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal(raw, &inventory); err != nil || len(inventory.Cases) != 10 {
		t.Fatalf("missing model-free frame limit inventory: %v", err)
	}
	path := filepath.Join(t.TempDir(), "stream-limits")
	build := exec.Command("cc", "-O2", "-DMODE=4", "testdata/executable-harness/protocol-worker.c", "-o", path)
	if output, err := build.CombinedOutput(); err != nil {
		t.Fatalf("compile model-free stream fixture: %v: %s", err, output)
	}
	for _, test := range inventory.Cases {
		t.Run(test.ID, func(t *testing.T) {
			rank, closeWorker, err := StreamCommand(context.Background(), path)
			if err != nil {
				t.Fatal(err)
			}
			defer closeWorker()
			_, err = rank(context.Background(), core.SemanticRankRequest{Query: test.ID})
			if (err == nil) != test.Accepted {
				t.Fatalf("unchanged Go reader accepted=%t, want %t: %v", err == nil, test.Accepted, err)
			}
			t.Logf("unchanged Go reader accepted=%t: %v", err == nil, err)
		})
	}
}
