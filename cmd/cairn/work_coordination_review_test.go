package main

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
	"github.com/halbritt/cairn/internal/plane"
)

func TestWorkCoordinationHTTPReviewUsesAuthenticatedBoundOperator(t *testing.T) {
	dsn := os.Getenv("CAIRN_TEST_DATABASE_URL")
	if dsn == "" {
		t.Skip("PostgreSQL integration test: run make test-integration")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()
	repo := "fixture:coordination-http:" + uuid.NewString()
	store, err := core.Open(ctx, dsn, core.Channel{Principal: "operator:coordination-http", Operator: true, Repo: repo})
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	if err := store.Migrate(ctx); err != nil {
		t.Fatal(err)
	}
	for _, feature := range []string{"work-view/1", "coordination-waits/1"} {
		if err := store.SetWorkFeatureStatus(ctx, core.SetWorkFeatureStatusRequest{RequestID: uuid.NewString(), FeatureName: feature, Status: "active"}); err != nil {
			t.Fatal(err)
		}
	}
	project, err := store.AddWorkProject(ctx, core.AddWorkProjectRequest{
		RequestID: uuid.NewString(), Collection: repo, LocalSlug: "http-review",
		PlaneInstance: "work", PlaneOrigin: "https://plane.example.com", WorkspaceUUID: uuid.New(), ProjectUUID: uuid.New(), ConfigRevision: "review-v1",
	})
	if err != nil {
		t.Fatal(err)
	}
	item := uuid.New()
	refresh, err := store.PrepareRefresh(ctx, repo, project.LocalSlug)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := store.CommitRefreshSnapshot(ctx, refresh, []plane.NormalizedItem{{PlaneItemUUID: item, Identifier: "HTTP-1", Title: "Synthetic selected item"}}, false, nil); err != nil {
		t.Fatal(err)
	}
	profile, err := core.Open(ctx, dsn, core.Channel{Principal: "profile:" + repo, Repo: repo})
	if err != nil {
		t.Fatal(err)
	}
	defer profile.Close()
	dest := core.Destination{Name: "hosted", AllowLocal: false}
	agents := make([]core.AgentInstance, 2)
	for i := range agents {
		agents[i], err = profile.RegisterAgent(ctx, core.RegisterAgentRequest{
			RequestID: uuid.NewString(), Repo: repo, Binding: "http-review", NativeSessionID: uuid.NewString(),
			Metadata: core.AgentMetadata{Harness: "codex", Project: "review", Workspace: "/review", DeliveryMode: "existing-session", State: "idle"},
		}, dest)
		if err != nil {
			t.Fatal(err)
		}
	}
	session, err := profile.ForAgentSession(core.AgentSessionRef{AgentID: agents[0].AgentID, ExecutionID: agents[0].ExecutionID}, dest)
	if err != nil {
		t.Fatal(err)
	}
	source, err := session.Create(ctx, core.CreateRequest{RequestID: uuid.NewString(), Draft: core.Draft{
		Kind: "note", Body: "PRIVATE_HTTP_DEPENDENCY_BODY", Sensitivity: "shareable", ClaimType: "self",
		Scope: core.Scope{Repo: repo, TaskID: "*", RunID: "*"},
	}})
	if err != nil {
		t.Fatal(err)
	}
	event, err := session.PublishEvent(ctx, core.PublishEventRequest{
		RequestID: uuid.NewString(), Repo: repo, Kind: "request", Ref: core.RecordVersionRef{RecordID: source.RecordID, Version: source.Version},
		Destination: core.EventDestination{Type: "agent", Name: "agent/" + agents[1].AgentID},
	}, dest)
	if err != nil {
		t.Fatal(err)
	}
	wait, err := session.CreateWait(ctx, core.CreateWaitRequest{
		RequestID: uuid.NewString(), Collection: repo, PredicateKind: core.WaitPredicateRequestResponse,
		DependencyEventUUID: event.EventID, Deadline: time.Now().Add(time.Minute),
	}, dest)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := store.AssociateWork(ctx, core.AssociateWorkRequest{
		RequestID: uuid.NewString(), Collection: repo, ProjectSlug: project.LocalSlug, ItemUUID: item,
		TargetType: "event", TargetID: event.EventID, Rationale: "PRIVATE_HTTP_ASSOCIATION_RATIONALE",
	}); err != nil {
		t.Fatal(err)
	}
	srv, err := newWorkServer(store, repo, project.LocalSlug, 8085)
	if err != nil {
		t.Fatal(err)
	}
	loginBody, err := json.Marshal(map[string]string{"code": srv.launchCode})
	if err != nil {
		t.Fatal(err)
	}
	login := httptest.NewRequest(http.MethodPost, "http://127.0.0.1:8085/api/auth/login", bytes.NewReader(loginBody))
	login.Header.Set("Content-Type", "application/json")
	login.Header.Set("Origin", "http://127.0.0.1:8085")
	loginResponse := httptest.NewRecorder()
	srv.ServeHTTP(loginResponse, login)
	if loginResponse.Code != http.StatusOK {
		t.Fatalf("real launch-code login failed: %d", loginResponse.Code)
	}
	var cookie *http.Cookie
	for _, candidate := range loginResponse.Result().Cookies() {
		if candidate.Name == "work_session" {
			cookie = candidate
		}
	}
	if cookie == nil {
		t.Fatal("login issued no session cookie")
	}
	path := "/api/work/item/coordination?project=" + url.QueryEscape(project.LocalSlug) + "&item=" + item.String()
	request := func(path, host, origin string, authenticated bool) *httptest.ResponseRecorder {
		t.Helper()
		r := httptest.NewRequest(http.MethodGet, "http://127.0.0.1:8085"+path, nil)
		r.Host = host
		if origin != "" {
			r.Header.Set("Origin", origin)
		}
		if authenticated {
			r.AddCookie(cookie)
		}
		w := httptest.NewRecorder()
		srv.ServeHTTP(w, r)
		return w
	}
	good := request(path, "127.0.0.1:8085", "http://127.0.0.1:8085", true)
	var view core.WorkItemCoordination
	if good.Code != http.StatusOK || json.Unmarshal(good.Body.Bytes(), &view) != nil || good.Header().Get("Cache-Control") != "no-store" {
		t.Fatalf("authenticated coordination GET: status=%d body=%s", good.Code, good.Body.String())
	}
	if view.Collection != repo || view.BindingID != project.BindingID || view.PlaneItemUUID != item || len(view.Waits.Items) != 1 || view.Waits.Items[0].WaitUUID.String() != wait.WaitUUID {
		t.Fatalf("HTTP route lost its bound Store/item membership: %+v", view)
	}
	for _, forbidden := range []string{source.RecordID, source.Body, "PRIVATE_HTTP_ASSOCIATION_RATIONALE", `"response_ref"`, `"profile_binding_id"`} {
		if strings.Contains(good.Body.String(), forbidden) {
			t.Errorf("HTTP response leaked forbidden source/profile field %q", forbidden)
		}
	}
	for _, tc := range []struct {
		name, suffix, host, origin string
		auth                       bool
		status                     int
	}{
		{"missing_cookie", "", "127.0.0.1:8085", "", false, http.StatusUnauthorized},
		{"wrong_host", "", "evil.example:8085", "", true, http.StatusForbidden},
		{"wrong_origin", "", "127.0.0.1:8085", "http://evil.example:8085", true, http.StatusForbidden},
		{"collection_override", "&collection=another-repo", "127.0.0.1:8085", "", true, http.StatusBadRequest},
		{"agent_override", "&agent_id=" + agents[1].AgentID, "127.0.0.1:8085", "", true, http.StatusBadRequest},
		{"duplicate_item", "&item=" + uuid.NewString(), "127.0.0.1:8085", "", true, http.StatusBadRequest},
		{"malformed_cursor", "&wait_cursor=invalid", "127.0.0.1:8085", "", true, http.StatusBadRequest},
	} {
		t.Run(tc.name, func(t *testing.T) {
			got := request(path+tc.suffix, tc.host, tc.origin, tc.auth)
			if got.Code != tc.status {
				t.Fatalf("want HTTP %d, got %d: %s", tc.status, got.Code, got.Body.String())
			}
			if strings.Contains(got.Body.String(), wait.WaitUUID) {
				t.Fatal("refused request retained prior coordination rows")
			}
		})
	}
}
