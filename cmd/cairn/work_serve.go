package main

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"net"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
	"github.com/halbritt/cairn/internal/refusal"
	"github.com/halbritt/cairn/web/workview"
)

func writeWorkHTTPError(w http.ResponseWriter, err error) {
	code := core.Code(err)
	status := refusal.Classify(code).HTTPStatus
	if status == 0 {
		status = http.StatusInternalServerError
	}
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(map[string]any{
		"error": err.Error(),
		"code":  code,
	})
}

type workServer struct {
	store           *core.Store
	collection      string
	defaultSlug     string
	launchCode      string
	launchExpiresAt time.Time
	launchUsed      bool
	launchMu        sync.Mutex
	sessionTokens   map[string]time.Time
	csrfTokens      map[string]string // sessionToken -> csrfToken
	sessionMu       sync.RWMutex
	port            int
}

func newWorkServer(store *core.Store, collection, defaultSlug string, port int) (*workServer, error) {
	codeBytes := make([]byte, 16)
	if _, err := rand.Read(codeBytes); err != nil {
		return nil, err
	}
	return &workServer{
		store:           store,
		collection:      collection,
		defaultSlug:     defaultSlug,
		launchCode:      hex.EncodeToString(codeBytes),
		launchExpiresAt: time.Now().Add(5 * time.Minute),
		sessionTokens:   make(map[string]time.Time),
		csrfTokens:      make(map[string]string),
		port:            port,
	}, nil
}

func (s *workServer) issueSession() (string, string) {
	s.sessionMu.Lock()
	defer s.sessionMu.Unlock()
	b := make([]byte, 24)
	_, _ = rand.Read(b)
	sess := hex.EncodeToString(b)
	cb := make([]byte, 16)
	_, _ = rand.Read(cb)
	csrf := hex.EncodeToString(cb)
	s.sessionTokens[sess] = time.Now().Add(24 * time.Hour)
	s.csrfTokens[sess] = csrf
	return sess, csrf
}

func (s *workServer) validateSession(r *http.Request) (string, bool) {
	cookie, err := r.Cookie("work_session")
	if err != nil || cookie.Value == "" {
		return "", false
	}
	s.sessionMu.RLock()
	defer s.sessionMu.RUnlock()
	exp, ok := s.sessionTokens[cookie.Value]
	if !ok || time.Now().After(exp) {
		return "", false
	}
	return cookie.Value, true
}

func (s *workServer) checkHostOrigin(r *http.Request) bool {
	host, portStr, err := net.SplitHostPort(r.Host)
	if err != nil {
		if s.port == 80 && (r.Host == "127.0.0.1" || r.Host == "localhost") {
			host = r.Host
			portStr = "80"
		} else {
			return false
		}
	}
	if host != "127.0.0.1" && host != "localhost" {
		return false
	}
	if portStr != strconv.Itoa(s.port) {
		return false
	}

	origin := r.Header.Get("Origin")
	if origin != "" {
		u, err := url.Parse(origin)
		if err != nil {
			return false
		}
		if u.Scheme != "http" {
			return false
		}
		origHost := u.Hostname()
		if origHost != "127.0.0.1" && origHost != "localhost" {
			return false
		}
		origPort := u.Port()
		if origPort == "" {
			if u.Scheme == "http" {
				origPort = "80"
			}
		}
		if origPort != strconv.Itoa(s.port) {
			return false
		}
	}
	return true
}

