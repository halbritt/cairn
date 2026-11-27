package main

import (
	"context"
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"net/http"
	"sync"
	"time"

	"github.com/halbritt/cairn/core"
)

// The page holds one server-sent event stream. It carries two things with
// different rules (docs/swarm-view.md):
//
//   - log: each campaign event once, in commit order. The stream ID of an
//     event is its cursor. A client that reconnects sends the last ID it
//     received and continues after it.
//   - snapshot: the whole current state, sent when it differs from the last
//     one sent on this connection.
//
// The database is read every tick, and only while a browser is connected.
// The Herdr read runs on its own loop with its own timestamp, so that a slow
// or failed Herdr leaves the delivery data current and marks only the pane
// data stale.

// swarmPaneView is the latest pane observation with its staleness.
type swarmPaneView struct {
	swarmPaneObservation
	// Stale is set when the observation is older than the loop should
	// allow: Herdr is slow, hung or failing.
	Stale bool `json:"stale"`
}

// swarmWindowView is the latest context-window observation with its
// staleness.
type swarmWindowView struct {
	swarmWindowObservation
	Stale bool `json:"stale"`
}

// swarmLoop makes one kind of host-local observation on its own schedule
// while at least one browser needs it. Each loop has its own cache and its
// own time, so that a slow reader delays nothing else. Nothing it reads is
// written to the store.
type swarmLoop[T any] struct {
	read       func(context.Context) T
	observed   func(*T) *time.Time // the observation's own time field
	interval   time.Duration
	staleAfter time.Duration

	mu      sync.Mutex
	users   int
	cancel  context.CancelFunc
	latest  *T
	arrived chan struct{}
}

func newSwarmPaneLoop(read swarmPaneReader, interval, staleAfter time.Duration) *swarmLoop[swarmPaneObservation] {
	return &swarmLoop[swarmPaneObservation]{read: read, observed: func(o *swarmPaneObservation) *time.Time { return &o.ObservedAt }, interval: interval, staleAfter: staleAfter, arrived: make(chan struct{})}
}

func newSwarmWindowLoop(read swarmWindowReader, interval, staleAfter time.Duration) *swarmLoop[swarmWindowObservation] {
	return &swarmLoop[swarmWindowObservation]{read: read, observed: func(o *swarmWindowObservation) *time.Time { return &o.ObservedAt }, interval: interval, staleAfter: staleAfter, arrived: make(chan struct{})}
}

func (l *swarmLoop[T]) acquire() {
	l.mu.Lock()
	defer l.mu.Unlock()
	l.users++
	if l.users == 1 {
		ctx, cancel := context.WithCancel(context.Background())
		l.cancel = cancel
		go l.run(ctx)
	}
}

func (l *swarmLoop[T]) release() {
	l.mu.Lock()
	defer l.mu.Unlock()
	l.users--
	if l.users == 0 && l.cancel != nil {
		l.cancel()
		l.cancel = nil
	}
}

func (l *swarmLoop[T]) run(ctx context.Context) {
	for {
		// One observation makes several bounded calls; bound the whole.
		readCtx, cancel := context.WithTimeout(ctx, 20*time.Second)
		observation := l.read(readCtx)
		cancel()
		if ctx.Err() != nil {
			return
		}
		if at := l.observed(&observation); at.IsZero() {
			*at = time.Now()
		}
		l.mu.Lock()
		l.latest = &observation
		close(l.arrived)
		l.arrived = make(chan struct{})
		l.mu.Unlock()
		select {
		case <-ctx.Done():
			return
		case <-time.After(l.interval):
		}
	}
}

// get returns the latest observation and whether it is stale. ok is false
// before the first read completes.
func (l *swarmLoop[T]) get(now time.Time) (observation T, stale, ok bool) {
	l.mu.Lock()
	defer l.mu.Unlock()
	if l.latest == nil {
		return observation, false, false
	}
	observation = *l.latest
	return observation, now.Sub(*l.observed(&observation)) > l.staleAfter, true
}

// first waits for the first observation, up to the limit.
func (l *swarmLoop[T]) first(ctx context.Context, limit time.Duration) {
	l.mu.Lock()
	arrived, have := l.arrived, l.latest != nil
	l.mu.Unlock()
	if !have {
		select {
		case <-arrived:
		case <-ctx.Done():
		case <-time.After(limit):
		}
	}
}

