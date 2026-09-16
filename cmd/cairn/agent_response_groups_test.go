package main

import (
	"context"
	"encoding/json"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
)

func TestResponseGroupHelpAndValidation(t *testing.T) {
	ctx := context.Background()

	// 1. Built-in help documents the response group flags and commands.
	for _, args := range [][]string{{"publish", "--help"}, {"agent", "publish", "--help"}} {
		out, err := run(ctx, args, strings.NewReader(""))
		if err != nil {
			t.Fatalf("%v: %v", args, err)
		}
		help, ok := out.(commandHelp)
		if !ok {
			t.Fatalf("%v: unexpected output type %T", args, out)
		}
		helpText := string(help)
		if !strings.Contains(helpText, "--response-deadline RFC3339") {
			t.Fatalf("%v: help missing --response-deadline RFC3339: %s", args, helpText)
		}
		if !strings.Contains(helpText, "--response-policy all|partial") {
			t.Fatalf("%v: help missing --response-policy all|partial: %s", args, helpText)
		}
		if !strings.Contains(helpText, "response-group") {
			t.Fatalf("%v: help missing response-group: %s", args, helpText)
		}
		if !strings.Contains(helpText, "response-groups") {
			t.Fatalf("%v: help missing response-groups: %s", args, helpText)
		}
	}

	validReqID := uuid.NewString()
	recordID := uuid.NewString()
	validDeadline := "2026-09-16T15:00:00Z"

	// 2. Both --response-deadline and --response-policy are required when either is supplied.
	pairedCases := []struct {
		name string
		args []string
	}{
		{
			"deadline_without_policy",
			[]string{"publish", "--request-id", validReqID, "--to", "agent:bob", "--kind", "request", "--version", "1", "--response-deadline", validDeadline, recordID},
		},
		{
			"policy_without_deadline",
			[]string{"publish", "--request-id", validReqID, "--to", "agent:bob", "--kind", "request", "--version", "1", "--response-policy", "all", recordID},
		},
	}
	for _, tc := range pairedCases {
		t.Run(tc.name, func(t *testing.T) {
			_, err := run(ctx, tc.args, strings.NewReader(""))
			if core.Code(err) != "INVALID_REQUEST" {
				t.Fatalf("%s: expected INVALID_REQUEST, got: %v", tc.name, err)
			}
			if !strings.Contains(err.Error(), "--response-deadline and --response-policy must both be provided") {
				t.Fatalf("%s: expected 'must both be provided', got: %v", tc.name, err)
			}
		})
	}

	// 3. Reject explicitly empty flags or whitespace.
	emptyCases := []struct {
		name string
		args []string
		want string
	}{
		{
			"empty_deadline",
			[]string{"publish", "--request-id", validReqID, "--to", "agent:bob", "--kind", "request", "--version", "1", "--response-deadline", "", "--response-policy", "all", recordID},
			"cannot be explicitly empty",
		},
		{
			"whitespace_deadline",
			[]string{"publish", "--request-id", validReqID, "--to", "agent:bob", "--kind", "request", "--version", "1", "--response-deadline", "   ", "--response-policy", "all", recordID},
			"cannot be explicitly empty",
		},
		{
			"empty_policy",
			[]string{"publish", "--request-id", validReqID, "--to", "agent:bob", "--kind", "request", "--version", "1", "--response-deadline", validDeadline, "--response-policy", "", recordID},
			"cannot be explicitly empty",
		},
		{
			"whitespace_policy",
			[]string{"publish", "--request-id", validReqID, "--to", "agent:bob", "--kind", "request", "--version", "1", "--response-deadline", validDeadline, "--response-policy", "   ", recordID},
			"cannot be explicitly empty",
		},
	}
	for _, tc := range emptyCases {
		t.Run(tc.name, func(t *testing.T) {
			_, err := run(ctx, tc.args, strings.NewReader(""))
			if core.Code(err) != "INVALID_REQUEST" {
				t.Fatalf("%s: expected INVALID_REQUEST, got: %v", tc.name, err)
			}
			if !strings.Contains(err.Error(), tc.want) {
				t.Fatalf("%s: expected %q, got: %v", tc.name, tc.want, err)
			}
		})
	}

	// 4. Reject invalid timestamp formats for --response-deadline.
	invalidTimeCases := []struct {
		name string
		args []string
	}{
		{"not_a_time", []string{"publish", "--request-id", validReqID, "--to", "agent:bob", "--kind", "request", "--version", "1", "--response-deadline", "tomorrow", "--response-policy", "all", recordID}},
		{"date_only", []string{"publish", "--request-id", validReqID, "--to", "agent:bob", "--kind", "request", "--version", "1", "--response-deadline", "2026-09-16", "--response-policy", "all", recordID}},
		{"missing_tz", []string{"publish", "--request-id", validReqID, "--to", "agent:bob", "--kind", "request", "--version", "1", "--response-deadline", "2026-09-16T12:00:00", "--response-policy", "all", recordID}},
	}
	for _, tc := range invalidTimeCases {
		t.Run(tc.name, func(t *testing.T) {
			_, err := run(ctx, tc.args, strings.NewReader(""))
			if core.Code(err) != "INVALID_REQUEST" {
				t.Fatalf("%s: expected INVALID_REQUEST, got: %v", tc.name, err)
			}
			if !strings.Contains(err.Error(), "must be an RFC3339 timestamp") {
				t.Fatalf("%s: expected 'must be an RFC3339 timestamp', got: %v", tc.name, err)
			}
		})
	}

	// 5. Reject invalid --response-policy choices.
	invalidPolicyCases := []struct {
		name   string
		policy string
	}{
		{"unknown_policy", "any"},
		{"numeric_policy", "1"},
		{"partial_typo", "partially"},
	}
	for _, tc := range invalidPolicyCases {
		t.Run(tc.name, func(t *testing.T) {
			args := []string{"publish", "--request-id", validReqID, "--to", "agent:bob", "--kind", "request", "--version", "1", "--response-deadline", validDeadline, "--response-policy", tc.policy, recordID}
			_, err := run(ctx, args, strings.NewReader(""))
			if core.Code(err) != "INVALID_REQUEST" {
				t.Fatalf("%s: expected INVALID_REQUEST, got: %v", tc.name, err)
			}
			if !strings.Contains(err.Error(), "must be all or partial") {
				t.Fatalf("%s: expected 'must be all or partial', got: %v", tc.name, err)
			}
		})
	}

	// 6. Response group flags permitted ONLY on the publish command.
	otherCommands := []struct {
		command string
		args    []string
	}{
		{"inbox_deadline", []string{"inbox", "next", "--response-deadline", validDeadline}},
		{"inbox_policy", []string{"inbox", "next", "--response-policy", "all"}},
		{"ack_deadline", []string{"ack", "--request-id", validReqID, "--lease", uuid.NewString(), "--response-deadline", validDeadline, uuid.NewString()}},
		{"complete_policy", []string{"complete", "--request-id", validReqID, "--lease", uuid.NewString(), "--response-policy", "all", "--stdin", uuid.NewString()}},
		{"retry_deadline", []string{"retry", "--lease", uuid.NewString(), "--response-deadline", validDeadline, uuid.NewString()}},
		{"renew_policy", []string{"renew", "--lease", uuid.NewString(), "--response-policy", "all", uuid.NewString()}},
		{"subscribe_deadline", []string{"subscribe", "--request-id", validReqID, "--topic", "alerts", "--response-deadline", validDeadline}},
		{"unsubscribe_policy", []string{"unsubscribe", "--request-id", validReqID, "--topic", "alerts", "--response-policy", "all"}},
		{"events_deadline", []string{"events", "--response-deadline", validDeadline}},
		{"event_status_policy", []string{"event-status", "--response-policy", "all", uuid.NewString()}},
		{"event_stats_deadline", []string{"event-stats", "--response-deadline", validDeadline}},
		{"response_group_deadline", []string{"response-group", "--response-deadline", validDeadline, uuid.NewString()}},
		{"response_groups_policy", []string{"response-groups", "--response-policy", "all"}},
	}
	for _, tc := range otherCommands {
		t.Run("unsupported_"+tc.command, func(t *testing.T) {
			_, err := run(ctx, tc.args, strings.NewReader(""))
			if core.Code(err) != "INVALID_REQUEST" {
				t.Fatalf("%s: expected INVALID_REQUEST, got: %v", tc.command, err)
			}
			if !strings.Contains(err.Error(), "is not supported by") {
				t.Fatalf("%s: expected 'is not supported by', got: %v", tc.command, err)
			}
		})
	}
}

