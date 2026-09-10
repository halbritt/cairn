// Package localapi exposes scoped memory access to authenticated local hosts.
// Operator authority mutations and process execution are deliberately absent.
package localapi

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"strings"
	"time"

	"github.com/halbritt/cairn/core"
	"github.com/halbritt/cairn/internal/buildinfo"
)

type Identity struct {
	TokenSHA256 string `json:"token_sha256"`
	Principal   string `json:"principal"`
	Repo        string `json:"repo"`
	Role        string `json:"role"`
	Destination string `json:"destination"`
}
type client struct {
	store       *core.Store
	destination core.Destination
}
type Server struct{ clients map[[32]byte]client }

func New(ctx context.Context, dsn string, identities []Identity) (*Server, error) {
	return NewWithSemanticRanker(ctx, dsn, identities, nil)
}

func NewWithSemanticRanker(ctx context.Context, dsn string, identities []Identity, ranker core.SemanticRanker) (*Server, error) {
	s := &Server{clients: map[[32]byte]client{}}
	fail := func(err error) (*Server, error) { s.Close(); return nil, err }
	invalid := func() (*Server, error) {
		return fail(&core.Error{Code: "INVALID_REQUEST", Message: "invalid or duplicate local API identity configuration"})
	}
	if len(identities) == 0 || len(identities) > 32 {
		return invalid()
	}
	principals := map[string]bool{}
	for _, identity := range identities {
		digest, err := hex.DecodeString(identity.TokenSHA256)
		if err != nil || len(digest) != 32 || strings.TrimSpace(identity.Principal) == "" || len(identity.Principal) > 256 || identity.Repo == "" || identity.Repo == "*" || len(identity.Repo) > 256 || principals[identity.Principal] {
			return invalid()
		}
		if identity.Role != "agent" && identity.Role != "observer" {
			return invalid()
		}
		if identity.Destination != "local" && identity.Destination != "hosted" {
			return invalid()
		}
		key := [32]byte(digest)
		if _, exists := s.clients[key]; exists {
			return invalid()
		}
		store, err := core.OpenWithSemanticRanker(ctx, dsn, core.Channel{Principal: identity.Principal, Repo: identity.Repo, Instrumented: identity.Role == "observer"}, ranker)
		if err != nil {
			return fail(err)
		}
		s.clients[key] = client{store, core.Destination{Name: identity.Destination, AllowLocal: identity.Destination == "local"}}
		principals[identity.Principal] = true
	}
	return s, nil
}
func (s *Server) Close() {
	for _, c := range s.clients {
		c.store.Close()
	}
}
func (s *Server) ServeHTTP(w http.ResponseWriter, r *http.Request) {
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
	if r.Method != "POST" {
		writeError(w, 405, "INVALID_REQUEST", "use POST with JSON")
		return
	}
	ctx, cancel := context.WithTimeout(r.Context(), 30*time.Second)
	defer cancel()
	r = r.WithContext(ctx)
	switch r.URL.Path {
	case "/v1/version":
		serveJSON(w, r, func(context.Context, struct{}) (buildinfo.Info, error) {
			return buildinfo.Read(), nil
		})
	case "/v1/create":
		serveJSON(w, r, c.store.Create)
	case "/v1/edit":
		serveJSON(w, r, c.store.Edit)
	case "/v1/revise":
		serveJSON(w, r, c.store.Revise)
	case "/v1/delete":
		serveJSON(w, r, c.store.Delete)
	case "/v1/supersede":
		serveJSON(w, r, func(ctx context.Context, req core.SupersedeRequest) (core.Supersession, error) {
			if req.GrantID != "" {
				return core.Supersession{}, &core.Error{Code: "AUTHORITY_DENIED", Message: "authority mutations require the operator CLI"}
			}
			return c.store.Supersede(ctx, req)
		})
	case "/v1/preview-retract":
		if !c.destination.AllowLocal {
			writeError(w, 403, "AUTHORITY_DENIED", "impact inspection requires a local profile")
			return
		}
		serveJSON(w, r, func(ctx context.Context, req recordRequest) (core.RetractionPreview, error) {
			return c.store.PreviewRetraction(ctx, req.RecordID)
		})
	case "/v1/supersession":
		if !c.destination.AllowLocal {
			writeError(w, 403, "AUTHORITY_DENIED", "supersession inspection requires a local profile")
			return
		}
		serveJSON(w, r, func(ctx context.Context, req recordRequest) (core.Supersession, error) {
			return c.store.Supersession(ctx, req.RecordID)
		})
	case "/v1/index":
		serveJSON(w, r, func(ctx context.Context, req core.CompileRequest) (core.IndexResult, error) {
			return c.store.Index(ctx, req, c.destination)
		})
	case "/v1/expand":
		serveJSON(w, r, func(ctx context.Context, req core.ExpandRequest) (core.Expansion, error) {
			return c.store.Expand(ctx, req, c.destination)
		})
	case "/v1/expand-evidence":
		serveJSON(w, r, func(ctx context.Context, req core.ExpandEvidenceRequest) (core.EvidenceExpansion, error) {
			return c.store.ExpandEvidence(ctx, req, c.destination)
		})
	case "/v1/compile":
		serveJSON(w, r, func(ctx context.Context, req core.CompileRequest) (core.Package, error) {
			return c.store.Compile(ctx, req, c.destination)
		})
	case "/v1/run-package":
		serveJSON(w, r, func(ctx context.Context, req core.RunPackageRequest) (core.Package, error) {
			return c.store.RunPackage(ctx, req, c.destination)
		})
	case "/v1/usage":
		serveJSON(w, r, c.store.RecordUsage)
	case "/v1/usage-coverage":
		serveJSON(w, r, c.store.RecordUsageCoverage)
	case "/v1/evidence-impact":
		if !c.destination.AllowLocal {
			writeError(w, 403, "AUTHORITY_DENIED", "evidence impact inspection requires a local profile")
			return
		}
		serveJSON(w, r, c.store.InspectEvidenceImpact)
	case "/v1/check-evidence":
		if !c.destination.AllowLocal {
			writeError(w, 403, "AUTHORITY_DENIED", "evidence inspection requires a local profile")
			return
		}
		serveJSON(w, r, c.store.CheckEvidence)
	case "/v1/evidence":
		serveJSON(w, r, c.store.CaptureEvidence)
	case "/v1/spawn":
		serveJSON(w, r, c.store.RecordSpawn)
	case "/v1/terminal":
		serveJSON(w, r, c.store.RecordTerminal)
	case "/v1/task-state":
		serveJSON(w, r, c.store.ObserveTask)
	case "/v1/run-status":
		serveJSON(w, r, func(ctx context.Context, req struct {
			ReceiptID string `json:"receipt_id"`
		}) (core.RunStatus, error) {
			return c.store.RunStatus(ctx, req.ReceiptID)
		})
	case "/v1/claim-run":
		serveJSON(w, r, func(ctx context.Context, req struct {
			ReceiptID string `json:"receipt_id"`
		}) (struct{}, error) {
			return struct{}{}, c.store.ClaimRun(ctx, req.ReceiptID)
		})
	case "/v1/bind-run":
		serveJSON(w, r, c.store.BindRun)
	case "/v1/link-run-retrieval":
		serveJSON(w, r, c.store.LinkRunRetrieval)
	case "/v1/register-context":
		serveJSON(w, r, c.store.RegisterManagedContext)
	case "/v1/delivery":
		serveJSON(w, r, c.store.RecordDelivery)
	case "/v1/outcome":
		serveJSON(w, r, c.store.RecordOutcome)
	case "/v1/assess-run":
		serveJSON(w, r, c.store.AssessRun)
	case "/v1/assessments":
		serveJSON(w, r, func(ctx context.Context, req struct {
			ReceiptID string `json:"receipt_id"`
		}) ([]core.Assessment, error) {
			return c.store.AssessmentHistory(ctx, req.ReceiptID, c.destination)
		})
	case "/v1/refusal":
		if !c.destination.AllowLocal {
			writeError(w, 403, "AUTHORITY_DENIED", "protected refusal inspection requires a local profile")
			return
		}
		serveJSON(w, r, func(ctx context.Context, req struct {
			RefusalID string `json:"refusal_id"`
		}) (core.Refusal, error) {
			return c.store.Refusal(ctx, req.RefusalID)
		})
	case "/v1/use-report":
		if !c.destination.AllowLocal {
			writeError(w, 403, "AUTHORITY_DENIED", "protected reports require a local profile")
			return
		}
		serveJSON(w, r, c.store.UseReport)
	case "/v1/run-report":
		if !c.destination.AllowLocal {
			writeError(w, 403, "AUTHORITY_DENIED", "protected reports require a local profile")
			return
		}
		serveJSON(w, r, c.store.RunReport)
	case "/v1/conflicts":
		if !c.destination.AllowLocal {
			writeError(w, 403, "AUTHORITY_DENIED", "conflict inspection requires a local profile")
			return
		}
		serveJSON(w, r, c.store.Conflicts)
	case "/v1/conflict":
		if !c.destination.AllowLocal {
			writeError(w, 403, "AUTHORITY_DENIED", "conflict inspection requires a local profile")
			return
		}
		serveJSON(w, r, func(ctx context.Context, req struct {
			ConflictID string `json:"conflict_id"`
		}) (core.ConflictDetail, error) {
			return c.store.Conflict(ctx, req.ConflictID)
		})
	case "/v1/history":
		serveJSON(w, r, func(ctx context.Context, req core.RecordHistoryRequest) (core.RecordHistory, error) {
			return c.store.History(ctx, req, c.destination)
		})
	case "/v1/get":
		serveJSON(w, r, func(ctx context.Context, req recordRequest) (core.Record, error) {
			record, err := c.store.Get(ctx, req.RecordID)
			if err == nil && !c.destination.AllowLocal && record.Sensitivity == "local" {
				return core.Record{}, &core.Error{Code: "NOT_FOUND", Message: "record not found"}
			}
			return record, err
		})
	default:
		writeError(w, 404, "NOT_FOUND", "unknown endpoint")
	}
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
}