// paneView returns the latest pane observation. Before the first read
// completes the status is "pending".
func (s *swarmServer) paneView(now time.Time) swarmPaneView {
	observation, stale, ok := s.panes.get(now)
	if !ok {
		return swarmPaneView{swarmPaneObservation: swarmPaneObservation{Status: "pending", Panes: map[string]swarmPane{}}}
	}
	if observation.Panes == nil {
		observation.Panes = map[string]swarmPane{}
	}
	return swarmPaneView{swarmPaneObservation: observation, Stale: stale}
}

// windowView returns the latest context-window observation, read from the
// harness transcripts of this host.
func (s *swarmServer) windowView(now time.Time) swarmWindowView {
	observation, stale, ok := s.windows.get(now)
	if !ok {
		return swarmWindowView{swarmWindowObservation: swarmWindowObservation{Status: "pending", Windows: map[string]swarmWindow{}, Unknown: map[string]string{}}}
	}
	if observation.Windows == nil {
		observation.Windows = map[string]swarmWindow{}
	}
	if observation.Unknown == nil {
		observation.Unknown = map[string]string{}
	}
	return swarmWindowView{swarmWindowObservation: observation, Stale: stale}
}

type swarmStreamWriter struct {
	w          http.ResponseWriter
	flusher    http.Flusher
	controller *http.ResponseController
}

// send writes one event. JSON has no raw line break, so the data is one line.
func (s *swarmStreamWriter) send(event, id string, data any) error {
	raw, err := json.Marshal(data)
	if err != nil {
		return err
	}
	_ = s.controller.SetWriteDeadline(time.Now().Add(15 * time.Second))
	if id != "" {
		_, err = fmt.Fprintf(s.w, "event: %s\nid: %s\ndata: %s\n\n", event, id, raw)
	} else {
		_, err = fmt.Fprintf(s.w, "event: %s\ndata: %s\n\n", event, raw)
	}
	if err != nil {
		return err
	}
	s.flusher.Flush()
	return nil
}

func swarmSnapshotDigest(snapshot core.SwarmSnapshot) [sha256.Size]byte {
	// The observation time changes on every read; the state may not.
	snapshot.ObservedAt = time.Time{}
	raw, _ := json.Marshal(snapshot)
	return sha256.Sum256(raw)
}

func swarmPaneDigest(view swarmPaneView) [sha256.Size]byte {
	view.ObservedAt = time.Time{}
	raw, _ := json.Marshal(view)
	return sha256.Sum256(raw)
}

func swarmWindowDigest(view swarmWindowView) [sha256.Size]byte {
	view.ObservedAt = time.Time{}
	raw, _ := json.Marshal(view)
	return sha256.Sum256(raw)
}

// swarmStreamFatal lists the refusals after which the same stream cannot
// continue. The page then decides: a refused cursor starts a new stream
// without one, an unknown campaign is shown as such.
var swarmStreamFatal = map[string]bool{
	"STALE_CURSOR": true, "INVALID_REQUEST": true, "NOT_FOUND": true, "AUTHORITY_DENIED": true, "SCHEMA_MISMATCH": true,
}