func TestResponseGroupCommandValidation(t *testing.T) {
	ctx := context.Background()

	// 1. response-group positional argument count and empty checks.
	posCases := []struct {
		name string
		args []string
		want string
	}{
		{"no_args", []string{"response-group"}, "unexpected positional arguments"},
		{"too_many_args", []string{"response-group", uuid.NewString(), uuid.NewString()}, "unexpected positional arguments"},
		{"empty_id", []string{"response-group", ""}, "EVENT_UUID is required"},
		{"prefix_only", []string{"response-group", "cairn:"}, "EVENT_UUID is required"},
	}
	for _, tc := range posCases {
		t.Run(tc.name, func(t *testing.T) {
			_, err := run(ctx, tc.args, strings.NewReader(""))
			if core.Code(err) != "INVALID_REQUEST" {
				t.Fatalf("%s: expected INVALID_REQUEST, got: %v", tc.name, err)
			}
			if !strings.Contains(err.Error(), tc.want) {
				t.Fatalf("%s: expected %q, got: %v", tc.name, tc.want, err)
			}
		})
	}

	// 2. response-group unsupported flags.
	unsupportedGroupFlags := []struct {
		name string
		args []string
	}{
		{"repo_flag", []string{"response-group", "--repo", "/some/repo", uuid.NewString()}},
		{"state_flag", []string{"response-group", "--state", "open", uuid.NewString()}},
		{"to_flag", []string{"response-group", "--to", "agent:alice", uuid.NewString()}},
		{"request_id_flag", []string{"response-group", "--request-id", uuid.NewString(), uuid.NewString()}},
	}
	for _, tc := range unsupportedGroupFlags {
		t.Run("group_"+tc.name, func(t *testing.T) {
			_, err := run(ctx, tc.args, strings.NewReader(""))
			if core.Code(err) != "INVALID_REQUEST" {
				t.Fatalf("%s: expected INVALID_REQUEST, got: %v", tc.name, err)
			}
			if !strings.Contains(err.Error(), "is not supported by response-group") {
				t.Fatalf("%s: expected 'is not supported by response-group', got: %v", tc.name, err)
			}
		})
	}

	// 3. response-groups positional argument count.
	groupsPosCases := []struct {
		name string
		args []string
	}{
		{"with_pos_arg", []string{"response-groups", uuid.NewString()}},
		{"with_two_args", []string{"response-groups", "arg1", "arg2"}},
	}
	for _, tc := range groupsPosCases {
		t.Run(tc.name, func(t *testing.T) {
			_, err := run(ctx, tc.args, strings.NewReader(""))
			if core.Code(err) != "INVALID_REQUEST" {
				t.Fatalf("%s: expected INVALID_REQUEST, got: %v", tc.name, err)
			}
			if !strings.Contains(err.Error(), "unexpected positional arguments") {
				t.Fatalf("%s: expected 'unexpected positional arguments', got: %v", tc.name, err)
			}
		})
	}

	// 4. response-groups state filter validation.
	invalidStateCases := []struct {
		name  string
		state string
	}{
		{"invalid_state", "closed"},
		{"typo_state", "collect"},
		{"numeric_state", "123"},
	}
	for _, tc := range invalidStateCases {
		t.Run(tc.name, func(t *testing.T) {
			args := []string{"response-groups", "--state", tc.state}
			_, err := run(ctx, args, strings.NewReader(""))
			if core.Code(err) != "INVALID_REQUEST" {
				t.Fatalf("%s: expected INVALID_REQUEST, got: %v", tc.name, err)
			}
			if !strings.Contains(err.Error(), "unknown response group state") && !strings.Contains(err.Error(), "must be open, collected, partial, or incomplete") {
				t.Fatalf("%s: expected state error message, got: %v", tc.name, err)
			}
		})
	}

	// 5. response-groups unsupported flags.
	unsupportedGroupsFlags := []struct {
		name string
		args []string
	}{
		{"to_flag", []string{"response-groups", "--to", "agent:alice"}},
		{"request_id_flag", []string{"response-groups", "--request-id", uuid.NewString()}},
		{"response_deadline_flag", []string{"response-groups", "--response-deadline", "2026-09-16T12:00:00Z"}},
	}
	for _, tc := range unsupportedGroupsFlags {
		t.Run("groups_"+tc.name, func(t *testing.T) {
			_, err := run(ctx, tc.args, strings.NewReader(""))
			if core.Code(err) != "INVALID_REQUEST" {
				t.Fatalf("%s: expected INVALID_REQUEST, got: %v", tc.name, err)
			}
			if !strings.Contains(err.Error(), "is not supported by response-groups") {
				t.Fatalf("%s: expected 'is not supported by response-groups', got: %v", tc.name, err)
			}
		})
	}
}

