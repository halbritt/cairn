package transcript

import (
	"bytes"
	"context"
	"encoding/json"
	"testing"

	"github.com/halbritt/cairn/internal/lifecycle/coordination"
)

func TestCoordinationBootstrapUsesConcreteReaderAndStructuredRefusal(t *testing.T) {
	for _, args := range [][]string{nil, {"switch", "--plan", "/not-opened", "--sha256", "not-opened"}, {"reverse", "--plan", "/not-opened", "--sha256", "not-opened"}} {
		var out, diagnostic bytes.Buffer
		code := RunCoordinationBootstrap(context.Background(), args, &out, &diagnostic)
		var result coordination.UnitBootstrapResult
		if err := json.Unmarshal(out.Bytes(), &result); err != nil {
			t.Fatal(err)
		}
		if code == 0 || result.Published || result.Inspected || result.OwnerFence != "unknown" || result.Error == "" || diagnostic.Len() != 0 {
			t.Fatalf("bootstrap wrapper lost read-only refusal: code=%d result=%+v diagnostic=%s", code, result, diagnostic.String())
		}
	}
}
