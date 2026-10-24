package main

import (
	"context"
	"encoding/json"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"testing"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
)

func sendArgs(t *testing.T, handler http.HandlerFunc) ([]string, string) {
	t.Helper()
	connection := agentCaptureAPI(t, handler)
	journal := filepath.Join(t.TempDir(), "send.json")
	if err := os.Chmod(filepath.Dir(journal), 0700); err != nil {
		t.Fatal(err)
	}
	return append(connection, "send", "--journal", journal, "--repo", "fixture:send", "--stdin", "--kind", "request", "--shareable", "--to", "agent/"+uuid.NewString()), journal
}

func sendAPIResponse(w http.ResponseWriter, status int, data any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(map[string]any{"schema": "cairn.response/1", "ok": status == http.StatusOK,
		"status":  map[bool]string{true: "OK", false: "API_CONNECTION_FAILED"}[status == http.StatusOK],
		"message": "selected fixture refusal", "data": data})
}

func sendAPIRefusal(w http.ResponseWriter, code string) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusForbidden)
	_ = json.NewEncoder(w).Encode(map[string]any{"schema": "cairn.response/1", "ok": false,
		"status": code, "message": "selected fixture refusal"})
}

func TestAgentSendRetryUsesOneSourceAndOnePublication(t *testing.T) {
	recordID, eventID := uuid.NewString(), uuid.NewString()
	var mu sync.Mutex
	var creates []core.CreateRequest
	var publications []core.PublishEventRequest
	snapshot := func() ([]core.CreateRequest, []core.PublishEventRequest) {
		mu.Lock()
		defer mu.Unlock()
		return append([]core.CreateRequest(nil), creates...), append([]core.PublishEventRequest(nil), publications...)
	}
	args, journalPath := sendArgs(t, func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/v1/create":
			var request core.CreateRequest
			if err := json.NewDecoder(r.Body).Decode(&request); err != nil {
				t.Error(err)
			}
			mu.Lock()
			creates = append(creates, request)
			mu.Unlock()
			sendAPIResponse(w, http.StatusOK, core.Record{RecordID: recordID, Version: 3, Sensitivity: request.Draft.Sensitivity, Draft: request.Draft})
		case "/v1/event-publish":
			var request core.PublishEventRequest
			if err := json.NewDecoder(r.Body).Decode(&request); err != nil {
				t.Error(err)
			}
			mu.Lock()
			publications = append(publications, request)
			count := len(publications)
			mu.Unlock()
			if count == 1 {
				connection, _, err := w.(http.Hijacker).Hijack()
				if err != nil {
					t.Error(err)
					return
				}
				_ = connection.Close() // The selected publish reached the server, but its response was lost.
			} else {
				sendAPIResponse(w, http.StatusOK, core.AgentEvent{EventID: eventID, Repo: request.Repo, Kind: request.Kind, From: "agent:sender", Ref: request.Ref, Destination: request.Destination})
			}
		default:
			t.Errorf("unexpected API path %s", r.URL.Path)
		}
	})
	body := "Selected assignment 日本語.\n"
	first, err := run(context.Background(), args, strings.NewReader(body))
	if err == nil {
		t.Fatalf("first send: %v", err)
	}
	partial := first.(sendResult)
	if partial.SourceSaved == nil || !*partial.SourceSaved || !partial.PublicationPending || partial.Source.RecordID != recordID || partial.Source.Version != 3 {
		t.Fatalf("first send lost partial state: %+v", partial)
	}
	created, published := snapshot()
	if created[0].RequestID == published[0].RequestID || created[0].Draft.Scope.TaskID != "coordination/"+published[0].RequestID || created[0].Draft.Scope.RunID != "*" {
		t.Fatalf("source and publication identities or task scope diverged")
	}
	if published[0].Ref.RecordID != recordID || published[0].Ref.Version != 3 {
		t.Fatalf("publish used the wrong source: %+v", published[0].Ref)
	}
	second, err := run(context.Background(), args, strings.NewReader(body))
	if err != nil {
		t.Fatal(err)
	}
	complete := second.(sendResult)
	created, published = snapshot()
	if complete.PublicationPending || complete.EventID != eventID || len(created) != 1 || len(published) != 2 || published[0].RequestID != published[1].RequestID {
		t.Fatalf("retry duplicated source or changed event intent: %+v creates=%d publishes=%d", complete, len(created), len(published))
	}
	_, err = run(context.Background(), args, strings.NewReader(body))
	_, published = snapshot()
	if err != nil || len(published) != 2 {
		t.Fatalf("confirmed send repeated publication: %v", err)
	}
	if _, err := run(context.Background(), args, strings.NewReader(body+"changed")); core.Code(err) != "IDEMPOTENCY_CONFLICT" {
		t.Fatalf("changed source reused journal: %v", err)
	}
	journalBytes, err := os.ReadFile(journalPath)
	if err != nil {
		t.Fatal(err)
	}
	if strings.Contains(string(journalBytes), body) {
		t.Fatal("journal retained assignment text")
	}
	if strings.Contains(string(journalBytes), "capture-test-token") {
		t.Fatal("journal retained API credential")
	}
	if info, err := os.Stat(journalPath); err != nil || info.Mode().Perm() != 0600 {
		t.Fatalf("journal permissions: %v, %v", info, err)
	}
	for i, arg := range args {
		if arg == "--token-file" {
			if err := os.WriteFile(args[i+1], []byte("different-token"), 0600); err != nil {
				t.Fatal(err)
			}
			break
		}
	}
	_, err = run(context.Background(), args, strings.NewReader(body))
	_, published = snapshot()
	if core.Code(err) != "IDEMPOTENCY_CONFLICT" || len(published) != 2 {
		t.Fatalf("different API identity reused journal: %v", err)
	}
}

