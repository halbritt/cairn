// Package localapi exposes scoped memory access to authenticated local hosts.
// Operator authority mutations and process execution are deliberately absent.
package localapi

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"strings"
	"sync"
	"time"

	"github.com/halbritt/cairn/core"
	"github.com/halbritt/cairn/internal/buildinfo"
	"github.com/halbritt/cairn/internal/jsontext"
)

type Identity struct {
	TokenSHA256 string `json:"token_sha256"`
	Principal   string `json:"principal"`
	Repo        string `json:"repo"`
	Role        string `json:"role"`
	Destination string `json:"destination"`
	Remote      bool   `json:"remote,omitempty"`
	MachineID   string `json:"machine_id,omitempty"`
}
type client struct {
	store       *core.Store
	remote      bool
	role        string
	principal   string
	machineID   string
	destination core.Destination
	// sourceOpening is the profile's receipt-binding state as configured at start;
	// the version declaration reads it here because an agent-session view of the
	// store is a different principal.
	sourceOpening        bool
	indexedSemanticQuery bool
}
type expansionReader struct {
	repo        string
	destination core.Destination
}
type Server struct {
	clients  map[[32]byte]client
	readers  map[string]expansionReader
	machines map[string]string
	// obs is the volatile client observation registry, created on first use so a
	// Server never depends on it for startup. Tests may set it before serving.
	obs         *observer
	observeOnce sync.Once
	routeMux    *http.ServeMux
	muxOnce     sync.Once
}

func (s *Server) mux() *http.ServeMux {
	s.muxOnce.Do(func() {
		s.routeMux = s.NewHandler()
	})
	return s.routeMux
}

func (s *Server) observation() *observer {
	s.observeOnce.Do(func() {
		if s.obs == nil {
			s.obs = newObserver()
		}
	})
	return s.obs
}

func New(ctx context.Context, dsn string, identities []Identity, options ...Option) (*Server, error) {
	return NewWithSemanticRanker(ctx, dsn, identities, nil, options...)
}

func NewWithSemanticRanker(ctx context.Context, dsn string, identities []Identity, ranker core.SemanticRanker, options ...Option) (*Server, error) {
	return newWithSemantic(ctx, dsn, identities, ranker, nil, options)
}

func NewWithSemanticRetriever(ctx context.Context, dsn string, identities []Identity, retrieve core.SemanticRetriever, options ...Option) (*Server, error) {
	return newWithSemantic(ctx, dsn, identities, nil, retrieve, options)
}

func newWithSemantic(ctx context.Context, dsn string, identities []Identity, ranker core.SemanticRanker, retrieve core.SemanticRetriever, options []Option) (*Server, error) {
	if err := ValidateIdentities(identities); err != nil {
		return nil, err
	}
	var configured serverOptions
	for _, option := range options {
		option(&configured)
	}
	if err := configured.validate(identities); err != nil {
		return nil, err
	}
	s := &Server{clients: map[[32]byte]client{}, readers: map[string]expansionReader{}, machines: map[string]string{}}
	fail := func(err error) (*Server, error) { s.Close(); return nil, err }
	for _, identity := range identities {
		digest, _ := hex.DecodeString(identity.TokenSHA256)
		key := [32]byte(digest)
		channel := core.Channel{Principal: identity.Principal, Repo: identity.Repo, Instrumented: identity.Role == "observer", SourceOpeningBinding: configured.sourceOpening[identity.Principal]}
		var store *core.Store
		var err error
		if retrieve != nil {
			store, err = core.OpenWithSemanticRetriever(ctx, dsn, channel, retrieve)
		} else {
			store, err = core.OpenWithSemanticRanker(ctx, dsn, channel, ranker)
		}
		if err != nil {
			return fail(err)
		}
		s.clients[key] = client{store: store, destination: core.Destination{Name: identity.Destination, AllowLocal: identity.Destination == "local"}, remote: identity.Remote, role: identity.Role, principal: identity.Principal, machineID: identity.MachineID, sourceOpening: channel.SourceOpeningBinding, indexedSemanticQuery: retrieve != nil}
		if identity.Role == "agent" {
			s.readers[identity.Principal] = expansionReader{identity.Repo, s.clients[key].destination}
		}
		s.machines[identity.Principal] = identity.MachineID
	}
	return s, nil
}
func (s *Server) Close() {
	for _, c := range s.clients {
		c.store.Close()
	}
}
func (s *Server) ServeHTTP(w http.ResponseWriter, r *http.Request) { s.serveHTTP(w, r, false) }

