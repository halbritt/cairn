package localapi

import (
	"net/http/httptest"
	"strings"
	"testing"
)

func TestEveryAPIRouteHasRemoteClassification(t *testing.T) {
	catalog := Operations()
	if len(catalog) == 0 {
		t.Fatal("API route inventory was empty")
	}
	mux := (&Server{}).NewHandler()
	routes := map[string]bool{}
	for _, spec := range catalog {
		if spec.Name == "" || spec.Method != "POST" || spec.Path != "/v1/"+spec.Name {
			t.Fatalf("malformed operation spec: %+v", spec)
		}
		if routes[spec.Name] {
			t.Fatalf("duplicate operation %q in catalog", spec.Name)
		}
		routes[spec.Name] = true
		if _, ok := remoteOperations[spec.Name]; !ok {
			t.Errorf("route %q needs an explicit remote decision", spec.Name)
		}
		if allowed, classified := RemoteOperation(spec.Name); !classified || allowed != spec.RemoteAllowed {
			t.Errorf("route %q remote classification mismatch: got (%v, %v), want (%v, true)", spec.Name, allowed, classified, spec.RemoteAllowed)
		}
		if limit := RequestBodyLimit(spec.Name); limit <= 0 || limit != spec.RequestBodyLimit || limit > MaxRequestBodyLimit {
			t.Errorf("route %q invalid request body limit %d", spec.Name, limit)
		}
		if spec.RequiresLocalDestination != (spec.LocalDestinationMessage != "") {
			t.Errorf("route %q local destination guard mismatch: RequiresLocalDestination=%v message=%q", spec.Name, spec.RequiresLocalDestination, spec.LocalDestinationMessage)
		}
		if spec.RequiresLocalDestination && !strings.Contains(spec.LocalDestinationMessage, "a local profile") {
			t.Errorf("route %q local destination guard message %q does not mention a local profile", spec.Name, spec.LocalDestinationMessage)
		}
		req := httptest.NewRequest(spec.Method, spec.Path, nil)
		if _, pattern := mux.Handler(req); pattern != spec.Method+" "+spec.Path {
			t.Errorf("NewHandler pattern for %s %s = %q, want %q", spec.Method, spec.Path, pattern, spec.Method+" "+spec.Path)
		}
	}
	for route := range remoteOperations {
		if !routes[route] {
			t.Errorf("classification %q has no API route", route)
		}
	}
}
