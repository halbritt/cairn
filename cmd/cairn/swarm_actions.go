package main

import (
	"context"
	"errors"
	"log/slog"
	"net/http"

	"github.com/halbritt/cairn/core"
	"github.com/halbritt/cairn/internal/refusal"
	"github.com/jackc/pgx/v5/pgconn"
)

type swarmActionRoute struct {
	method string
	handle func(*swarmServer, http.ResponseWriter, *http.Request)
}

var swarmActionRoutes = map[string]swarmActionRoute{
	"/api/swarm/event":                  {http.MethodGet, (*swarmServer).swarmEvent},
	"/api/swarm/action/send":            {http.MethodPost, (*swarmServer).swarmSend},
	"/api/swarm/action/acknowledge":     {http.MethodPost, (*swarmServer).swarmAcknowledge},
	"/api/swarm/action/cancel-preview":  {http.MethodGet, (*swarmServer).swarmCancelPreview},
	"/api/swarm/action/cancel-delivery": {http.MethodPost, (*swarmServer).swarmCancelDelivery},
	"/api/swarm/action/cancel-wait":     {http.MethodPost, (*swarmServer).swarmCancelWait},
}

// ServeHTTP has already checked the host, origin, session and POST CSRF token.
// The CLI opens s.store as the local-uid:<euid> operator. The browser can
// choose an action's target, while the Store channel supplies its caller.
func (s *swarmServer) handleAction(w http.ResponseWriter, r *http.Request) {
	route, ok := swarmActionRoutes[r.URL.Path]
	if !ok {
		swarmRefuse(w, http.StatusNotFound, "NOT_FOUND", "no such route")
		return
	}
	if r.Method != route.method {
		swarmMethod(w, route.method)
		return
	}
	route.handle(s, w, r)
}

func writeSwarmActionResult(w http.ResponseWriter, result any, err error) {
	if err == nil {
		writeSwarmJSON(w, http.StatusOK, result)
		return
	}
	if errors.Is(err, context.Canceled) || errors.Is(err, errSwarmSessionEnded) {
		writeSwarmError(w, err)
		return
	}

	code := core.Code(err)
	var pgErr *pgconn.PgError
	if code == "STORE_ERROR" || code == "TIMEOUT" || errors.As(err, &pgErr) {
		slog.Error("swarm action store error", "code", code, "error", err)
		classification := refusal.Classify(code)
		message := classification.HTTPMessage
		if message == "" {
			message = refusal.Classify("STORE_ERROR").HTTPMessage
		}
		swarmRefuse(w, classification.HTTPStatus, code, message)
		return
	}
	writeSwarmError(w, err)
}

func (s *swarmServer) swarmEvent(w http.ResponseWriter, r *http.Request) {
	query := r.URL.Query()
	result, err := s.store.GetSwarmEvent(r.Context(), s.collection, query.Get("campaign"), query.Get("event_id"))
	writeSwarmActionResult(w, result, err)
}

func (s *swarmServer) swarmSend(w http.ResponseWriter, r *http.Request) {
	var req core.SwarmSendRequest
	if !decodeSwarmBody(w, r, &req) {
		return
	}
	req.Repo = s.collection
	result, err := s.store.SendSwarmEvent(r.Context(), req)
	writeSwarmActionResult(w, result, err)
}

func (s *swarmServer) swarmAcknowledge(w http.ResponseWriter, r *http.Request) {
	var req core.SwarmDeliveryActionRequest
	if !decodeSwarmBody(w, r, &req) {
		return
	}
	req.Repo = s.collection
	result, err := s.store.AcknowledgeSwarmDelivery(r.Context(), req)
	var conflict *core.SwarmAcknowledgeConflict
	if errors.As(err, &conflict) {
		writeSwarmJSON(w, http.StatusConflict, map[string]any{
			"error": conflict.Error(), "code": core.Code(conflict),
			"expected": conflict.Expected, "oldest": conflict.Oldest,
		})
		return
	}
	writeSwarmActionResult(w, result, err)
}

func (s *swarmServer) swarmCancelPreview(w http.ResponseWriter, r *http.Request) {
	query := r.URL.Query()
	result, err := s.store.GetSwarmCancelPreview(r.Context(), core.SwarmCancelPreviewRequest{
		Repo: s.collection, Campaign: query.Get("campaign"), DeliveryID: query.Get("delivery_id"),
	})
	writeSwarmActionResult(w, result, err)
}

func (s *swarmServer) swarmCancelDelivery(w http.ResponseWriter, r *http.Request) {
	var req core.SwarmCancelDeliveryRequest
	if !decodeSwarmBody(w, r, &req) {
		return
	}
	req.Repo = s.collection
	result, err := s.store.CancelSwarmDelivery(r.Context(), req)
	var conflict *core.SwarmPreviewConflict
	if errors.As(err, &conflict) {
		writeSwarmJSON(w, http.StatusConflict, map[string]any{
			"error": conflict.Error(), "code": core.Code(conflict), "preview": conflict.Preview,
		})
		return
	}
	writeSwarmActionResult(w, result, err)
}

func (s *swarmServer) swarmCancelWait(w http.ResponseWriter, r *http.Request) {
	var req core.SwarmCancelWaitRequest
	if !decodeSwarmBody(w, r, &req) {
		return
	}
	req.Repo = s.collection
	result, err := s.store.CancelSwarmWait(r.Context(), req)
	var conflict *core.SwarmWaitOwnerConflict
	if errors.As(err, &conflict) {
		writeSwarmJSON(w, http.StatusConflict, map[string]any{
			"error": conflict.Error(), "code": core.Code(conflict), "wait": conflict.Wait,
		})
		return
	}
	writeSwarmActionResult(w, result, err)
}
