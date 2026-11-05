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
	"sync"
	"time"

	"github.com/halbritt/cairn/core"
	"github.com/halbritt/cairn/web/workview"
)

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

	// API authentication gate
	sess, authenticated := s.validateSession(r)
	if !authenticated && r.URL.Path != "/api/auth/login" {
		http.Error(w, `{"error":"unauthorized"}`, http.StatusUnauthorized)
		return
	}

	// CSRF validation on mutating API requests
	if r.Method == http.MethodPost || r.Method == http.MethodPut || r.Method == http.MethodDelete {
		if r.URL.Path != "/api/auth/login" {
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
			http.Error(w, fmt.Sprintf(`{"error":%q}`, err.Error()), http.StatusInternalServerError)
			return
		}
		_ = json.NewEncoder(w).Encode(map[string]any{"projects": projects})

	case r.URL.Path == "/api/work/items" && r.Method == http.MethodGet:
		slug := r.URL.Query().Get("project")
		if slug == "" {
			slug = s.defaultSlug
		}
		proj, err := s.store.GetWorkProject(ctx, s.collection, slug)
		if err != nil {
			http.Error(w, fmt.Sprintf(`{"error":%q}`, err.Error()), http.StatusNotFound)
			return
		}
		items, _, err := s.store.ListWorkItems(ctx, s.collection, slug, "", 0, 0)
		if err != nil {
			http.Error(w, fmt.Sprintf(`{"error":%q}`, err.Error()), http.StatusInternalServerError)
			return
		}
		_ = json.NewEncoder(w).Encode(map[string]any{"project": proj, "items": items})

	case r.URL.Path == "/api/work/item" && r.Method == http.MethodGet:
		slug := r.URL.Query().Get("project")
		if slug == "" {
			slug = s.defaultSlug
		}
		itemUUIDStr := r.URL.Query().Get("item")
		if itemUUIDStr == "" {
			http.Error(w, `{"error":"missing item identifier or uuid"}`, http.StatusBadRequest)
			return
		}
		detail, err := s.store.GetWorkItemDetail(ctx, s.collection, slug, itemUUIDStr)
		if err != nil {
			http.Error(w, fmt.Sprintf(`{"error":%q}`, err.Error()), http.StatusNotFound)
			return
		}
		_ = json.NewEncoder(w).Encode(detail)

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
