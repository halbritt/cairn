package semantic

import (
	"context"
	"os"
	"testing"
)

// Selected acceptance runs use the Go owner with the Rust embed worker, the
// only supported backend. A stale non-Rust selection fails instead of skipping.
func realEmbeddingForTest(t *testing.T) *CommandEmbedder {
	t.Helper()
	backend := os.Getenv("CAIRN_REAL_EMBEDDING_BACKEND")
	if backend != "" && backend != "rust" {
		t.Fatalf("unsupported real embedding backend %q; only rust remains", backend)
	}
	path := os.Getenv("CAIRN_RUST_EMBED_TEST_CONFIG")
	if path == "" {
		if backend == "rust" {
			t.Fatal("selected Rust backend requires CAIRN_RUST_EMBED_TEST_CONFIG")
		}
		t.Skip("requires a prepared Rust embed worker configuration")
	}
	config, err := ReadRustWorkerConfig(path)
	if err != nil {
		t.Fatal(err)
	}
	model, err := RustEmbeddingCommand(context.Background(), config)
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
