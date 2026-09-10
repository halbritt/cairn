package main

import (
	"context"
	"encoding/json"
	"strings"
	"testing"

	"github.com/halbritt/cairn/core"
)

func TestVersionDoesNotNeedStoreOrConfiguration(t *testing.T) {
	t.Setenv("CAIRN_DATABASE_URL", "not a database URL")
	t.Setenv("CAIRN_HOME", "/absent/version-test")
	result, err := run(context.Background(), []string{"version"}, strings.NewReader(""))
	if err != nil {
		t.Fatal(err)
	}
	encoded, err := json.Marshal(result)
	if err != nil {
		t.Fatal(err)
	}
	var value map[string]any
	if err := json.Unmarshal(encoded, &value); err != nil {
		t.Fatal(err)
	}
	if value["go_version"] == nil || value["schema"] != "cairn.build/1" {
		t.Fatalf("missing executable identity: %s", encoded)
	}
	if _, known := value["vcs_modified"]; !known {
		t.Fatal("unknown modification state must be explicit")
	}
	_, err = run(context.Background(), []string{"version", "unexpected"}, strings.NewReader(""))
	if core.Code(err) != "INVALID_REQUEST" {
		t.Fatalf("unexpected version argument: %v", err)
	}
}
