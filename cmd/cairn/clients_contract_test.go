package main

import (
	"context"
	"encoding/json"
	"fmt"
	"github.com/halbritt/cairn/core"
	"net/http"
	"strings"
	"testing"
)

func TestClientsRemoteDenialRemainsUnknownNotUnsupported(t *testing.T) {
	socket, token, _ := clientsAPI(t, func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(403)
		w.Write([]byte(`{"schema":"cairn.response/1","ok":false,"status":"AUTHORITY_DENIED","message":"operation is unavailable to remote machine profiles"}`))
	})
	_, err := run(context.Background(), []string{"clients", "--socket", socket, "--token-file", token}, strings.NewReader(""))
	if core.Code(err) != "AUTHORITY_DENIED" || !strings.Contains(err.Error(), "predates") || !strings.Contains(err.Error(), "unknown") {
		t.Fatalf("denial ambiguity lost: %v", err)
	}
}
func clientsFixture(t *testing.T) map[string]any {
	t.Helper()
	var envelope map[string]any
	if err := json.Unmarshal([]byte(emptyView), &envelope); err != nil {
		t.Fatal(err)
	}
	return envelope
}
func TestClientsValidatesAndProjectsPeerData(t *testing.T) {
	for name, change := range map[string]func(map[string]any){
		"new schema":        func(v map[string]any) { v["schema"] = "cairn.clients/9" },
		"exhaustive":        func(v map[string]any) { v["exhaustive"] = true },
		"bad build":         func(v map[string]any) { v["server"].(map[string]any)["go_version"] = "\x1b[31mCANARY" },
		"epoch":             func(v map[string]any) { v["observation_epoch"] = "CANARY" },
		"missing time":      func(v map[string]any) { delete(v, "observed_at") },
		"rows exceed limit": func(v map[string]any) { v["returned"] = 129; v["eligible_rows"] = 129; v["rows"] = make([]any, 129) },
	} {
		t.Run(name, func(t *testing.T) {
			envelope := clientsFixture(t)
			change(envelope["data"].(map[string]any))
			raw, _ := json.Marshal(envelope)
			socket, token, _ := clientsAPI(t, answer(string(raw)))
			result, err := run(context.Background(), []string{"clients", "--socket", socket, "--token-file", token}, strings.NewReader(""))
			if core.Code(err) != "INVALID_DIAGNOSTICS" || result != nil || strings.Contains(err.Error(), "CANARY") {
				t.Fatalf("unsafe data accepted/reflected: %T %v", result, err)
			}
		})
	}
	envelope := clientsFixture(t)
	envelope["data"].(map[string]any)["future_text"] = "CANARY\x1b[31m"
	raw, _ := json.Marshal(envelope)
	socket, token, _ := clientsAPI(t, answer(string(raw)))
	result, err := run(context.Background(), []string{"clients", "--socket", socket, "--token-file", token, "--limit", "128"}, strings.NewReader(""))
	if err != nil {
		t.Fatal(err)
	}
	encoded, _ := json.Marshal(result)
	if strings.Contains(string(encoded), "CANARY") || strings.Contains(string(encoded), "future_text") {
		t.Fatal("unknown peer text relayed")
	}
}

