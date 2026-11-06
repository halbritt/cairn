package main

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"testing"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
	"github.com/halbritt/cairn/internal/plane"
)

func TestWorkServerCoordinationEndpointContract(t *testing.T) {
	dsn := os.Getenv("CAIRN_TEST_DATABASE_URL")
	if dsn == "" {
		t.Skip("PostgreSQL integration test: run make test-integration")
	}
	ctx := context.Background()
	collection := "repo:http-coord:" + uuid.NewString()
	store, err := core.Open(ctx, dsn, core.Channel{Principal: "operator:http-test", Operator: true, Repo: collection})
	if err != nil {
		t.Fatalf("Open store: %v", err)
	}
	defer store.Close()
	if err := store.Migrate(ctx); err != nil {
		t.Fatalf("Migrate store: %v", err)
	}

	proj, err := store.AddWorkProject(ctx, core.AddWorkProjectRequest{
		RequestID: uuid.NewString(), Collection: collection, LocalSlug: "http-coord",
		PlaneInstance: "work", PlaneOrigin: "https://plane.example.com",
		WorkspaceUUID: uuid.New(), ProjectUUID: uuid.New(), ConfigRevision: "rev-http",
	})
	if err != nil {
		t.Fatal(err)
	}
	itemUUID := uuid.New()
	ref, err := store.PrepareRefresh(ctx, collection, proj.LocalSlug)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := store.CommitRefreshSnapshot(ctx, ref, []plane.NormalizedItem{
		{PlaneItemUUID: itemUUID, Identifier: "HTTP-1", Title: "HTTP Coord Item"},
	}, false, nil); err != nil {
		t.Fatal(err)
	}

	srv, err := newWorkServer(store, collection, proj.LocalSlug, 8085)
	if err != nil {
		t.Fatal(err)
	}

	// 1. Unauthenticated GET -> 401.
	reqUnauth := httptest.NewRequest(http.MethodGet, "/api/work/item/coordination?project=http-coord&item="+itemUUID.String(), nil)
	reqUnauth.Host = "127.0.0.1:8085"
	recUnauth := httptest.NewRecorder()
	srv.ServeHTTP(recUnauth, reqUnauth)
	if recUnauth.Code != http.StatusUnauthorized {
		t.Fatalf("expected 401 without session cookie, got %d", recUnauth.Code)
	}

	// Login with one-time launch code.
	loginBody, _ := json.Marshal(map[string]string{"code": srv.launchCode})
	reqLogin := httptest.NewRequest(http.MethodPost, "/api/auth/login", bytes.NewReader(loginBody))
	reqLogin.Host = "127.0.0.1:8085"
	recLogin := httptest.NewRecorder()
	srv.ServeHTTP(recLogin, reqLogin)
	if recLogin.Code != http.StatusOK {
		t.Fatalf("login failed: %d %s", recLogin.Code, recLogin.Body.String())
	}
	cookies := recLogin.Result().Cookies()
	csrf := recLogin.Header().Get("X-CSRF-Token")

	// 2. Non-GET -> 405 Method Not Allowed + Cache-Control: no-store.
	reqPost := httptest.NewRequest(http.MethodPost, "/api/work/item/coordination?project=http-coord&item="+itemUUID.String(), bytes.NewReader([]byte(`{}`)))
	reqPost.Host = "127.0.0.1:8085"
	reqPost.Header.Set("X-CSRF-Token", csrf)
	for _, c := range cookies {
		reqPost.AddCookie(c)
	}
	recPost := httptest.NewRecorder()
	srv.ServeHTTP(recPost, reqPost)
	if recPost.Code != http.StatusMethodNotAllowed || recPost.Header().Get("Cache-Control") != "no-store" {
		t.Fatalf("expected 405 + no-store on POST, got %d cc=%q", recPost.Code, recPost.Header().Get("Cache-Control"))
	}

	// 3. Forbidden collection/agent selector query param -> 400 INVALID_REQUEST.
	reqBadQuery := httptest.NewRequest(http.MethodGet, "/api/work/item/coordination?project=http-coord&item="+itemUUID.String()+"&collection=other", nil)
	reqBadQuery.Host = "127.0.0.1:8085"
	for _, c := range cookies {
		reqBadQuery.AddCookie(c)
	}
	recBadQuery := httptest.NewRecorder()
	srv.ServeHTTP(recBadQuery, reqBadQuery)
	if recBadQuery.Code != http.StatusBadRequest {
		t.Fatalf("expected 400 on unsupported collection query param, got %d: %s", recBadQuery.Code, recBadQuery.Body.String())
	}

	// 4. Valid GET -> 200 OK + Cache-Control: no-store + exact schema.
	reqOK := httptest.NewRequest(http.MethodGet, "/api/work/item/coordination?project=http-coord&item="+itemUUID.String()+"&limit=10", nil)
	reqOK.Host = "127.0.0.1:8085"
	for _, c := range cookies {
		reqOK.AddCookie(c)
	}
	recOK := httptest.NewRecorder()
	srv.ServeHTTP(recOK, reqOK)
	if recOK.Code != http.StatusOK || recOK.Header().Get("Cache-Control") != "no-store" {
		t.Fatalf("expected 200 + no-store, got %d cc=%q body=%s", recOK.Code, recOK.Header().Get("Cache-Control"), recOK.Body.String())
	}
	var view core.WorkItemCoordination
	if err := json.Unmarshal(recOK.Body.Bytes(), &view); err != nil {
		t.Fatalf("decode response: %v", err)
	}
	if view.Schema != core.WorkCoordinationViewSchema || view.Collection != collection || view.PlaneItemUUID != itemUUID {
		t.Fatalf("unexpected coordination view identity: %+v", view)
	}
}