// handleStream serves one stream connection for a signed-in session.
//
// Once the session is accepted the answer is always an event stream, also
// when the first read is refused or fails. A browser's EventSource gives up
// for good on any other answer and cannot read its status or body, so a
// reconnect that carries a cursor from before a restore, or one that arrives
// while the store is down, would otherwise leave the page stuck until a
// manual reload. A refusal is a "reset" event; a failed read is a "fault"
// event and the server keeps trying.
//
// The stream lives no longer than its session: it ends at logout, at the
// session's lifetime, and when the session limit evicts the session.
func (s *swarmServer) handleStream(w http.ResponseWriter, r *http.Request, sessionID string, session swarmBrowserSession) {
	if r.Method != http.MethodGet {
		swarmMethod(w, http.MethodGet)
		return
	}
	flusher, ok := w.(http.Flusher)
	if !ok {
		swarmRefuse(w, http.StatusInternalServerError, "INTERNAL", "streaming is not supported by this connection")
		return
	}
	ctx := r.Context()
	request := core.SwarmStreamRequest{Repo: s.collection, Campaign: r.URL.Query().Get("campaign"), Cursor: r.Header.Get("Last-Event-ID")}
	if request.Cursor == "" {
		request.Cursor = r.URL.Query().Get("cursor")
	}
	header := w.Header()
	header.Set("Content-Type", "text/event-stream; charset=utf-8")
	header.Set("Cache-Control", "no-store")
	header.Set("X-Accel-Buffering", "no")
	w.WriteHeader(http.StatusOK)
	stream := &swarmStreamWriter{w: w, flusher: flusher, controller: http.NewResponseController(w)}

	s.panes.acquire()
	defer s.panes.release()
	s.windows.acquire()
	defer s.windows.release()
	operator := ""
	if s.store != nil {
		operator = s.store.Channel().Principal
	}
	if err := stream.send("hello", "", map[string]any{
		"schema": core.SwarmStreamSchema, "collection": s.collection, "campaign": request.Campaign,
		"operator_principal": operator, "tick_ms": s.tick.Milliseconds(),
		"log_window": core.SwarmFlowEventWindow, "plane_url": s.planeURL, "server_time": s.now(),
	}); err != nil {
		return
	}
	signedOut := func() {
		_ = stream.send("reset", "", map[string]any{"code": "UNAUTHENTICATED", "error": "the session ended; sign in again"})
	}
	// The stream's reads and frames stop when the session ends, not only
	// between ticks: a read that is waiting on the store is cancelled, and no
	// frame is written after logout, eviction or the session's lifetime.
	streamCtx, cancel := context.WithCancel(ctx)
	defer cancel()
	lifetime := time.NewTimer(time.Until(session.expires))
	defer lifetime.Stop()
	go func() {
		select {
		case <-session.revoked:
		case <-lifetime.C:
		case <-streamCtx.Done():
		}
		cancel()
	}()
	// emit writes one frame for a session that still stands. It reports
	// false when the stream must end.
	emit := func(event, id string, data any) bool {
		if !s.sessionAlive(sessionID) {
			signedOut()
			return false
		}
		return stream.send(event, id, data) == nil
	}
	// wait pauses for one tick. It reports false when the stream must end.
	wait := func() bool {
		timer := time.NewTimer(s.tick)
		defer timer.Stop()
		select {
		case <-streamCtx.Done():
			if ctx.Err() == nil {
				signedOut()
			}
			return false
		case <-timer.C:
			return true
		}
	}
	var lastSnapshot, lastPanes, lastWindows [sha256.Size]byte
	sentSnapshot, sentPanes, sentWindows := false, false, false
	for {
		// Nothing is read for a session that has ended.
		if !s.sessionAlive(sessionID) {
			signedOut()
			return
		}
		read, err := s.reads.ReadSwarmStream(streamCtx, request)
		if ctx.Err() != nil {
			return
		}
		if streamCtx.Err() != nil || !s.sessionAlive(sessionID) {
			// The session ended while the read was in flight. Its result,
			// or its failure, is not reported.
			signedOut()
			return
		}
		if err != nil {
			code := core.Code(err)
			if swarmStreamFatal[code] {
				emit("reset", "", map[string]any{"code": code, "error": err.Error()})
				return
			}
			// The store could not be read. Keep the connection and say so;
			// the host observations are still their own.
			if !emit("fault", "", map[string]any{"code": code, "error": err.Error(), "at": s.now()}) || !wait() {
				return
			}
			continue
		}
		if read.Resync && !emit("resync", "", map[string]any{"reason": read.ResyncReason, "membership_epoch": read.Snapshot.MembershipEpoch}) {
			return
		}
		for _, event := range read.Events {
			if !emit("log", event.Cursor, event) {
				return
			}
		}
		if digest := swarmSnapshotDigest(read.Snapshot); !sentSnapshot || digest != lastSnapshot {
			if !emit("snapshot", read.Cursor, read.Snapshot) {
				return
			}
			lastSnapshot, sentSnapshot = digest, true
		}
		panes := s.paneView(s.now())
		if digest := swarmPaneDigest(panes); !sentPanes || digest != lastPanes {
			if !emit("herdr", "", panes) {
				return
			}
			lastPanes, sentPanes = digest, true
		}
		windows := s.windowView(s.now())
		if digest := swarmWindowDigest(windows); !sentWindows || digest != lastWindows {
			if !emit("window", "", windows) {
				return
			}
			lastWindows, sentWindows = digest, true
		}
		// Each observation carries its own time, so the page can show the
		// age of each and mark one stale without hiding the other.
		if !emit("tick", read.Cursor, map[string]any{
			"db_observed_at": read.Snapshot.ObservedAt,
			"panes":          map[string]any{"observed_at": panes.ObservedAt, "status": panes.Status, "error": panes.Error, "stale": panes.Stale},
			"window":         map[string]any{"observed_at": windows.ObservedAt, "status": windows.Status, "error": windows.Error, "stale": windows.Stale},
		}) {
			return
		}
		request.Cursor = read.Cursor
		if !read.More && !wait() {
			return
		}
	}
}