func observedFixture(t *testing.T) map[string]any {
	t.Helper()
	v := clientsFixture(t)
	var row map[string]any
	if err := json.Unmarshal([]byte(`{"cohort_id":"00000000-0000-4000-8000-000000000002","principal":"machine:box-b/agent","machine_id":"box-b","metadata_state":"present","origin_state":"reported","first_observed_at":"2026-09-30T12:00:00Z","last_observed_at":"2026-09-30T12:01:00Z","reported":{"schema":"cairn.client-diagnostics/1","surface":"mcp","harness":"claude","transport_build":{"schema":"cairn.build/1","go_version":"go1.25.0","vcs_modified":null},"origin":{"component":"lifecycle-memory","implementation_id":"memory.v3","basis":"reported"},"retrieval_capabilities":{"schema":"cairn.retrieval-capabilities/1","search_memory_budget_bytes":true,"search_min_pull_bytes":true}}}`), &row); err != nil {
		t.Fatal(err)
	}
	data := v["data"].(map[string]any)
	data["rows"] = []any{row}
	data["returned"] = 1
	data["eligible_rows"] = 1
	return v
}
func TestClientsChecksObservedDescriptorsWithoutEchoingThem(t *testing.T) {
	cases := map[string]func(map[string]any){
		"valid": func(row map[string]any) {
			row["future_text"] = "CANARY"
			row["reported"].(map[string]any)["future_text"] = "CANARY"
		},
		"metadata":  func(row map[string]any) { row["metadata_state"] = "CANARY" },
		"cohort":    func(row map[string]any) { row["cohort_id"] = "CANARY" },
		"principal": func(row map[string]any) { row["principal"] = "\x1b[31mCANARY" },
		"machine":   func(row map[string]any) { row["machine_id"] = "/CANARY" },
		"time":      func(row map[string]any) { row["last_observed_at"] = "CANARY" },
		"surface":   func(row map[string]any) { row["reported"].(map[string]any)["surface"] = "CANARY" },
		"harness":   func(row map[string]any) { row["reported"].(map[string]any)["harness"] = "CANARY" },
		"origin": func(row map[string]any) {
			row["reported"].(map[string]any)["origin"].(map[string]any)["component"] = "CANARY"
		},
		"build": func(row map[string]any) {
			row["reported"].(map[string]any)["transport_build"].(map[string]any)["go_version"] = "CANARY"
		},
		"capability omitted flag": func(row map[string]any) {
			delete(row["reported"].(map[string]any)["retrieval_capabilities"].(map[string]any), "search_min_pull_bytes")
		},
		"capability contradiction": func(row map[string]any) {
			row["reported"].(map[string]any)["retrieval_capabilities"].(map[string]any)["search_memory_budget_bytes"] = false
		},
		"state contradiction": func(row map[string]any) { row["metadata_state"] = "missing" },
	}
	for name, change := range cases {
		t.Run(name, func(t *testing.T) {
			envelope := observedFixture(t)
			row := envelope["data"].(map[string]any)["rows"].([]any)[0].(map[string]any)
			change(row)
			raw, _ := json.Marshal(envelope)
			socket, token, _ := clientsAPI(t, answer(string(raw)))
			result, err := run(context.Background(), []string{"clients", "--socket", socket, "--token-file", token}, strings.NewReader(""))
			if name == "valid" {
				if err != nil {
					t.Fatal(err)
				}
				encoded, _ := json.Marshal(result)
				if strings.Contains(string(encoded), "CANARY") || !strings.Contains(string(encoded), "machine:box-b/agent") {
					t.Fatal("projection lost safe identity or retained unknown fields")
				}
				return
			}
			if core.Code(err) != "INVALID_DIAGNOSTICS" || result != nil || strings.Contains(err.Error(), "CANARY") {
				t.Fatalf("unsafe descriptor accepted: %T %v", result, err)
			}
		})
	}
}

func TestClientsEnforcesRequestedPageAndWireBounds(t *testing.T) {
	for _, test := range []struct {
		name    string
		rows    int
		limit   string
		padding bool
		valid   bool
	}{
		{"all retained rows", 128, "128", false, true},
		{"above requested default", 51, "", false, false},
		{"above cohort bound", 129, "128", false, false},
		{"oversized otherwise valid response", 1, "", true, false},
	} {
		t.Run(test.name, func(t *testing.T) {
			envelope := observedFixture(t)
			data := envelope["data"].(map[string]any)
			template := data["rows"].([]any)[0].(map[string]any)
			var rows []any
			for i := 0; i < test.rows; i++ {
				row := map[string]any{}
				for k, v := range template {
					row[k] = v
				}
				row["cohort_id"] = fmt.Sprintf("00000000-0000-4000-8000-%012x", i+2)
				rows = append(rows, row)
			}
			data["rows"], data["returned"], data["eligible_rows"] = rows, len(rows), len(rows)
			if test.padding {
				padding := make([]string, 70000)
				for i := range padding {
					padding[i] = "unused"
				}
				data["future_padding"] = padding
			}
			raw, _ := json.Marshal(envelope)
			socket, token, _ := clientsAPI(t, answer(string(raw)))
			args := []string{"clients", "--socket", socket, "--token-file", token}
			if test.limit != "" {
				args = append(args, "--limit", test.limit)
			}
			result, err := run(context.Background(), args, strings.NewReader(""))
			if test.valid {
				if err != nil {
					t.Fatal(err)
				}
				encoded, _ := json.Marshal(result)
				var got struct {
					Rows []any `json:"rows"`
				}
				json.Unmarshal(encoded, &got)
				if len(got.Rows) != 128 {
					t.Fatalf("lost retained rows: %d", len(got.Rows))
				}
			} else if core.Code(err) != "INVALID_DIAGNOSTICS" {
				t.Fatalf("unbounded result: %T %v", result, err)
			}
		})
	}
}
