package coordination

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestUnitControlPlanRefusesUnknownInputs(t *testing.T) {
	root := t.TempDir()
	if err := os.Chmod(root, 0700); err != nil {
		t.Fatal(err)
	}
	for _, row := range []struct {
		name, body string
		mode       os.FileMode
	}{
		{"serialized-receipt", `{"schema":"cairn.unit-control/1","facts":{},"complete":true}`, 0600},
		{"duplicate", `{"schema":"a","schema":"b"}`, 0600},
		{"case-alias-duplicate", `{"schema":"a","Schema":"b"}`, 0600},
		{"nonliteral-field", `{"Schema":"cairn.unit-control/1"}`, 0600},
		{"trailing", `{} {}`, 0600},
		{"bad-schema", `{"schema":"unknown","current":{"directory":"/x","pins":{"one.json":"x"}},"next":[{}],"callbacks":[]}`, 0600},
		{"unknown-census", `{"schema":"cairn.unit-control/1","current":{"directory":"/x","pins":{"one.json":"x"}},"next":[{}],"callbacks":null}`, 0600},
		{"unsafe-callback-pid", `{"schema":"cairn.unit-control/1","current":{"directory":"/x","pins":{"one.json":"x"}},"next":[{}],"callbacks":[{"pid":1,"start":1,"boot":"fixture"}]}`, 0600},
		{"unknown-callback-field", `{"schema":"cairn.unit-control/1","current":{"directory":"/x","pins":{"one.json":"x"}},"next":[{}],"callbacks":[{"pid":2,"start":1,"boot":"fixture","complete":true}]}`, 0600},
		{"invalid-callback-start", `{"schema":"cairn.unit-control/1","current":{"directory":"/x","pins":{"one.json":"x"}},"next":[{}],"callbacks":[{"pid":2,"start":-1,"boot":"fixture"}]}`, 0600},
		{"public-mode", `{}`, 0644},
	} {
		t.Run(row.name, func(t *testing.T) {
			path := filepath.Join(root, row.name+".json")
			if err := os.WriteFile(path, []byte(row.body), row.mode); err != nil {
				t.Fatal(err)
			}
			d := sha256.Sum256([]byte(row.body))
			pin := hex.EncodeToString(d[:])
			if _, _, err := readUnitControlPlan(path, pin); err == nil {
				t.Fatal("unknown input accepted")
			}
			var out bytes.Buffer
			cli := &CLI{}
			if code := cli.RunUnitControl(context.Background(), []string{"switch", "--plan", path, "--sha256", pin}, &out); code == 0 {
				t.Fatal("invalid input published")
			}
			var got UnitControlResult
			if err := json.Unmarshal(out.Bytes(), &got); err != nil {
				t.Fatal(err)
			}
			if got.Inspected || got.Exchange.Published || got.Error == "" {
				t.Fatalf("lost refusal: %+v", got)
			}
		})
	}
}

func TestUnitControlRequiresLiteralSyntaxAndPlanPin(t *testing.T) {
	for _, args := range [][]string{nil, {"force"}, {"inspect", "--plan", "/x", "--sha256", "bad"}, {"inspect", "--plan", "/x", "--sha256", strings.Repeat("A", 64)}} {
		var out bytes.Buffer
		if code := new(CLI).RunUnitControl(context.Background(), args, &out); code == 0 {
			t.Fatal("invalid syntax/pin accepted")
		}
		var result UnitControlResult
		if err := json.Unmarshal(out.Bytes(), &result); err != nil {
			t.Fatal(err)
		}
		if result.Exchange.Published || result.Error == "" {
			t.Fatalf("lost refusal: %+v", result)
		}
	}
}