func (s *workServer) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	if !s.checkHostOrigin(r) {
		http.Error(w, "Forbidden: loopback access only", http.StatusForbidden)
		return
	}

	// Static asset routing
	switch r.URL.Path {
	case "/styles.css":
		data, err := workview.FS.ReadFile("styles.css")
		if err != nil {
			http.NotFound(w, r)
			return
		}
		w.Header().Set("Content-Type", "text/css; charset=utf-8")
		_, _ = w.Write(data)
		return
	case "/app.js":
		data, err := workview.FS.ReadFile("app.js")
		if err != nil {
			http.NotFound(w, r)
			return
		}
		w.Header().Set("Content-Type", "application/javascript; charset=utf-8")
		_, _ = w.Write(data)
		return
	case "/":
		data, err := workview.FS.ReadFile("index.html")
		if err != nil {
			http.NotFound(w, r)
			return
		}
		w.Header().Set("Content-Type", "text/html; charset=utf-8")
		_, _ = w.Write(data)
		return
	}

	if (r.URL.Path == "/api/auth/session" || r.URL.Path == "/api/auth/status") && r.Method == http.MethodGet {
		sess, authenticated := s.validateSession(r)
		w.Header().Set("Content-Type", "application/json; charset=utf-8")
		if !authenticated {
			_ = json.NewEncoder(w).Encode(map[string]any{
				"authenticated": false,
			})
			return
		}
		s.sessionMu.RLock()
		csrf := s.csrfTokens[sess]
		s.sessionMu.RUnlock()
		w.Header().Set("X-CSRF-Token", csrf)
		_ = json.NewEncoder(w).Encode(map[string]any{
			"authenticated": true,
			"csrf_token":    csrf,
		})
		return
	}

	// API authentication gate
	sess, authenticated := s.validateSession(r)
	if !authenticated && r.URL.Path != "/api/auth/login" && r.URL.Path != "/api/auth/session" && r.URL.Path != "/api/auth/status" {
		http.Error(w, `{"error":"unauthorized"}`, http.StatusUnauthorized)
		return
	}

	// CSRF validation on mutating API requests
	if r.Method == http.MethodPost || r.Method == http.MethodPut || r.Method == http.MethodDelete {
		if r.URL.Path != "/api/auth/login" && r.URL.Path != "/api/auth/session" && r.URL.Path != "/api/auth/status" {
			s.sessionMu.RLock()
			expectedCSRF := s.csrfTokens[sess]
			s.sessionMu.RUnlock()
			givenCSRF := r.Header.Get("X-CSRF-Token")
			if expectedCSRF == "" || givenCSRF != expectedCSRF {
				http.Error(w, `{"error":"invalid csrf token"}`, http.StatusForbidden)
				return
			}
		}
	}

	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	if sess != "" {
		s.sessionMu.RLock()
		if csrf, ok := s.csrfTokens[sess]; ok {
			w.Header().Set("X-CSRF-Token", csrf)
		}
		s.sessionMu.RUnlock()
	}

	s.handleAPI(w, r)
}

