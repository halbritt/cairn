package localapi

import (
	"context"
	"strings"
	"testing"

	"github.com/halbritt/cairn/core"
)

func TestInvokeSharesTheHTTPDecoder(t *testing.T) {
	ctx := context.Background()
	create, ok := LookupOperation("create")
	if !ok || !create.Invocable() {
		t.Fatal("create must be invocable outside HTTP")
	}
	dest := core.Destination{Name: "local", AllowLocal: true}
	if _, err := Invoke(ctx, create, nil, dest, []byte(`{"request_id":"x","bogus":true}`)); core.Code(err) != "INVALID_REQUEST" || !strings.Contains(err.Error(), "unknown field") {
		t.Fatalf("unknown field not refused: %v", err)
	}
	if _, err := Invoke(ctx, create, nil, dest, []byte(`{} {}`)); core.Code(err) != "INVALID_REQUEST" || !strings.Contains(err.Error(), "one JSON") {
		t.Fatalf("trailing value not refused: %v", err)
	}
	if _, err := Invoke(ctx, create, nil, dest, []byte("{\"request_id\":\"\xff\"}")); core.Code(err) != "INVALID_REQUEST" {
		t.Fatalf("invalid unicode not refused: %v", err)
	}
	version, _ := LookupOperation("version")
	if version.Invocable() {
		t.Fatal("handler-shaped operations must stay HTTP-only")
	}
	if _, err := Invoke(ctx, version, nil, dest, []byte(`{}`)); core.Code(err) != "INVALID_REQUEST" {
		t.Fatalf("non-invocable operation did not refuse: %v", err)
	}
	invocable := 0
	for _, spec := range Operations() {
		if spec.Invocable() {
			invocable++
		}
	}
	if invocable < 50 {
		t.Fatalf("expected the store-bound majority of the catalog to be invocable, got %d", invocable)
	}
}
