package localapi

import (
	"context"
	"net/http"
	"reflect"

	"github.com/halbritt/cairn/core"
)

// DefaultRequestBodyLimit is the encoded JSON body limit when an operation
// does not specify a custom limit.
const DefaultRequestBodyLimit int64 = 128 * 1024

// OperationSpec declaratively defines one localapi HTTP operation.
type OperationSpec struct {
	Name                     string
	Method                   string
	Path                     string
	SourceFile               string
	RequiresLocalDestination bool
	LocalDestinationMessage  string
	RemoteAllowed            bool
	RemoteObserverAllowed    bool
	RequestBodyLimit         int64
	RetryClass               string
	RequestType              reflect.Type
	ResponseType             reflect.Type
	serve                    func(*Server, http.ResponseWriter, *http.Request, client)
}

type clientContextKey struct{}

var (
	operationCatalog []OperationSpec
	operationsByName map[string]OperationSpec
	operationsByPath map[string]OperationSpec
)

func init() {
	all := make([]OperationSpec, 0, len(serverOperations)+len(agentSessionOperations)+len(agentEventOperations))
	all = append(all, serverOperations...)
	all = append(all, agentSessionOperations...)
	all = append(all, agentEventOperations...)
	operationCatalog = all
	operationsByName = make(map[string]OperationSpec, len(all))
	operationsByPath = make(map[string]OperationSpec, len(all))
	for _, spec := range all {
		if _, dup := operationsByName[spec.Name]; dup {
			panic("duplicate localapi operation: " + spec.Name)
		}
		if _, dup := operationsByPath[spec.Path]; dup {
			panic("duplicate localapi path: " + spec.Path)
		}
		operationsByName[spec.Name] = spec
		operationsByPath[spec.Path] = spec
		remoteOperations[spec.Name] = spec.RemoteAllowed
	}
}

// Operations returns a copy of the declarative localapi operation catalog.
func Operations() []OperationSpec {
	out := make([]OperationSpec, len(operationCatalog))
	copy(out, operationCatalog)
	return out
}

// LookupOperation looks up a declarative operation by its name (without /v1/).
func LookupOperation(name string) (OperationSpec, bool) {
	spec, ok := operationsByName[name]
	return spec, ok
}

// LookupOperationByPath looks up a declarative operation by its HTTP path.
func LookupOperationByPath(path string) (OperationSpec, bool) {
	spec, ok := operationsByPath[path]
	return spec, ok
}

// NewHandler mounts every declarative operation in the catalog onto an
// http.ServeMux at its Method and Path.
func (s *Server) NewHandler() *http.ServeMux {
	mux := http.NewServeMux()
	for _, spec := range operationCatalog {
		spec := spec
		mux.HandleFunc(spec.Method+" "+spec.Path, func(w http.ResponseWriter, r *http.Request) {
			c, ok := r.Context().Value(clientContextKey{}).(client)
			if !ok {
				s.serveHTTP(w, r, false)
				return
			}
			s.serveOperation(w, r, c, spec)
		})
	}
	return mux
}

func bindOperation[Q, R any](spec OperationSpec, call func(context.Context, *Server, *http.Request, client, Q) (R, error)) OperationSpec {
	if spec.Method == "" {
		spec.Method = http.MethodPost
	}
	if spec.Path == "" {
		spec.Path = "/v1/" + spec.Name
	}
	if spec.RequestBodyLimit == 0 {
		spec.RequestBodyLimit = DefaultRequestBodyLimit
	}
	spec.RequestType = reflect.TypeFor[Q]()
	spec.ResponseType = reflect.TypeFor[R]()
	spec.serve = func(s *Server, w http.ResponseWriter, r *http.Request, c client) {
		serveJSON(w, r, func(ctx context.Context, req Q) (R, error) {
			return call(ctx, s, r, c, req)
		})
	}
	return spec
}

func bindStore[Q, R any](spec OperationSpec, call func(*core.Store, context.Context, Q) (R, error)) OperationSpec {
	return bindOperation(spec, func(ctx context.Context, _ *Server, _ *http.Request, c client, req Q) (R, error) {
		return call(c.store, ctx, req)
	})
}

func bindDestination[Q, R any](spec OperationSpec, call func(*core.Store, context.Context, Q, core.Destination) (R, error)) OperationSpec {
	return bindOperation(spec, func(ctx context.Context, _ *Server, _ *http.Request, c client, req Q) (R, error) {
		return call(c.store, ctx, req, c.destination)
	})
}
