package mcpapi

import (
	"context"
	"encoding/json"
	"net/http"
	"reflect"
	"sync/atomic"
	"testing"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
	"github.com/modelcontextprotocol/go-sdk/mcp"
)

func TestNoteOpeningNativeSchemaAndForwarding(t *testing.T) {
	var calls atomic.Int32
	request := core.ExpandRequest{RequestID: uuid.NewString(), ReceiptID: uuid.NewString(), Handle: uuid.NewString(), Span: &core.ByteSpanRequest{Offset: 100, Length: 30}, OpeningBytes: 96}
	opening := &core.ByteSpan{Offset: 0, End: 7, TotalBytes: 150, Body: "Project", SHA256: "checked-prefix-hash"}
	client, _ := diagnosticClient(t, func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/v1/version" {
			json.NewEncoder(w).Encode(map[string]any{"schema": "cairn.response/1", "ok": true, "data": map[string]any{}})
			return
		}
		calls.Add(1)
		var got core.ExpandRequest
		if err := json.NewDecoder(r.Body).Decode(&got); err != nil {
			t.Error(err)
		}
		if r.URL.Path != "/v1/expand" || !reflect.DeepEqual(got, request) {
			t.Errorf("opening intent changed: %+v", got)
		}
		json.NewEncoder(w).Encode(map[string]any{"schema": "cairn.response/1", "ok": true, "data": core.Expansion{SourceOpening: opening, CreditsRemaining: 3}})
	})
	session := diagnosticSession(t, client, 32000)
	list, err := session.ListTools(context.Background(), nil)
	if err != nil {
		t.Fatal(err)
	}
	found := false
	for _, tool := range list.Tools {
		if tool.Name != "cairn_pull" {
			continue
		}
		b, err := json.Marshal(tool.InputSchema)
		if err != nil {
			t.Fatal(err)
		}
		var schema struct {
			Properties map[string]json.RawMessage `json:"properties"`
			Required   []string                   `json:"required"`
		}
		if err = json.Unmarshal(b, &schema); err != nil {
			t.Fatal(err)
		}
		_, found = schema.Properties["opening_bytes"]
		for _, key := range schema.Required {
			if key == "opening_bytes" {
				t.Fatal("opening unexpectedly required")
			}
		}
	}
	if !found {
		t.Fatal("opening missing from tool schema")
	}
	result, err := session.CallTool(context.Background(), &mcp.CallToolParams{Name: "cairn_pull", Arguments: request})
	if err != nil || result.IsError {
		t.Fatalf("pull: %v %+v", err, result)
	}
	var got core.Expansion
	if err = json.Unmarshal([]byte(result.Content[0].(*mcp.TextContent).Text), &got); err != nil {
		t.Fatal(err)
	}
	if calls.Load() != 1 || !reflect.DeepEqual(got.SourceOpening, opening) {
		t.Fatalf("opening lost: %+v", got)
	}
}
