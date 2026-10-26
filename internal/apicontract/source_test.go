package apicontract

import (
	"net/http/httptest"
	"testing"

	"github.com/halbritt/cairn/localapi"
)

func TestLoadedSourceMatchesOperationCatalog(t *testing.T) {
	source, err := Load("../../localapi")
	if err != nil {
		t.Fatal(err)
	}
	catalog := localapi.Operations()
	if len(source.Routes) != len(catalog) {
		t.Fatalf("loaded %d routes, catalog has %d", len(source.Routes), len(catalog))
	}
	byName := map[string]Route{}
	for _, route := range source.Routes {
		if len(route.Operations) != 1 {
			t.Fatalf("expected one operation per declarative route, got %v", route.Operations)
		}
		byName[route.Operations[0]] = route
	}
	for _, spec := range catalog {
		route, ok := byName[spec.Name]
		if !ok {
			t.Fatalf("catalog operation %q missing from loaded source", spec.Name)
		}
		if route.RequiresLocalDestination != spec.RequiresLocalDestination {
			t.Errorf("%s RequiresLocalDestination = %v, want %v", spec.Name, route.RequiresLocalDestination, spec.RequiresLocalDestination)
		}
		if route.File != spec.SourceFile {
			t.Errorf("%s File = %q, want %q", spec.Name, route.File, spec.SourceFile)
		}
		if route.Request == nil || route.Response == nil {
			t.Errorf("%s has nil request or response type", spec.Name)
		}
	}
}

func TestNewHandlerMountsCatalogRoutes(t *testing.T) {
	mux := (&localapi.Server{}).NewHandler()
	for _, spec := range localapi.Operations() {
		req := httptest.NewRequest(spec.Method, spec.Path, nil)
		if _, pattern := mux.Handler(req); pattern != spec.Method+" "+spec.Path {
			t.Errorf("NewHandler pattern for %s %s = %q, want %q", spec.Method, spec.Path, pattern, spec.Method+" "+spec.Path)
		}
	}
	if _, pattern := mux.Handler(httptest.NewRequest("POST", "/v1/unknown-operation", nil)); pattern != "" {
		t.Errorf("unexpected pattern %q for unmounted route", pattern)
	}
}

func TestRetryClassRejectsRequestIDMismatch(t *testing.T) {
	if class, ok := retryClass("create", map[string]bool{"request_id": true}); !ok || class != "request-id" {
		t.Fatalf("create with request_id = (%q, %v), want (\"request-id\", true)", class, ok)
	}
	if _, ok := retryClass("create", map[string]bool{}); ok {
		t.Fatal("create without request_id property should fail retry verification")
	}
	if _, ok := retryClass("version", map[string]bool{"request_id": true}); ok {
		t.Fatal("read operation with request_id property should fail retry verification")
	}
}
