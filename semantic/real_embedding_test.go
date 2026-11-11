package semantic

import (
	"context"
	"os"
	"testing"
)

// Selected acceptance runs use the same Go owner for each actual backend.
func realEmbeddingForTest(t *testing.T) *CommandEmbedder {
	t.Helper()
	backend := os.Getenv("CAIRN_REAL_EMBEDDING_BACKEND")
	var model *CommandEmbedder
	var err error
	switch backend {
	case "rust":
		config, readErr := ReadRustWorkerConfig(os.Getenv("CAIRN_RUST_EMBED_TEST_CONFIG"))
		if readErr != nil {
			t.Fatal(readErr)
		}
		model, err = RustEmbeddingCommand(context.Background(), config)
	case "", "python":
		path := os.Getenv("CAIRN_EMBEDDING_WORKER")
		if path == "" {
			if backend == "python" {
				t.Fatal("selected Python backend requires CAIRN_EMBEDDING_WORKER")
			}
			t.Skip("requires a prepared local embedding worker")
		}
		model, err = EmbeddingCommand(context.Background(), path)
	default:
		t.Fatalf("unknown real embedding backend %q", backend)
	}
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(model.Close)
	identity, err := model.Identity(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	t.Logf("real backend=%q identity=%+v", backend, identity)
	return model
}
