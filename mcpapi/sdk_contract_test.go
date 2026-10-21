package mcpapi

import (
	"context"
	"encoding/json"
	"os"
	"reflect"
	"testing"

	"github.com/halbritt/cairn/core"
	"github.com/modelcontextprotocol/go-sdk/mcp"
)

func TestSDKNativeAdvertisesAuthoritativeRetrievalContract(t *testing.T) {
	server, err := NewServer(nil, Config{Scope: core.Scope{Repo: "contract", TaskID: "contract", RunID: "contract"}, AvailableTokens: 32000, MaxTokens: 32000})
	if err != nil {
		t.Fatal(err)
	}
	listed, err := connect(t, context.Background(), server).ListTools(context.Background(), nil)
	if err != nil {
		t.Fatal(err)
	}
	want := []*mcp.Tool{}
	for _, tool := range listed.Tools {
		if tool.Name == "cairn_search" || tool.Name == "cairn_pull" {
			want = append(want, tool)
		}
	}
	raw, err := os.ReadFile("../scripts/trial_sdk_native_tools.json")
	if err != nil {
		t.Fatal(err)
	}
	var got any
	if err = json.Unmarshal(raw, &got); err != nil {
		t.Fatal(err)
	}
	encoded, err := json.Marshal(want)
	if err != nil {
		t.Fatal(err)
	}
	var expected any
	if err = json.Unmarshal(encoded, &expected); err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(got, expected) {
		t.Fatal("native tool contract drift: regenerate with go run ./internal/sdkcontract")
	}
}