func (s *workServer) handleAPI(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	if strings.HasPrefix(r.URL.Path, "/api/work/") {
		if s.store == nil {
			http.Error(w, `{"error":"store unavailable"}`, http.StatusServiceUnavailable)
			return
		}
		st, err := s.store.GetWorkFeatureStatus(ctx, "work-view/1")
		if err != nil {
			writeWorkHTTPError(w, err)
			return
		}
		if st == "retired" {
			writeWorkHTTPError(w, &core.Error{Code: "FEATURE_RETIRED", Message: "work-view/1 feature is retired"})
			return
		}
		if st != "active" {
			writeWorkHTTPError(w, &core.Error{Code: "FEATURE_DISABLED", Message: "work-view/1 feature is disabled"})
			return
		}
	}

	switch {
	case r.URL.Path == "/api/auth/login" && r.Method == http.MethodPost:
		var req struct {
			Code  string `json:"code"`
			Token string `json:"token"`
		}
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			http.Error(w, `{"error":"invalid json"}`, http.StatusBadRequest)
			return
		}
		candidate := req.Code
		if candidate == "" {
			candidate = req.Token
		}
		s.launchMu.Lock()
		if s.launchUsed {
			s.launchMu.Unlock()
			http.Error(w, `{"error":"launch code already used"}`, http.StatusForbidden)
			return
		}
		if time.Now().After(s.launchExpiresAt) {
			s.launchMu.Unlock()
			http.Error(w, `{"error":"launch code expired"}`, http.StatusForbidden)
			return
		}
		if candidate == "" || candidate != s.launchCode {
			s.launchMu.Unlock()
			http.Error(w, `{"error":"invalid launch code"}`, http.StatusForbidden)
			return
		}
		s.launchUsed = true
		s.launchMu.Unlock()
		sess, csrf := s.issueSession()
		http.SetCookie(w, &http.Cookie{
			Name:     "work_session",
			Value:    sess,
			Path:     "/",
			HttpOnly: true,
			SameSite: http.SameSiteStrictMode,
		})
		w.Header().Set("X-CSRF-Token", csrf)
		_ = json.NewEncoder(w).Encode(map[string]any{"ok": true, "csrf_token": csrf})

	case r.URL.Path == "/api/work/projects" && r.Method == http.MethodGet:
		projects, err := s.store.ListWorkProjects(ctx, s.collection)
		if err != nil {
			writeWorkHTTPError(w, err)
			return
		}
		_ = json.NewEncoder(w).Encode(map[string]any{"projects": projects})

	case r.URL.Path == "/api/work/items" && r.Method == http.MethodGet:
		slug := r.URL.Query().Get("project")
		if slug == "" {
			slug = s.defaultSlug
		}
		stateGroup := r.URL.Query().Get("state_group")
		limit, _ := strconv.Atoi(r.URL.Query().Get("limit"))
		if limit <= 0 {
			limit = 50
		}
		offset, _ := strconv.Atoi(r.URL.Query().Get("offset"))
		blockerOnly := r.URL.Query().Get("blocker_only") == "1" || r.URL.Query().Get("blocker_only") == "true"

		proj, err := s.store.GetWorkProject(ctx, s.collection, slug)
		if err != nil {
			writeWorkHTTPError(w, err)
			return
		}
		items, total, err := s.store.ListWorkItemsExtended(ctx, s.collection, slug, stateGroup, blockerOnly, limit, offset)
		if err != nil {
			writeWorkHTTPError(w, err)
			return
		}
		if items == nil {
			items = make([]core.WorkItem, 0)
		}
		_ = json.NewEncoder(w).Encode(map[string]any{"project": proj, "items": items, "total": total})

	case r.URL.Path == "/api/work/item/coordination":
		s.handleWorkItemCoordination(w, r)

	case r.URL.Path == "/api/work/item" && r.Method == http.MethodGet:
		slug := r.URL.Query().Get("project")
		if slug == "" {
			slug = s.defaultSlug
		}
		itemUUIDStr := r.URL.Query().Get("item")
		if itemUUIDStr == "" {
			writeWorkHTTPError(w, &core.Error{Code: "INVALID_REQUEST", Message: "missing item identifier or uuid"})
			return
		}
		detail, err := s.store.GetWorkItemDetail(ctx, s.collection, slug, itemUUIDStr)
		if err != nil {
			writeWorkHTTPError(w, err)
			return
		}
		_ = json.NewEncoder(w).Encode(detail)

	case r.URL.Path == "/api/work/refresh" && r.Method == http.MethodPost:
		slug := r.URL.Query().Get("project")
		if slug == "" {
			slug = s.defaultSlug
		}
		snap, err := refreshWorkProject(ctx, s.store, s.collection, slug)
		if err != nil {
			writeWorkHTTPError(w, err)
			return
		}
		_ = json.NewEncoder(w).Encode(snap)

	case r.URL.Path == "/api/work/progress/add" && r.Method == http.MethodPost:
		var req struct {
			Project   string `json:"project"`
			ItemUUID  string `json:"item_uuid"`
			Item      string `json:"item"`
			Text      string `json:"text"`
			RequestID string `json:"request_id"`
		}
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			writeWorkHTTPError(w, &core.Error{Code: "INVALID_REQUEST", Message: "invalid json"})
			return
		}
		slug := req.Project
		if slug == "" {
			slug = s.defaultSlug
		}
		rawItem := req.ItemUUID
		if rawItem == "" {
			rawItem = req.Item
		}
		if strings.TrimSpace(rawItem) == "" {
			writeWorkHTTPError(w, &core.Error{Code: "INVALID_REQUEST", Message: "item or item_uuid is required"})
			return
		}
		itemUUID, err := s.store.ResolveWorkItemUUID(ctx, s.collection, slug, rawItem)
		if err != nil {
			writeWorkHTTPError(w, err)
			return
		}
		if req.RequestID == "" {
			req.RequestID = uuid.NewString()
		}
		ann, err := s.store.AddProgressAnnotation(ctx, core.AddProgressAnnotationRequest{
			RequestID:   req.RequestID,
			Collection:  s.collection,
			ProjectSlug: slug,
			ItemUUID:    itemUUID,
			Body:        req.Text,
		})
		if err != nil {
			writeWorkHTTPError(w, err)
			return
		}
		_ = json.NewEncoder(w).Encode(ann)

	case r.URL.Path == "/api/work/progress/edit" && r.Method == http.MethodPost:
		var req struct {
			Project          string `json:"project"`
			ItemUUID         string `json:"item_uuid"`
			Item             string `json:"item"`
			AnnotationID     string `json:"annotation_id"`
			Text             string `json:"text"`
			ExpectedRevision *int64 `json:"expected_revision"`
			RequestID        string `json:"request_id"`
		}
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			writeWorkHTTPError(w, &core.Error{Code: "INVALID_REQUEST", Message: "invalid json"})
			return
		}
		if req.ExpectedRevision == nil || *req.ExpectedRevision <= 0 {
			writeWorkHTTPError(w, &core.Error{Code: "INVALID_REQUEST", Message: "expected_revision must be positive"})
			return
		}
		annotUUID, err := uuid.Parse(req.AnnotationID)
		if err != nil {
			writeWorkHTTPError(w, &core.Error{Code: "INVALID_REQUEST", Message: "invalid annotation_id"})
			return
		}
		slug := req.Project
		if slug == "" {
			slug = s.defaultSlug
		}
		rawItem := req.ItemUUID
		if rawItem == "" {
			rawItem = req.Item
		}
		if strings.TrimSpace(rawItem) == "" {
			writeWorkHTTPError(w, &core.Error{Code: "INVALID_REQUEST", Message: "item or item_uuid is required"})
			return
		}
		itemUUID, err := s.store.ResolveWorkItemUUID(ctx, s.collection, slug, rawItem)
		if err != nil {
			writeWorkHTTPError(w, err)
			return
		}
		if req.RequestID == "" {
			req.RequestID = uuid.NewString()
		}
		ann, err := s.store.EditProgressAnnotation(ctx, core.EditProgressAnnotationRequest{
			RequestID:        req.RequestID,
			Collection:       s.collection,
			ProjectSlug:      slug,
			ItemUUID:         itemUUID,
			AnnotationID:     annotUUID,
			Body:             req.Text,
			ExpectedRevision: *req.ExpectedRevision,
		})
		if err != nil {
			writeWorkHTTPError(w, err)
			return
		}
		_ = json.NewEncoder(w).Encode(ann)

	case r.URL.Path == "/api/work/blocker/add" && r.Method == http.MethodPost:
		var req struct {
			Project   string `json:"project"`
			ItemUUID  string `json:"item_uuid"`
			Item      string `json:"item"`
			Text      string `json:"text"`
			RequestID string `json:"request_id"`
		}
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			writeWorkHTTPError(w, &core.Error{Code: "INVALID_REQUEST", Message: "invalid json"})
			return
		}
		slug := req.Project
		if slug == "" {
			slug = s.defaultSlug
		}
		rawItem := req.ItemUUID
		if rawItem == "" {
			rawItem = req.Item
		}
		if strings.TrimSpace(rawItem) == "" {
			writeWorkHTTPError(w, &core.Error{Code: "INVALID_REQUEST", Message: "item or item_uuid is required"})
			return
		}
		itemUUID, err := s.store.ResolveWorkItemUUID(ctx, s.collection, slug, rawItem)
		if err != nil {
			writeWorkHTTPError(w, err)
			return
		}
		if req.RequestID == "" {
			req.RequestID = uuid.NewString()
		}
		ann, err := s.store.AddBlockerAnnotation(ctx, core.AddBlockerAnnotationRequest{
			RequestID:   req.RequestID,
			Collection:  s.collection,
			ProjectSlug: slug,
			ItemUUID:    itemUUID,
			Body:        req.Text,
		})
		if err != nil {
			writeWorkHTTPError(w, err)
			return
		}
		_ = json.NewEncoder(w).Encode(ann)

	case r.URL.Path == "/api/work/blocker/resolve" && r.Method == http.MethodPost:
		var req struct {
			Project          string `json:"project"`
			ItemUUID         string `json:"item_uuid"`
			Item             string `json:"item"`
			AnnotationID     string `json:"annotation_id"`
			ExpectedRevision *int64 `json:"expected_revision"`
			RequestID        string `json:"request_id"`
		}
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			writeWorkHTTPError(w, &core.Error{Code: "INVALID_REQUEST", Message: "invalid json"})
			return
		}
		if req.ExpectedRevision == nil || *req.ExpectedRevision <= 0 {
			writeWorkHTTPError(w, &core.Error{Code: "INVALID_REQUEST", Message: "expected_revision must be positive"})
			return
		}
		annotUUID, err := uuid.Parse(req.AnnotationID)
		if err != nil {
			writeWorkHTTPError(w, &core.Error{Code: "INVALID_REQUEST", Message: "invalid annotation_id"})
			return
		}
		slug := req.Project
		if slug == "" {
			slug = s.defaultSlug
		}
		rawItem := req.ItemUUID
		if rawItem == "" {
			rawItem = req.Item
		}
		if strings.TrimSpace(rawItem) == "" {
			writeWorkHTTPError(w, &core.Error{Code: "INVALID_REQUEST", Message: "item or item_uuid is required"})
			return
		}
		itemUUID, err := s.store.ResolveWorkItemUUID(ctx, s.collection, slug, rawItem)
		if err != nil {
			writeWorkHTTPError(w, err)
			return
		}
		if req.RequestID == "" {
			req.RequestID = uuid.NewString()
		}
		ann, err := s.store.ResolveBlockerAnnotation(ctx, core.ResolveBlockerAnnotationRequest{
			RequestID:        req.RequestID,
			Collection:       s.collection,
			ProjectSlug:      slug,
			ItemUUID:         itemUUID,
			AnnotationID:     annotUUID,
			ExpectedRevision: *req.ExpectedRevision,
		})
		if err != nil {
			writeWorkHTTPError(w, err)
			return
		}
		_ = json.NewEncoder(w).Encode(ann)

	case r.URL.Path == "/api/work/associate" && r.Method == http.MethodPost:
		var req struct {
			Project    string `json:"project"`
			ItemUUID   string `json:"item_uuid"`
			Item       string `json:"item"`
			TargetType string `json:"target_type"`
			TargetID   string `json:"target_id"`
			Rationale  string `json:"rationale"`
			RequestID  string `json:"request_id"`
		}
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			writeWorkHTTPError(w, &core.Error{Code: "INVALID_REQUEST", Message: "invalid json"})
			return
		}
		slug := req.Project
		if slug == "" {
			slug = s.defaultSlug
		}
		rawItem := req.ItemUUID
		if rawItem == "" {
			rawItem = req.Item
		}
		if strings.TrimSpace(rawItem) == "" {
			writeWorkHTTPError(w, &core.Error{Code: "INVALID_REQUEST", Message: "item or item_uuid is required"})
			return
		}
		itemUUID, err := s.store.ResolveWorkItemUUID(ctx, s.collection, slug, rawItem)
		if err != nil {
			writeWorkHTTPError(w, err)
			return
		}
		if req.RequestID == "" {
			req.RequestID = uuid.NewString()
		}
		assoc, err := s.store.AssociateWork(ctx, core.AssociateWorkRequest{
			RequestID:   req.RequestID,
			Collection:  s.collection,
			ProjectSlug: slug,
			ItemUUID:    itemUUID,
			TargetType:  req.TargetType,
			TargetID:    req.TargetID,
			Rationale:   req.Rationale,
		})
		if err != nil {
			writeWorkHTTPError(w, err)
			return
		}
		_ = json.NewEncoder(w).Encode(assoc)

	case r.URL.Path == "/api/work/unassociate" && r.Method == http.MethodPost:
		var req struct {
			Project          string `json:"project"`
			ItemUUID         string `json:"item_uuid"`
			Item             string `json:"item"`
			AssociationID    string `json:"association_id"`
			ExpectedRevision *int64 `json:"expected_revision"`
			RequestID        string `json:"request_id"`
		}
		if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
			writeWorkHTTPError(w, &core.Error{Code: "INVALID_REQUEST", Message: "invalid json"})
			return
		}
		if req.ExpectedRevision == nil || *req.ExpectedRevision <= 0 {
			writeWorkHTTPError(w, &core.Error{Code: "INVALID_REQUEST", Message: "expected_revision must be positive"})
			return
		}
		assocUUID, err := uuid.Parse(req.AssociationID)
		if err != nil {
			writeWorkHTTPError(w, &core.Error{Code: "INVALID_REQUEST", Message: "invalid association_id"})
			return
		}
		slug := req.Project
		if slug == "" {
			slug = s.defaultSlug
		}
		rawItem := req.ItemUUID
		if rawItem == "" {
			rawItem = req.Item
		}
		if strings.TrimSpace(rawItem) == "" {
			writeWorkHTTPError(w, &core.Error{Code: "INVALID_REQUEST", Message: "item or item_uuid is required"})
			return
		}
		itemUUID, err := s.store.ResolveWorkItemUUID(ctx, s.collection, slug, rawItem)
		if err != nil {
			writeWorkHTTPError(w, err)
			return
		}
		if req.RequestID == "" {
			req.RequestID = uuid.NewString()
		}
		assoc, err := s.store.UnassociateWork(ctx, core.UnassociateWorkRequest{
			RequestID:        req.RequestID,
			Collection:       s.collection,
			ProjectSlug:      slug,
			AssociationID:    assocUUID,
			ItemUUID:         itemUUID,
			ExpectedRevision: req.ExpectedRevision,
		})
		if err != nil {
			writeWorkHTTPError(w, err)
			return
		}
		_ = json.NewEncoder(w).Encode(assoc)

	default:
		http.NotFound(w, r)
	}
}

