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

// swarmPaneLoop reads Herdr on its own schedule while at least one browser
// needs it. Nothing it reads is written to the store.
type swarmPaneLoop struct {
	read       swarmPaneReader
	interval   time.Duration
	staleAfter time.Duration

	mu      sync.Mutex
	users   int
	cancel  context.CancelFunc
	latest  *swarmPaneObservation
	arrived chan struct{}
}

func newSwarmPaneLoop(read swarmPaneReader, interval, staleAfter time.Duration) *swarmPaneLoop {
	return &swarmPaneLoop{read: read, interval: interval, staleAfter: staleAfter, arrived: make(chan struct{})}
}

func (l *swarmPaneLoop) acquire() {
	l.mu.Lock()
	defer l.mu.Unlock()
	l.users++
	if l.users == 1 {
		ctx, cancel := context.WithCancel(context.Background())
		l.cancel = cancel
		go l.run(ctx)
	}
}

func (l *swarmPaneLoop) release() {
	l.mu.Lock()
	defer l.mu.Unlock()
	l.users--
	if l.users == 0 && l.cancel != nil {
		l.cancel()
		l.cancel = nil
	}
}

func (l *swarmPaneLoop) run(ctx context.Context) {
	for {
		// One observation makes several bounded Herdr calls; bound the whole.
		readCtx, cancel := context.WithTimeout(ctx, 20*time.Second)
		observation := l.read(readCtx)
		cancel()
		if ctx.Err() != nil {
			return
		}
		if observation.ObservedAt.IsZero() {
			observation.ObservedAt = time.Now()
		}
		if observation.Panes == nil {
			observation.Panes = map[string]swarmPane{}
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

// view returns the latest observation. Before the first read completes the
// status is "pending".
func (l *swarmPaneLoop) view(now time.Time) swarmPaneView {
	l.mu.Lock()
	defer l.mu.Unlock()
	if l.latest == nil {
		return swarmPaneView{swarmPaneObservation: swarmPaneObservation{Status: "pending", Panes: map[string]swarmPane{}}}
	}
	return swarmPaneView{swarmPaneObservation: *l.latest, Stale: now.Sub(l.latest.ObservedAt) > l.staleAfter}
}

// wait returns the first observation that arrives within the limit, or the
// current view.
func (l *swarmPaneLoop) wait(ctx context.Context, now func() time.Time, limit time.Duration) swarmPaneView {
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
	return l.view(now())
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

// swarmStreamFatal lists the refusals after which the same stream cannot
// continue. The page then starts again without a cursor.
var swarmStreamFatal = map[string]bool{
	"STALE_CURSOR": true, "INVALID_REQUEST": true, "NOT_FOUND": true, "AUTHORITY_DENIED": true, "SCHEMA_MISMATCH": true,
}

func (s *swarmServer) handleStream(w http.ResponseWriter, r *http.Request) {
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
	// The first read decides the status line: an unknown campaign or a
	// refused cursor is an error response, not an empty stream.
	read, err := s.store.ReadSwarmStream(ctx, request)
	if err != nil {
		writeSwarmError(w, err)
		return
	}
	header := w.Header()
	header.Set("Content-Type", "text/event-stream; charset=utf-8")
	header.Set("Cache-Control", "no-store")
	header.Set("X-Accel-Buffering", "no")
	w.WriteHeader(http.StatusOK)
	stream := &swarmStreamWriter{w: w, flusher: flusher, controller: http.NewResponseController(w)}

	s.panes.acquire()
	defer s.panes.release()
	if err = stream.send("hello", "", map[string]any{
		"schema": core.SwarmStreamSchema, "collection": s.collection, "campaign": read.Snapshot.Campaign.Campaign,
		"operator_principal": read.Snapshot.OperatorPrincipal, "tick_ms": s.tick.Milliseconds(),
		"log_window": core.SwarmFlowEventWindow, "server_time": s.now(),
	}); err != nil {
		return
	}
	var lastSnapshot, lastPanes [sha256.Size]byte
	sentSnapshot, sentPanes := false, false
	for {
		if read.Resync {
			if err = stream.send("resync", "", map[string]any{"reason": read.ResyncReason, "membership_epoch": read.Snapshot.MembershipEpoch}); err != nil {
				return
			}
		}
		for _, event := range read.Events {
			if err = stream.send("log", event.Cursor, event); err != nil {
				return
			}
		}
		if digest := swarmSnapshotDigest(read.Snapshot); !sentSnapshot || digest != lastSnapshot {
			if err = stream.send("snapshot", read.Cursor, read.Snapshot); err != nil {
				return
			}
			lastSnapshot, sentSnapshot = digest, true
		}
		panes := s.panes.view(s.now())
		if digest := swarmPaneDigest(panes); !sentPanes || digest != lastPanes {
			if err = stream.send("herdr", "", panes); err != nil {
				return
			}
			lastPanes, sentPanes = digest, true
		}
		// Each observation carries its own time, so the page can show the
		// age of each and mark one stale without hiding the other.
		if err = stream.send("tick", read.Cursor, map[string]any{
			"db_observed_at": read.Snapshot.ObservedAt,
			"panes":          map[string]any{"observed_at": panes.ObservedAt, "status": panes.Status, "error": panes.Error, "stale": panes.Stale},
		}); err != nil {
			return
		}
		request.Cursor = read.Cursor
		if !read.More && !swarmSleep(ctx, s.tick) {
			return
		}
		for {
			next, rErr := s.store.ReadSwarmStream(ctx, request)
			if rErr == nil {
				read = next
				break
			}
			if ctx.Err() != nil {
				return
			}
			code := core.Code(rErr)
			if swarmStreamFatal[code] {
				_ = stream.send("reset", "", map[string]any{"code": code, "error": rErr.Error()})
				return
			}
			// The database could not be read. Keep the connection and say
			// so; the pane data is still its own observation.
			if err = stream.send("fault", "", map[string]any{"code": code, "error": rErr.Error(), "at": s.now()}); err != nil {
				return
			}
			if !swarmSleep(ctx, s.tick) {
				return
			}
		}
	}
}

func swarmSleep(ctx context.Context, d time.Duration) bool {
	timer := time.NewTimer(d)
	defer timer.Stop()
	select {
	case <-ctx.Done():
		return false
	case <-timer.C:
		return true
	}
}