func writeError(w http.ResponseWriter, status int, code, message string, refusalIDs ...string) {
	id := ""
	if len(refusalIDs) > 0 {
		id = refusalIDs[0]
	}
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(response{Schema: "cairn.response/1", Status: code, Message: message, RefusalID: id})
}
func serveJSON[Q any, R any](w http.ResponseWriter, r *http.Request, call func(context.Context, Q) (R, error)) {
	limit := RequestBodyLimit(strings.TrimPrefix(r.URL.Path, "/v1/"))
	decoder := json.NewDecoder(http.MaxBytesReader(w, r.Body, limit))
	decoder.DisallowUnknownFields()
	var req Q
	if err := decoder.Decode(&req); err != nil {
		writeError(w, 400, "INVALID_REQUEST", "invalid bounded JSON request")
		return
	}
	var extra any
	if err := decoder.Decode(&extra); !errors.Is(err, io.EOF) {
		writeError(w, 400, "INVALID_REQUEST", "expected one JSON request")
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
		case "PAYLOAD_UNAVAILABLE", "FORGET_REQUIRED", "STALE_HANDLE", "VERSION_CONFLICT", "IDEMPOTENCY_CONFLICT", "STALE_PACKAGE", "RUN_ALREADY_STARTED", "ATTEMPT_TERMINAL":
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
	_ = json.NewEncoder(w).Encode(response{Schema: "cairn.response/1", OK: true, Status: "OK", Data: result})
}