func runWorkServe(ctx context.Context, store *core.Store, args []string) error {
	f := flags("work serve")
	slug := f.String("project", "", "project slug")
	port := f.Int("port", 8085, "port to listen on loopback 127.0.0.1")
	collection := f.String("collection", "", "collection name")
	if err := f.Parse(args); err != nil {
		return err
	}
	if *collection == "" {
		*collection = store.Channel().Repo
		if *collection == "" {
			*collection = "default"
		}
	}

	srv, err := newWorkServer(store, *collection, *slug, *port)
	if err != nil {
		return err
	}

	listener, err := net.Listen("tcp", fmt.Sprintf("127.0.0.1:%d", *port))
	if err != nil {
		return err
	}
	defer listener.Close()

	actualPort := listener.Addr().(*net.TCPAddr).Port
	srv.port = actualPort
	fmt.Printf("Work view serving on http://127.0.0.1:%d/\nOne-time terminal launch code: %s (expires in 5 minutes)\n", actualPort, srv.launchCode)

	httpServer := &http.Server{
		Handler:           srv,
		ReadHeaderTimeout: 10 * time.Second,
		IdleTimeout:       60 * time.Second,
	}

	go func() {
		<-ctx.Done()
		_ = httpServer.Shutdown(context.Background())
	}()

	err = httpServer.Serve(listener)
	if errors.Is(err, http.ErrServerClosed) {
		return nil
	}
	return err
}