func TestAgentSendRetriesUncertainSourceWithSameIdentity(t *testing.T) {
	var mu sync.Mutex
	var creates []core.CreateRequest
	recordID, eventID := uuid.NewString(), uuid.NewString()
	args, _ := sendArgs(t, func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/v1/create":
			var request core.CreateRequest
			if err := json.NewDecoder(r.Body).Decode(&request); err != nil {
				t.Error(err)
			}
			mu.Lock()
			creates = append(creates, request)
			count := len(creates)
			mu.Unlock()
			if count == 1 {
				sendAPIResponse(w, http.StatusServiceUnavailable, nil)
			} else {
				sendAPIResponse(w, http.StatusOK, core.Record{RecordID: recordID, Version: 1, Sensitivity: request.Draft.Sensitivity, Draft: request.Draft})
			}
		case "/v1/event-publish":
			var request core.PublishEventRequest
			if err := json.NewDecoder(r.Body).Decode(&request); err != nil {
				t.Error(err)
			}
			sendAPIResponse(w, http.StatusOK, core.AgentEvent{EventID: eventID, Repo: request.Repo, Kind: request.Kind, From: "agent:sender", Ref: request.Ref, Destination: request.Destination})
		default:
			t.Errorf("unexpected API path %s", r.URL.Path)
		}
	})
	first, err := run(context.Background(), args, strings.NewReader("selected"))
	if core.Code(err) != "API_CONNECTION_FAILED" || first.(sendResult).SourceSaved != nil {
		t.Fatalf("uncertain create appeared confirmed: %+v %v", first, err)
	}
	second, err := run(context.Background(), args, strings.NewReader("selected"))
	mu.Lock()
	created := append([]core.CreateRequest(nil), creates...)
	mu.Unlock()
	if err != nil || second.(sendResult).EventID != eventID || len(created) != 2 || created[0].RequestID != created[1].RequestID {
		t.Fatalf("create retry changed identity: %+v %v", second, err)
	}
}

func TestAgentSendRequiresExplicitTargetBeforeCreatingJournal(t *testing.T) {
	var called atomic.Bool
	args, journalPath := sendArgs(t, func(w http.ResponseWriter, r *http.Request) { called.Store(true) })
	args = args[:len(args)-2]
	_, err := run(context.Background(), args, strings.NewReader("selected"))
	if core.Code(err) != "INVALID_REQUEST" || called.Load() {
		t.Fatalf("implicit destination reached API: %v", err)
	}
	if _, err := os.Stat(journalPath); !os.IsNotExist(err) {
		t.Fatalf("invalid intent created journal: %v", err)
	}
}