func TestResponseGroupsRequestWiring(t *testing.T) {
	ctx := context.Background()

	root, err := os.MkdirTemp("/tmp", "cairn-resp-groups-")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		_ = os.RemoveAll(root)
	})

	socketPath := filepath.Join(root, "api.sock")
	tokenPath := filepath.Join(root, "agent.token")
	token := "test-agent-token"
	if err := os.WriteFile(tokenPath, []byte(token), 0600); err != nil {
		t.Fatal(err)
	}

	listener, err := net.Listen("unix", socketPath)
	if err != nil {
		t.Fatal(err)
	}

	var mu sync.Mutex
	var lastPublish core.PublishEventRequest
	var lastGroupQuery core.ResponseGroupQuery
	var lastGroupsList core.ResponseGroupListRequest
	var lastPath string
	var callCounts = make(map[string]int)

	server := &http.Server{
		Handler: http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			if r.Header.Get("Authorization") != "Bearer "+token {
				w.WriteHeader(http.StatusForbidden)
				return
			}
			mu.Lock()
			lastPath = r.URL.Path
			callCounts[r.URL.Path]++
			mu.Unlock()

			switch r.URL.Path {
			case "/v1/event-publish":
				var req core.PublishEventRequest
				if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
					t.Errorf("decode publish: %v", err)
					w.WriteHeader(http.StatusBadRequest)
					return
				}
				mu.Lock()
				lastPublish = req
				mu.Unlock()

				resp := map[string]any{
					"schema": "cairn.response/1",
					"ok":     true,
					"status": "OK",
					"data": map[string]any{
						"event_id":   uuid.NewString(),
						"position":   1,
						"repo":       req.Repo,
						"from":       "agent:test-agent",
						"created_at": time.Now().UTC().Format(time.RFC3339Nano),
						"kind":       req.Kind,
					},
				}
				_ = json.NewEncoder(w).Encode(resp)

			case "/v1/event-group":
				var req core.ResponseGroupQuery
				if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
					t.Errorf("decode group query: %v", err)
					w.WriteHeader(http.StatusBadRequest)
					return
				}
				mu.Lock()
				lastGroupQuery = req
				mu.Unlock()

				resp := map[string]any{
					"schema": "cairn.response/1",
					"ok":     true,
					"status": "OK",
					"data": map[string]any{
						"event_id":       req.EventID,
						"position":       42,
						"correlation_id": uuid.NewString(),
						"deadline":       time.Now().Add(time.Hour).UTC().Format(time.RFC3339),
						"partial_policy": "all",
						"state":          "open",
						"expected":       2,
						"responded":      1,
						"overdue":        false,
						"members":        []any{},
						"observations":   []any{},
						"more":           false,
					},
				}
				_ = json.NewEncoder(w).Encode(resp)

			case "/v1/event-groups":
				var req core.ResponseGroupListRequest
				if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
					t.Errorf("decode groups list: %v", err)
					w.WriteHeader(http.StatusBadRequest)
					return
				}
				mu.Lock()
				lastGroupsList = req
				mu.Unlock()

				resp := map[string]any{
					"schema": "cairn.response/1",
					"ok":     true,
					"status": "OK",
					"data": map[string]any{
						"groups":     []any{},
						"more":       false,
						"next_after": req.After,
					},
				}
				_ = json.NewEncoder(w).Encode(resp)

			default:
				http.NotFound(w, r)
			}
		}),
	}

	go func() {
		_ = server.Serve(listener)
	}()
	t.Cleanup(func() {
		_ = server.Close()
	})

	recordID := uuid.NewString()
	connArgs := []string{"--socket", socketPath, "--token-file", tokenPath}

	// 1. Publish with response group flags (policy=all).
	deadlineStr := "2026-09-16T18:00:00Z"
	wantDeadline, _ := time.Parse(time.RFC3339, deadlineStr)
	reqID := uuid.NewString()

	args := append(append([]string{"publish"}, connArgs...),
		"--request-id", reqID,
		"--to", "agent:worker",
		"--kind", "request",
		"--version", "1",
		"--response-deadline", deadlineStr,
		"--response-policy", "all",
		recordID,
	)

	_, err = run(ctx, args, strings.NewReader(""))
	if err != nil {
		t.Fatalf("publish with response group failed: %v", err)
	}

	mu.Lock()
	if lastPublish.ResponseGroup == nil {
		t.Fatal("expected ResponseGroup to be non-nil")
	}
	if !lastPublish.ResponseGroup.Deadline.Equal(wantDeadline) {
		t.Fatalf("deadline mismatch: got %v, want %v", lastPublish.ResponseGroup.Deadline, wantDeadline)
	}
	if lastPublish.ResponseGroup.PartialPolicy != "all" {
		t.Fatalf("policy mismatch: got %v, want all", lastPublish.ResponseGroup.PartialPolicy)
	}
	mu.Unlock()

	// 2. Publish with response group flags (policy=partial) and nanoseconds.
	deadlineNanoStr := "2026-09-16T18:30:00.987654321Z"
	wantDeadlineNano, _ := time.Parse(time.RFC3339Nano, deadlineNanoStr)
	reqID = uuid.NewString()

	args = append(append([]string{"publish"}, connArgs...),
		"--request-id", reqID,
		"--to", "agent:worker",
		"--kind", "request",
		"--version", "1",
		"--response-deadline", deadlineNanoStr,
		"--response-policy", "partial",
		recordID,
	)

	_, err = run(ctx, args, strings.NewReader(""))
	if err != nil {
		t.Fatalf("publish with partial policy failed: %v", err)
	}

	mu.Lock()
	if lastPublish.ResponseGroup == nil {
		t.Fatal("expected ResponseGroup to be non-nil")
	}
	if !lastPublish.ResponseGroup.Deadline.Equal(wantDeadlineNano) {
		t.Fatalf("deadline mismatch: got %v, want %v", lastPublish.ResponseGroup.Deadline, wantDeadlineNano)
	}
	if lastPublish.ResponseGroup.Deadline.Nanosecond() != wantDeadlineNano.Nanosecond() {
		t.Fatalf("nanoseconds lost: got %d, want %d", lastPublish.ResponseGroup.Deadline.Nanosecond(), wantDeadlineNano.Nanosecond())
	}
	if lastPublish.ResponseGroup.PartialPolicy != "partial" {
		t.Fatalf("policy mismatch: got %v, want partial", lastPublish.ResponseGroup.PartialPolicy)
	}
	mu.Unlock()

	// 3. Normal publish without response group flags leaves ResponseGroup nil.
	reqID = uuid.NewString()
	args = append(append([]string{"publish"}, connArgs...),
		"--request-id", reqID,
		"--to", "agent:worker",
		"--kind", "request",
		"--version", "1",
		recordID,
	)

	_, err = run(ctx, args, strings.NewReader(""))
	if err != nil {
		t.Fatalf("normal publish failed: %v", err)
	}

	mu.Lock()
	if lastPublish.ResponseGroup != nil {
		t.Fatalf("expected nil ResponseGroup, got %v", lastPublish.ResponseGroup)
	}
	mu.Unlock()

	// 4. response-group friendly command maps to /v1/event-group with ResponseGroupQuery.
	eventUUID := uuid.NewString()
	args = append(append([]string{"response-group"}, connArgs...),
		"--after", "50",
		"--limit", "10",
		"cairn:"+eventUUID,
	)

	_, err = run(ctx, args, strings.NewReader(""))
	if err != nil {
		t.Fatalf("response-group failed: %v", err)
	}

	mu.Lock()
	if lastPath != "/v1/event-group" {
		t.Fatalf("expected path /v1/event-group, got %s", lastPath)
	}
	if lastGroupQuery.EventID != eventUUID {
		t.Fatalf("event ID mismatch: got %q, want %q", lastGroupQuery.EventID, eventUUID)
	}
	if lastGroupQuery.After != 50 {
		t.Fatalf("after mismatch: got %d, want 50", lastGroupQuery.After)
	}
	if lastGroupQuery.Limit != 10 {
		t.Fatalf("limit mismatch: got %d, want 10", lastGroupQuery.Limit)
	}
	mu.Unlock()

	// 5. agent response-group invocation.
	args = append(append([]string{"agent"}, connArgs...),
		"response-group",
		eventUUID,
	)

	_, err = run(ctx, args, strings.NewReader(""))
	if err != nil {
		t.Fatalf("agent response-group failed: %v", err)
	}

	mu.Lock()
	if lastPath != "/v1/event-group" {
		t.Fatalf("expected path /v1/event-group, got %s", lastPath)
	}
	if lastGroupQuery.EventID != eventUUID {
		t.Fatalf("event ID mismatch: got %q, want %q", lastGroupQuery.EventID, eventUUID)
	}
	if lastGroupQuery.After != 0 || lastGroupQuery.Limit != 0 {
		t.Fatalf("expected default after/limit, got after=%d limit=%d", lastGroupQuery.After, lastGroupQuery.Limit)
	}
	mu.Unlock()

	// 6. response-groups friendly command maps to /v1/event-groups with ResponseGroupListRequest.
	args = append(append([]string{"response-groups"}, connArgs...),
		"--repo", "/test/repo",
		"--state", "open",
		"--after", "100",
		"--limit", "25",
	)

	_, err = run(ctx, args, strings.NewReader(""))
	if err != nil {
		t.Fatalf("response-groups failed: %v", err)
	}

	mu.Lock()
	if lastPath != "/v1/event-groups" {
		t.Fatalf("expected path /v1/event-groups, got %s", lastPath)
	}
	if lastGroupsList.Repo != "/test/repo" {
		t.Fatalf("repo mismatch: got %q, want /test/repo", lastGroupsList.Repo)
	}
	if lastGroupsList.State != "open" {
		t.Fatalf("state mismatch: got %q, want open", lastGroupsList.State)
	}
	if lastGroupsList.After != 100 {
		t.Fatalf("after mismatch: got %d, want 100", lastGroupsList.After)
	}
	if lastGroupsList.Limit != 25 {
		t.Fatalf("limit mismatch: got %d, want 25", lastGroupsList.Limit)
	}
	mu.Unlock()

	// 7. agent response-groups invocation with state filter.
	for _, st := range []string{"collected", "partial", "incomplete"} {
		args = append(append([]string{"agent"}, connArgs...),
			"response-groups",
			"--state", st,
		)

		_, err = run(ctx, args, strings.NewReader(""))
		if err != nil {
			t.Fatalf("agent response-groups state=%s failed: %v", st, err)
		}

		mu.Lock()
		if lastGroupsList.State != st {
			t.Fatalf("state mismatch: got %q, want %q", lastGroupsList.State, st)
		}
		mu.Unlock()
	}

	// 8. Raw agent operations event-group and event-groups with JSON stdin.
	rawGroupInput := `{"event_id":"` + eventUUID + `","after":15,"limit":3}`
	args = append(append([]string{"agent"}, connArgs...), "event-group")

	_, err = run(ctx, args, strings.NewReader(rawGroupInput))
	if err != nil {
		t.Fatalf("raw agent event-group failed: %v", err)
	}

	mu.Lock()
	if lastPath != "/v1/event-group" {
		t.Fatalf("expected path /v1/event-group, got %s", lastPath)
	}
	if lastGroupQuery.EventID != eventUUID {
		t.Fatalf("raw event ID mismatch: got %q, want %q", lastGroupQuery.EventID, eventUUID)
	}
	if lastGroupQuery.After != 15 || lastGroupQuery.Limit != 3 {
		t.Fatalf("raw after/limit mismatch: after=%d limit=%d", lastGroupQuery.After, lastGroupQuery.Limit)
	}
	mu.Unlock()

	rawGroupsInput := `{"repo":"/custom/repo","state":"partial","after":40,"limit":7}`
	args = append(append([]string{"agent"}, connArgs...), "event-groups")

	_, err = run(ctx, args, strings.NewReader(rawGroupsInput))
	if err != nil {
		t.Fatalf("raw agent event-groups failed: %v", err)
	}

	mu.Lock()
	if lastPath != "/v1/event-groups" {
		t.Fatalf("expected path /v1/event-groups, got %s", lastPath)
	}
	if lastGroupsList.Repo != "/custom/repo" {
		t.Fatalf("raw repo mismatch: got %q, want /custom/repo", lastGroupsList.Repo)
	}
	if lastGroupsList.State != "partial" {
		t.Fatalf("raw state mismatch: got %q, want partial", lastGroupsList.State)
	}
	if lastGroupsList.After != 40 || lastGroupsList.Limit != 7 {
		t.Fatalf("raw after/limit mismatch: after=%d limit=%d", lastGroupsList.After, lastGroupsList.Limit)
	}
	mu.Unlock()
}