func (s *Server) serveHTTP(w http.ResponseWriter, r *http.Request, remoteOnly bool) {
	w.Header().Set("Content-Type", "application/json")
	token, ok := strings.CutPrefix(r.Header.Get("Authorization"), "Bearer ")
	if !ok || len(token) > 512 {
		writeError(w, 401, "AUTHORITY_DENIED", "authentication required")
		return
	}
	c, ok := s.clients[sha256.Sum256([]byte(token))]
	if !ok {
		writeError(w, 401, "AUTHORITY_DENIED", "authentication required")
		return
	}
	if remoteOnly && !c.remote {
		writeError(w, 403, "AUTHORITY_DENIED", "network API requires a remote machine profile")
		return
	}
	opSpec, hasOp := LookupOperation(strings.TrimPrefix(r.URL.Path, "/v1/"))
	if c.remote && (!hasOp || !opSpec.RemoteAllowed || (c.role != "agent" && !opSpec.RemoteObserverAllowed)) {
		writeError(w, 403, "AUTHORITY_DENIED", "operation is unavailable to remote machine profiles")
		return
	}
	if r.Method != "POST" {
		writeError(w, 405, "INVALID_REQUEST", "use POST with JSON")
		return
	}
	// Record the declaration of this authenticated contact before protocol
	// admission, so a refused request is still diagnosable. Observation never
	// reads the body, fails, retries or changes how the request is handled; the
	// inspection route is excluded so that listing does not refresh anything.
	if r.URL.Path != "/v1/clients" {
		s.observation().observe(c.principal, c.machineID, r.Header.Values(ClientDiagnosticsHeader))
	}
	if !admitProtocol(w, r, remoteOnly) {
		return
	}
	ctx, cancel := context.WithTimeout(r.Context(), 30*time.Second)
	defer cancel()
	r = r.WithContext(ctx)
	ref := core.AgentSessionRef{AgentID: r.Header.Get("Cairn-Agent-ID"), ExecutionID: r.Header.Get("Cairn-Execution-ID")}
	if len(r.Header.Values("Cairn-Agent-ID")) > 0 || len(r.Header.Values("Cairn-Execution-ID")) > 0 {
		if strings.HasPrefix(r.URL.Path, "/v1/agent-") {
			writeError(w, 400, "INVALID_REQUEST", "session directory operations use the base profile and explicit request fields")
			return
		}
		store, err := c.store.ForAgentSession(ref, c.destination)
		if err != nil {
			writeError(w, 400, core.Code(err), err.Error())
			return
		}
		c.store = store
	}
	spec, ok := LookupOperationByPath(r.URL.Path)
	if !ok || r.Method != spec.Method {
		writeError(w, 404, "NOT_FOUND", "unknown endpoint")
		return
	}
	s.mux().ServeHTTP(w, r.WithContext(context.WithValue(r.Context(), clientContextKey{}, c)))
}

func (s *Server) serveOperation(w http.ResponseWriter, r *http.Request, c client, spec OperationSpec) {
	if spec.RequiresLocalDestination && !c.destination.AllowLocal {
		writeError(w, 403, "AUTHORITY_DENIED", spec.LocalDestinationMessage)
		return
	}
	spec.serve(s, w, r, c)
}