func TestAgentSendPreservesLocalSensitivityOnPublicationRefusal(t *testing.T) {
	var mu sync.Mutex
	var created core.CreateRequest
	var published core.PublishEventRequest
	recordID := uuid.NewString()
	args, _ := sendArgs(t, func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/v1/create":
			var request core.CreateRequest
			if err := json.NewDecoder(r.Body).Decode(&request); err != nil {
				t.Error(err)
			}
			mu.Lock()
			created = request
			mu.Unlock()
			sendAPIResponse(w, http.StatusOK, core.Record{RecordID: recordID, Version: 1, Sensitivity: request.Draft.Sensitivity, Draft: request.Draft})
		case "/v1/event-publish":
			var request core.PublishEventRequest
			if err := json.NewDecoder(r.Body).Decode(&request); err != nil {
				t.Error(err)
			}
			mu.Lock()
			published = request
			mu.Unlock()
			sendAPIRefusal(w, "DESTINATION_PROHIBITED")
		default:
			t.Errorf("unexpected API path %s", r.URL.Path)
		}
	})
	for i, arg := range args {
		if arg == "--shareable" {
			args = append(args[:i], args[i+1:]...)
			break
		}
	}
	result, err := run(context.Background(), args, strings.NewReader("local selected assignment"))
	if core.Code(err) != "DESTINATION_PROHIBITED" {
		t.Fatalf("publication refusal: %v", err)
	}
	partial := result.(sendResult)
	mu.Lock()
	createdCopy, publishedCopy := created, published
	mu.Unlock()
	if partial.SourceSaved == nil || !*partial.SourceSaved || !partial.PublicationPending || createdCopy.Draft.Sensitivity != "local" || publishedCopy.Ref.RecordID != recordID {
		t.Fatalf("local source silently widened or partial state lost: %+v", partial)
	}
}

func TestAgentSendRefusesAmbiguousResolutionBeforeSourceSave(t *testing.T) {
	var called atomic.Bool
	args, journal := sendArgs(t, func(w http.ResponseWriter, r *http.Request) { called.Store(true) })
	resolution := filepath.Join(t.TempDir(), "resolution.json")
	if err := os.WriteFile(resolution, []byte(`{"state":"ambiguous","candidates":[{}]}`), 0600); err != nil {
		t.Fatal(err)
	}
	args = append(args[:len(args)-2], "--resolution", resolution)
	_, err := run(context.Background(), args, strings.NewReader("selected"))
	if core.Code(err) != "INVALID_REQUEST" || called.Load() {
		t.Fatalf("ambiguous resolution reached API: %v", err)
	}
	if _, err := os.Stat(journal); !os.IsNotExist(err) {
		t.Fatalf("ambiguous resolution created journal: %v", err)
	}
}

func TestAgentSendKeepsExactSourceOnStaleResolutionOrForgottenSource(t *testing.T) {
	for _, code := range []string{"STALE_RESOLUTION", "NOT_FOUND"} {
		t.Run(code, func(t *testing.T) {
			recordID := uuid.NewString()
			var mu sync.Mutex
			var published core.PublishEventRequest
			args, _ := sendArgs(t, func(w http.ResponseWriter, r *http.Request) {
				switch r.URL.Path {
				case "/v1/create":
					var request core.CreateRequest
					if err := json.NewDecoder(r.Body).Decode(&request); err != nil {
						t.Error(err)
					}
					sendAPIResponse(w, http.StatusOK, core.Record{RecordID: recordID, Version: 2, Sensitivity: request.Draft.Sensitivity, Draft: request.Draft})
				case "/v1/event-publish":
					var request core.PublishEventRequest
					if err := json.NewDecoder(r.Body).Decode(&request); err != nil {
						t.Error(err)
					}
					mu.Lock()
					published = request
					mu.Unlock()
					sendAPIRefusal(w, code)
				default:
					t.Errorf("unexpected API path %s", r.URL.Path)
				}
			})
			if code == "STALE_RESOLUTION" {
				resolution := core.AgentResolution{AgentID: uuid.NewString(), ExecutionID: uuid.NewString(), Repo: "fixture:send", DatabaseGeneration: 1, ContextRevision: 1}
				path := filepath.Join(t.TempDir(), "resolution.json")
				encoded, err := json.Marshal(resolution)
				if err != nil {
					t.Fatal(err)
				}
				if err := os.WriteFile(path, encoded, 0600); err != nil {
					t.Fatal(err)
				}
				args = append(args[:len(args)-2], "--resolution", path)
			}
			result, err := run(context.Background(), args, strings.NewReader("selected"))
			if core.Code(err) != code {
				t.Fatalf("publish refusal: %v", err)
			}
			partial := result.(sendResult)
			mu.Lock()
			publishedCopy := published
			mu.Unlock()
			if partial.SourceSaved == nil || !*partial.SourceSaved || !partial.PublicationPending || partial.Source.RecordID != recordID || partial.Source.Version != 2 || publishedCopy.Ref != *partial.Source {
				t.Fatalf("publish refusal lost exact source: %+v", partial)
			}
			if code == "STALE_RESOLUTION" && publishedCopy.Resolution == nil {
				t.Fatal("freshness check lost selected resolution")
			}
		})
	}
}
