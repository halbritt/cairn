package main

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/halbritt/cairn/internal/lifecycle/coordination"
)

func TestPrivateBootstrapDispatchRefusesMissingPlansAndSerializedAuthority(t *testing.T) {
	for _, args := range [][]string{{"unit-bootstrap"}, {"unit-bootstrap", "switch", "--plan", "/not-opened", "--sha256", "not-opened"}, {"unit-bootstrap", "reverse", "--plan", "/not-opened", "--sha256", "not-opened"}, {"unit-bootstrap", "rollback", "--plan", "/not-opened", "--sha256", "not-opened"}, {"unit-bootstrap", "inspect", "--trusted"}} {
		var out, diagnostic bytes.Buffer
		code := run(args, strings.NewReader(`{"facts":{},"stopped":true}`), &out, &diagnostic)
		var result coordination.UnitBootstrapResult
		if err := json.Unmarshal(out.Bytes(), &result); err != nil {
			t.Fatal(err)
		}
		if code == 0 || result.Schema != "cairn.unit-bootstrap-result/1" || result.Published || result.Inspected || result.DatabaseCoverage != "" || result.Error == "" || diagnostic.Len() != 0 {
			t.Fatalf("bootstrap dispatch lost refusal: code=%d result=%+v diagnostic=%s", code, result, diagnostic.String())
		}
	}
}

func TestBootstrapDispatchDoesNotAcceptSerializedLegacyPreflightOrRecovery(t *testing.T) {
	for _, field := range []string{`"legacy_preflight":true`, `"recovery_journal":{"disabled":true}`, `"owner_fence":"selected-units-only"`} {
		t.Run(field, func(t *testing.T) {
			directory := t.TempDir()
			if err := os.Chmod(directory, 0700); err != nil {
				t.Fatal(err)
			}
			path := filepath.Join(directory, "selection.json")
			raw := []byte(`{"schema":"cairn.unit-bootstrap/2",` + field + `}`)
			if err := os.WriteFile(path, raw, 0600); err != nil {
				t.Fatal(err)
			}
			digest := sha256.Sum256(raw)
			var out, diagnostic bytes.Buffer
			code := run([]string{"unit-bootstrap", "switch", "--plan", path, "--sha256", hex.EncodeToString(digest[:])}, strings.NewReader(""), &out, &diagnostic)
			var result coordination.UnitBootstrapResult
			if err := json.Unmarshal(out.Bytes(), &result); err != nil {
				t.Fatal(err)
			}
			if code == 0 || result.Published || result.Inspected || result.OwnerFence != "unknown" || !strings.Contains(result.Error, "unknown or nonliteral plan field") {
				t.Fatalf("serialized legacy state accepted: %d %+v", code, result)
			}
		})
	}
}