var serverOperations = []OperationSpec{
	bindOperation(OperationSpec{Name: "version", SourceFile: "server.go", RemoteAllowed: true, RemoteObserverAllowed: true, RetryClass: "read"}, func(_ context.Context, _ *Server, _ *http.Request, c client, _ struct{}) (VersionResponse, error) {
		return VersionResponse{QueryInputCapabilities: &QueryInputCapabilities{Schema: QueryInputCapabilitiesSchema, IndexedSemanticQueryV1: c.indexedSemanticQuery}, PreviewCapabilities: &PreviewCapabilities{Schema: PreviewCapabilitiesSchema, EntitiesOmitted: true}, VersionInfo: VersionInfo{Info: buildinfo.Read(), Protocol: &Protocol}, RetrievalCapabilities: CurrentRetrievalCapabilities(), InspectionCapabilities: CurrentInspectionCapabilities(),
			SourceOpeningCapabilities: &SourceOpeningCapabilities{Schema: SourceOpeningCapabilitiesSchema, ReaderGuard: true, ReceiptBinding: c.sourceOpening}}, nil
	}),
	// A principal-scoped read of this process's volatile observations. The
	// caller's own principal and machine come from its authenticated profile.
	bindOperation(OperationSpec{Name: "clients", SourceFile: "server.go", RemoteAllowed: true, RetryClass: "read"}, func(_ context.Context, s *Server, _ *http.Request, c client, req ClientsRequest) (ClientsResponse, error) {
		return s.observation().view(c.principal, c.machineID, req)
	}),
	bindStore(OperationSpec{Name: "create", SourceFile: "server.go", RemoteAllowed: true, RequestBodyLimit: 512 * 1024, RetryClass: "request-id"}, (*core.Store).Create),
	bindStore(OperationSpec{Name: "edit", SourceFile: "server.go", RemoteAllowed: true, RequestBodyLimit: 512 * 1024, RetryClass: "request-id"}, (*core.Store).Edit),
	bindStore(OperationSpec{Name: "revise", SourceFile: "server.go", RemoteAllowed: true, RequestBodyLimit: 512 * 1024, RetryClass: "request-id"}, (*core.Store).Revise),
	bindStore(OperationSpec{Name: "append", SourceFile: "server.go", RemoteAllowed: true, RequestBodyLimit: 512 * 1024, RetryClass: "request-id"}, (*core.Store).Append),
	bindDestination(OperationSpec{Name: "replace", SourceFile: "server.go", RemoteAllowed: true, RequestBodyLimit: 1024 * 1024, RetryClass: "request-id"}, (*core.Store).ReplaceForDestination),
	bindDestination(OperationSpec{Name: "set-sections", SourceFile: "server.go", RemoteAllowed: true, RequestBodyLimit: 1024 * 1024, RetryClass: "request-id"}, (*core.Store).SetSectionsForDestination),
	bindStore(OperationSpec{Name: "cite", SourceFile: "server.go", RemoteAllowed: true, RetryClass: "request-id"}, (*core.Store).Cite),
	bindStore(OperationSpec{Name: "delete", SourceFile: "server.go", RemoteAllowed: true, RetryClass: "request-id"}, (*core.Store).Delete),
	bindOperation(OperationSpec{Name: "supersede", SourceFile: "server.go", RemoteAllowed: true, RetryClass: "request-id"}, func(ctx context.Context, _ *Server, _ *http.Request, c client, req core.SupersedeRequest) (core.Supersession, error) {
		if req.GrantID != "" {
			return core.Supersession{}, &core.Error{Code: "AUTHORITY_DENIED", Message: "authority mutations require the operator CLI"}
		}
		return c.store.Supersede(ctx, req)
	}),
	bindOperation(OperationSpec{Name: "preview-retract", SourceFile: "server.go", RequiresLocalDestination: true, LocalDestinationMessage: "impact inspection requires a local profile", RemoteAllowed: false, RetryClass: "read"}, func(ctx context.Context, _ *Server, _ *http.Request, c client, req recordRequest) (core.RetractionPreview, error) {
		return c.store.PreviewRetraction(ctx, req.RecordID)
	}),
	bindOperation(OperationSpec{Name: "supersession", SourceFile: "server.go", RequiresLocalDestination: true, LocalDestinationMessage: "supersession inspection requires a local profile", RemoteAllowed: false, RetryClass: "read"}, func(ctx context.Context, _ *Server, _ *http.Request, c client, req recordRequest) (core.Supersession, error) {
		return c.store.Supersession(ctx, req.RecordID)
	}),
	bindOperation(OperationSpec{Name: "index", SourceFile: "server.go", RemoteAllowed: true, RetryClass: "request-id"}, func(ctx context.Context, s *Server, _ *http.Request, c client, req core.CompileRequest) (core.IndexResult, error) {
		if err := s.checkExpansionReader(req, c.destination); err != nil {
			return core.IndexResult{}, err
		}
		return c.store.Index(ctx, req, c.destination)
	}),
	bindOperation(OperationSpec{Name: "expand", SourceFile: "server.go", RemoteAllowed: true, RetryClass: "request-id"}, func(ctx context.Context, _ *Server, r *http.Request, c client, req core.ExpandRequest) (core.Expansion, error) {
		accepts, err := acceptsSourceOpening(r.Header)
		if err != nil {
			return core.Expansion{}, err
		}
		return c.store.ExpandForReader(ctx, req, c.destination, accepts)
	}),
	bindDestination(OperationSpec{Name: "expand-evidence", SourceFile: "server.go", RemoteAllowed: true, RetryClass: "request-id"}, (*core.Store).ExpandEvidence),
	bindOperation(OperationSpec{Name: "compile", SourceFile: "server.go", RemoteAllowed: true, RetryClass: "request-id"}, func(ctx context.Context, s *Server, _ *http.Request, c client, req core.CompileRequest) (core.Package, error) {
		if err := s.checkExpansionReader(req, c.destination); err != nil {
			return core.Package{}, err
		}
		return c.store.Compile(ctx, req, c.destination)
	}),
	bindOperation(OperationSpec{Name: "explain-page", SourceFile: "server.go", RemoteAllowed: true, RequestBodyLimit: 4096, RetryClass: "read"}, func(ctx context.Context, _ *Server, _ *http.Request, c client, req core.ExplainPageRequest) (core.ExplanationPage, error) {
		page, err := c.store.ExplainPageForDestination(ctx, req, c.destination)
		if err != nil {
			return core.ExplanationPage{}, err
		}
		if err = boundEnvelope(page, 32*1024); err != nil {
			return core.ExplanationPage{}, err
		}
		return page, nil
	}),
	bindOperation(OperationSpec{Name: "recompile", SourceFile: "server.go", RemoteAllowed: true, RetryClass: "read"}, func(ctx context.Context, _ *Server, _ *http.Request, c client, req core.RecompileRequest) (struct {
		Historical bool         `json:"historical"`
		Package    core.Package `json:"package"`
	}, error) {
		pkg, err := c.store.RecompileForDestination(ctx, req, c.destination)
		return struct {
			Historical bool         `json:"historical"`
			Package    core.Package `json:"package"`
		}{true, pkg}, err
	}),
	bindDestination(OperationSpec{Name: "run-index", SourceFile: "server.go", RemoteAllowed: false, RetryClass: "read"}, (*core.Store).RunIndex),
	bindDestination(OperationSpec{Name: "run-package", SourceFile: "server.go", RemoteAllowed: false, RetryClass: "read"}, (*core.Store).RunPackage),
	bindStore(OperationSpec{Name: "usage", SourceFile: "server.go", RemoteAllowed: true, RetryClass: "request-id"}, (*core.Store).RecordUsage),
	bindStore(OperationSpec{Name: "usage-coverage", SourceFile: "server.go", RemoteAllowed: true, RetryClass: "request-id"}, (*core.Store).RecordUsageCoverage),
	bindStore(OperationSpec{Name: "recall-observation", SourceFile: "server.go", RemoteAllowed: true, RetryClass: "request-id"}, (*core.Store).RecordRecallObservation),
	bindStore(OperationSpec{Name: "capture-observation", SourceFile: "server.go", RemoteAllowed: true, RetryClass: "request-id"}, (*core.Store).RecordCaptureObservation),
	bindStore(OperationSpec{Name: "capture-observations", SourceFile: "server.go", RequiresLocalDestination: true, LocalDestinationMessage: "capture observation history requires a local profile", RemoteAllowed: false, RetryClass: "read"}, (*core.Store).CaptureObservations),
	bindStore(OperationSpec{Name: "evidence-impact", SourceFile: "server.go", RequiresLocalDestination: true, LocalDestinationMessage: "evidence impact inspection requires a local profile", RemoteAllowed: false, RetryClass: "read"}, (*core.Store).InspectEvidenceImpact),
	bindStore(OperationSpec{Name: "check-evidence", SourceFile: "server.go", RequiresLocalDestination: true, LocalDestinationMessage: "evidence inspection requires a local profile", RemoteAllowed: false, RetryClass: "request-id"}, (*core.Store).CheckEvidence),
	bindStore(OperationSpec{Name: "evidence", SourceFile: "server.go", RemoteAllowed: true, RequestBodyLimit: MaxRequestBodyLimit, RetryClass: "request-id"}, (*core.Store).CaptureEvidence),
	bindStore(OperationSpec{Name: "spawn", SourceFile: "server.go", RemoteAllowed: false, RetryClass: "request-id"}, (*core.Store).RecordSpawn),
	bindStore(OperationSpec{Name: "terminal", SourceFile: "server.go", RemoteAllowed: false, RetryClass: "request-id"}, (*core.Store).RecordTerminal),
	bindStore(OperationSpec{Name: "task-state", SourceFile: "server.go", RemoteAllowed: false, RetryClass: "request-id"}, (*core.Store).ObserveTask),
	bindOperation(OperationSpec{Name: "run-status", SourceFile: "server.go", RemoteAllowed: false, RetryClass: "read"}, func(ctx context.Context, _ *Server, _ *http.Request, c client, req struct {
		ReceiptID string `json:"receipt_id"`
	}) (core.RunStatus, error) {
		return c.store.RunStatus(ctx, req.ReceiptID)
	}),
	bindOperation(OperationSpec{Name: "claim-run", SourceFile: "server.go", RemoteAllowed: false, RetryClass: "no-key"}, func(ctx context.Context, _ *Server, _ *http.Request, c client, req struct {
		ReceiptID string `json:"receipt_id"`
	}) (struct{}, error) {
		return struct{}{}, c.store.ClaimRun(ctx, req.ReceiptID)
	}),
	bindStore(OperationSpec{Name: "bind-run", SourceFile: "server.go", RemoteAllowed: false, RetryClass: "request-id"}, (*core.Store).BindRun),
	bindStore(OperationSpec{Name: "link-run-retrieval", SourceFile: "server.go", RemoteAllowed: false, RetryClass: "request-id"}, (*core.Store).LinkRunRetrieval),
	bindStore(OperationSpec{Name: "register-context", SourceFile: "server.go", RemoteAllowed: false, RetryClass: "request-id"}, (*core.Store).RegisterManagedContext),
	bindStore(OperationSpec{Name: "delivery", SourceFile: "server.go", RemoteAllowed: false, RetryClass: "request-id"}, (*core.Store).RecordDelivery),
	bindStore(OperationSpec{Name: "outcome", SourceFile: "server.go", RemoteAllowed: false, RetryClass: "request-id"}, (*core.Store).RecordOutcome),
	bindDestination(OperationSpec{Name: "assess-run", SourceFile: "server.go", RemoteAllowed: false, RetryClass: "request-id"}, (*core.Store).AssessRunForDestination),
	bindOperation(OperationSpec{Name: "assessments", SourceFile: "server.go", RemoteAllowed: false, RetryClass: "read"}, func(ctx context.Context, _ *Server, _ *http.Request, c client, req struct {
		ReceiptID string `json:"receipt_id"`
	}) ([]core.Assessment, error) {
		return c.store.AssessmentHistory(ctx, req.ReceiptID, c.destination)
	}),
	bindOperation(OperationSpec{Name: "assessments-page", SourceFile: "server.go", RemoteAllowed: false, RetryClass: "read"}, func(ctx context.Context, _ *Server, _ *http.Request, c client, req struct {
		ReceiptID    string `json:"receipt_id"`
		AfterVersion int    `json:"after_version"`
		Limit        int    `json:"limit"`
	}) (core.AssessmentPage, error) {
		if req.Limit == 0 {
			req.Limit = 100
		}
		return c.store.AssessmentHistoryPage(ctx, req.ReceiptID, c.destination, req.AfterVersion, req.Limit)
	}),
	bindOperation(OperationSpec{Name: "refusal", SourceFile: "server.go", RequiresLocalDestination: true, LocalDestinationMessage: "protected refusal inspection requires a local profile", RemoteAllowed: false, RetryClass: "read"}, func(ctx context.Context, _ *Server, _ *http.Request, c client, req struct {
		RefusalID string `json:"refusal_id"`
	}) (core.Refusal, error) {
		return c.store.Refusal(ctx, req.RefusalID)
	}),
	bindStore(OperationSpec{Name: "use-report", SourceFile: "server.go", RequiresLocalDestination: true, LocalDestinationMessage: "protected reports require a local profile", RemoteAllowed: false, RetryClass: "read"}, (*core.Store).UseReport),
	bindStore(OperationSpec{Name: "run-report", SourceFile: "server.go", RequiresLocalDestination: true, LocalDestinationMessage: "protected reports require a local profile", RemoteAllowed: false, RetryClass: "read"}, (*core.Store).RunReport),
	bindStore(OperationSpec{Name: "conflicts", SourceFile: "server.go", RequiresLocalDestination: true, LocalDestinationMessage: "conflict inspection requires a local profile", RemoteAllowed: false, RetryClass: "read"}, (*core.Store).Conflicts),
	bindOperation(OperationSpec{Name: "conflict", SourceFile: "server.go", RequiresLocalDestination: true, LocalDestinationMessage: "conflict inspection requires a local profile", RemoteAllowed: false, RetryClass: "read"}, func(ctx context.Context, _ *Server, _ *http.Request, c client, req struct {
		ConflictID string `json:"conflict_id"`
	}) (core.ConflictDetail, error) {
		return c.store.Conflict(ctx, req.ConflictID)
	}),
	bindDestination(OperationSpec{Name: "history", SourceFile: "server.go", RemoteAllowed: true, RetryClass: "read"}, (*core.Store).History),
	bindOperation(OperationSpec{Name: "get", SourceFile: "server.go", RemoteAllowed: true, RetryClass: "read"}, func(ctx context.Context, _ *Server, _ *http.Request, c client, req recordRequest) (core.Record, error) {
		record, err := c.store.Get(ctx, req.RecordID)
		if err == nil && !c.destination.AllowLocal && record.Sensitivity == "local" {
			return core.Record{}, &core.Error{Code: "NOT_FOUND", Message: "record not found"}
		}
		return record, err
	}),
}

