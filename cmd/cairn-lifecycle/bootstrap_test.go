package main

import (
	"bytes"
	"encoding/json"
	"strings"
	"testing"

	"github.com/halbritt/cairn/internal/lifecycle/coordination"
)

func TestPrivateBootstrapDispatchCannotPublishOrReplayAuthority(t *testing.T) {
	for _, args := range [][]string{{"unit-bootstrap"}, {"unit-bootstrap", "switch", "--plan", "/not-opened", "--sha256", "not-opened"}, {"unit-bootstrap", "reverse", "--plan", "/not-opened", "--sha256", "not-opened"}, {"unit-bootstrap", "inspect", "--trusted"}} {
		var out, diagnostic bytes.Buffer
		code := run(args, strings.NewReader(`{"facts":{},"stopped":true}`), &out, &diagnostic)
		var result coordination.UnitBootstrapResult
		if err := json.Unmarshal(out.Bytes(), &result); err != nil {
			t.Fatal(err)
		}
		if code == 0 || result.Schema != "cairn.unit-bootstrap-result/1" || result.Published || result.Inspected || result.Error == "" || diagnostic.Len() != 0 {
			t.Fatalf("bootstrap dispatch lost refusal: code=%d result=%+v diagnostic=%s", code, result, diagnostic.String())
		}
	}
}
