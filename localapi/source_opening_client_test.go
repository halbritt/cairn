package localapi_test

import (
	"context"
	"encoding/json"
	"testing"

	"github.com/halbritt/cairn/core"
	"github.com/halbritt/cairn/localapi"
)

func TestClientDeclaresOnlyReadersThatRetainSourceOpening(t *testing.T) {
	client, seen := recordingAPI(t, nil)
	for _, tc := range []struct {
		operation string
		target    any
		want      string
	}{
		{"expand", &core.Expansion{}, "1"},
		{"expand", new(json.RawMessage), "1"},
		{"expand", &struct{ Selection core.Selection }{}, ""},
		{"index", new(json.RawMessage), ""},
	} {
		if err := client.Call(context.Background(), tc.operation, map[string]string{"request_id": "unchanged"}, tc.target); err != nil {
			t.Fatal(err)
		}
		calls := seen()
		last := calls[len(calls)-1]
		if got := last.header.Get(localapi.SourceOpeningHeader); got != tc.want {
			t.Fatalf("%s %T: header=%q want=%q", tc.operation, tc.target, got, tc.want)
		}
		var body map[string]any
		if err := json.Unmarshal([]byte(last.body), &body); err != nil {
			t.Fatal(err)
		}
		delete(body, localapi.BodyProtocolField)
		if len(body) != 1 || body["request_id"] != "unchanged" {
			t.Fatalf("response declaration changed intent: %s", last.body)
		}
	}
}