func (s *Server) checkExpansionReader(req core.CompileRequest, dest core.Destination) error {
	if req.ExpansionReader == "" {
		return nil
	}
	reader, ok := s.readers[req.ExpansionReader]
	if !ok || reader.repo != req.Scope.Repo || reader.destination != dest {
		return &core.Error{Code: "AUTHORITY_DENIED", Message: "expansion reader must be a configured ordinary profile with the same repository and destination"}
	}
	return nil
}

type recordRequest struct {
	RecordID string `json:"record_id"`
}
type response struct {
	RefusalID string `json:"refusal_id,omitempty"`
	Schema    string `json:"schema"`
	OK        bool   `json:"ok"`
	Status    string `json:"status"`
	Data      any    `json:"data,omitempty"`
	Message   string `json:"message,omitempty"`
	// Protocol is the replying party's supported range (server or relay). It
	// is always present from this release; legacy clients ignore it, and its
	// absence identifies a legacy peer.
	Protocol ProtocolRange `json:"protocol"`
}

func writeError(w http.ResponseWriter, status int, code, message string, refusalIDs ...string) {
	id := ""
	if len(refusalIDs) > 0 {
		id = refusalIDs[0]
	}
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(response{Schema: "cairn.response/1", Status: code, Message: message, RefusalID: id, Protocol: Protocol})
}
func serveJSON[Q any, R any](w http.ResponseWriter, r *http.Request, call func(context.Context, Q) (R, error)) {
	limit := RequestBodyLimit(strings.TrimPrefix(r.URL.Path, "/v1/"))
	body, err := io.ReadAll(http.MaxBytesReader(w, r.Body, limit))
	if err != nil || jsontext.CheckUnicode(body) != nil {
		writeError(w, 400, "INVALID_REQUEST", "invalid bounded JSON request")
		return
	}
	body, admitted := admitBodyProtocol(w, r, body)
	if !admitted {
		return
	}
	var req Q
	if err := DecodeRequest(body, &req); err != nil {
		if errors.Is(err, errTrailingRequest) {
			writeError(w, 400, "INVALID_REQUEST", "expected one JSON request")
			return
		}
		writeError(w, 400, "INVALID_REQUEST", "invalid bounded JSON request")
		return
	}
	result, err := call(r.Context(), req)
	if err != nil {
		code := core.Code(err)
		status := 422
		message := err.Error()
		switch code {
		case "AUTHORITY_DENIED", "SELF_PROMOTION_DENIED":
			status = 403
		case "RESTORE_PAUSED":
			status = 503
		case "INVALID_REQUEST":
			status = 400
		case "NOT_FOUND":
			status = 404
		case "PROTOCOL_UNSUPPORTED":
			status = 426
		case "STALE_WORKER", "WORKER_UNAVAILABLE", "POOL_FULL", "POOL_PAUSED", "STALE_RESOLUTION", "STALE_SESSION", "AGENT_BUSY", "DELIVERY_ACTIVE", "STALE_LEASE", "PAYLOAD_UNAVAILABLE", "FORGET_REQUIRED", "STALE_HANDLE", "VERSION_CONFLICT", "IDEMPOTENCY_CONFLICT", "STALE_PACKAGE", "RUN_ALREADY_STARTED", "ATTEMPT_TERMINAL":
			status = 409
		case "STORE_ERROR", "REFUSAL_UNRECORDED":
			status = 500
			message = "store operation failed"
		}
		var failure *core.Error
		id := ""
		if errors.As(err, &failure) {
			id = failure.RefusalID
		}
		writeError(w, status, code, message, id)
		return
	}
	// HTTP write errors mean the caller may not have received a committed result;
	// the request's idempotency key remains its recovery mechanism. Never retry here.
	_ = encodeSuccess(w, result)
}

// encodeSuccess writes the one success envelope. Size checks use it too, so a
// bound on the response measures the bytes serveJSON actually sends.
func encodeSuccess(w io.Writer, data any) error {
	return json.NewEncoder(w).Encode(response{Schema: "cairn.response/1", OK: true, Status: "OK", Data: data, Protocol: Protocol})
}

// boundEnvelope refuses a result whose complete success envelope, final newline
// included, exceeds limit bytes. The refusal names no content.
func boundEnvelope(data any, limit int) error {
	var encoded bytes.Buffer
	if err := encodeSuccess(&encoded, data); err != nil {
		return err
	}
	if encoded.Len() > limit {
		return &core.Error{Code: "BUDGET_REFUSED", Message: "response exceeds its byte bound; request fewer items"}
	}
	return nil
}
