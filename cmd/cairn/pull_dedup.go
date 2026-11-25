package main

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"io"
	"os"
	"path/filepath"
	"syscall"
	"time"

	"github.com/halbritt/cairn/core"
	"github.com/halbritt/cairn/localapi"
)

const (
	cliPullDedupMaxRecords = 256
	cliPullDedupMaxHandles = 1024
	cliPullDedupMaxReqs    = 256
	cliPullDedupTTLSeconds = 3600
)

type cliPullSpan struct {
	Offset       int  `json:"offset"`
	End          int  `json:"end"`
	EOF          bool `json:"eof,omitempty"`
	OpeningBytes int  `json:"opening_bytes,omitempty"`
}

type cliPullDelivered struct {
	Scope         string                `json:"scope"`
	RecordID      string                `json:"record_id"`
	WholeVersion  int                   `json:"whole_version,omitempty"`
	RequiresWhole bool                  `json:"requires_whole,omitempty"`
	Spans         map[int][]cliPullSpan `json:"spans,omitempty"`
	At            int64                 `json:"at"`
}

type cliPullHandle struct {
	ReceiptID     string `json:"receipt_id"`
	Handle        string `json:"handle"`
	RecordID      string `json:"record_id"`
	Version       int    `json:"version"`
	RequiresWhole bool   `json:"requires_whole,omitempty"`
	Scope         string `json:"scope,omitempty"`
	At            int64  `json:"at"`
}

type cliPullReqSig struct {
	RequestID    string `json:"request_id"`
	ReceiptID    string `json:"receipt_id"`
	Handle       string `json:"handle"`
	HasSpan      bool   `json:"has_span,omitempty"`
	Offset       int    `json:"offset,omitempty"`
	Length       int    `json:"length,omitempty"`
	OpeningBytes int    `json:"opening_bytes,omitempty"`
}

type cliPullDedupState struct {
	Handles       []cliPullHandle    `json:"handles,omitempty"`
	Delivered     []cliPullDelivered `json:"delivered,omitempty"`
	ReceiptScopes map[string]string  `json:"receipt_scopes,omitempty"`
	SeenRequests  []cliPullReqSig    `json:"seen_requests,omitempty"`
}

type cliPullAlreadyDelivered struct {
	AlreadyDelivered bool                  `json:"already_delivered"`
	RecordID         string                `json:"record_id"`
	Version          int                   `json:"version"`
	Span             *core.ByteSpanRequest `json:"span,omitempty"`
	Message          string                `json:"message"`
}

func cliPullDedupEnabled() bool {
	return os.Getenv("CAIRN_RECALL_DEDUP") != "0"
}

func cliPullDedupPath(session *core.AgentSessionRef, socketPath, tokenPath string) (string, error) {
	base, err := os.UserCacheDir()
	if err != nil || base == "" {
		return "", err
	}
	var key string
	switch {
	case session != nil:
		key = "session:" + socketPath + "\n" + session.AgentID + "/" + session.ExecutionID
	case os.Getenv("CAIRN_SESSION_ID") != "":
		key = "env:" + socketPath + "\n" + os.Getenv("CAIRN_SESSION_ID")
	default:
		key = "client:" + socketPath + "\n" + tokenPath
	}
	sum := sha256.Sum256([]byte(key))
	dir := filepath.Join(base, "cairn", "pull-dedup")
	if err := os.MkdirAll(dir, 0700); err != nil {
		return "", err
	}
	return filepath.Join(dir, hex.EncodeToString(sum[:])+".json"), nil
}

func withCLIPullDedupLock(session *core.AgentSessionRef, socketPath, tokenPath string, fn func(*cliPullDedupState, int64) bool) {
	if !cliPullDedupEnabled() {
		return
	}
	path, err := cliPullDedupPath(session, socketPath, tokenPath)
	if err != nil {
		return
	}
	lockFile, err := os.OpenFile(path+".lock", os.O_CREATE|os.O_RDWR, 0600)
	if err != nil {
		return
	}
	defer lockFile.Close()
	if err := syscall.Flock(int(lockFile.Fd()), syscall.LOCK_EX); err != nil {
		return
	}
	defer syscall.Flock(int(lockFile.Fd()), syscall.LOCK_UN)

	now := time.Now().Unix()
	var state cliPullDedupState
	if raw, err := os.ReadFile(path); err == nil {
		_ = json.Unmarshal(raw, &state)
	}
	pruneCLIPullDedup(&state, now)
	if !fn(&state, now) {
		return
	}
	raw, err := json.Marshal(state)
	if err != nil {
		return
	}
	tmp, err := os.CreateTemp(filepath.Dir(path), "tmp")
	if err != nil {
		return
	}
	if _, err := tmp.Write(raw); err != nil {
		tmp.Close()
		os.Remove(tmp.Name())
		return
	}
	if err := tmp.Close(); err != nil {
		os.Remove(tmp.Name())
		return
	}
	_ = os.Rename(tmp.Name(), path)
}

func pruneCLIPullDedup(state *cliPullDedupState, now int64) {
	keptHandles := make([]cliPullHandle, 0, len(state.Handles))
	for _, h := range state.Handles {
		if now-h.At <= cliPullDedupTTLSeconds {
			keptHandles = append(keptHandles, h)
		}
	}
	if len(keptHandles) > cliPullDedupMaxHandles {
		keptHandles = keptHandles[len(keptHandles)-cliPullDedupMaxHandles:]
	}
	state.Handles = keptHandles

	keptDelivered := make([]cliPullDelivered, 0, len(state.Delivered))
	for _, d := range state.Delivered {
		if now-d.At <= cliPullDedupTTLSeconds {
			keptDelivered = append(keptDelivered, d)
		}
	}
	if len(keptDelivered) > cliPullDedupMaxRecords {
		keptDelivered = keptDelivered[len(keptDelivered)-cliPullDedupMaxRecords:]
	}
	state.Delivered = keptDelivered

	if len(state.SeenRequests) > cliPullDedupMaxReqs {
		state.SeenRequests = state.SeenRequests[len(state.SeenRequests)-cliPullDedupMaxReqs:]
	}
}

func scopeKeyForCLI(scope core.Scope) string {
	return scope.Repo + "\n" + scope.TaskID + "\n" + scope.RunID
}

func recordAgentSearchHandles(session *core.AgentSessionRef, socketPath, tokenPath string, scope core.Scope, view agentSearchView) {
	if view.ReceiptID == "" {
		return
	}
	scopeKey := scopeKeyForCLI(scope)
	withCLIPullDedupLock(session, socketPath, tokenPath, func(state *cliPullDedupState, now int64) bool {
		if state.ReceiptScopes == nil {
			state.ReceiptScopes = make(map[string]string)
		}
		state.ReceiptScopes[view.ReceiptID] = scopeKey
		for _, entry := range view.Index {
			if entry.RecordID == "" || entry.Version <= 0 || entry.PullArguments.Handle == "" {
				continue
			}
			putCLIHandle(state, cliPullHandle{
				ReceiptID:     view.ReceiptID,
				Handle:        entry.PullArguments.Handle,
				RecordID:      entry.RecordID,
				Version:       entry.Version,
				RequiresWhole: entry.Class == "C" || len(entry.Conflicts) > 0,
				Scope:         scopeKey,
				At:            now,
			})
		}
		pruneCLIPullDedup(state, now)
		return true
	})
}

func putCLIHandle(state *cliPullDedupState, item cliPullHandle) {
	for i, existing := range state.Handles {
		if existing.ReceiptID == item.ReceiptID && existing.Handle == item.Handle {
			state.Handles = append(state.Handles[:i], state.Handles[i+1:]...)
			break
		}
	}
	state.Handles = append(state.Handles, item)
}

func cliReqSignature(req core.ExpandRequest) cliPullReqSig {
	sig := cliPullReqSig{
		RequestID:    req.RequestID,
		ReceiptID:    req.ReceiptID,
		Handle:       req.Handle,
		OpeningBytes: req.OpeningBytes,
	}
	if req.Span != nil {
		sig.HasSpan = true
		sig.Offset = req.Span.Offset
		sig.Length = req.Span.Length
	}
	return sig
}

func checkCLIPullDedup(session *core.AgentSessionRef, socketPath, tokenPath string, req core.ExpandRequest) (cliPullAlreadyDelivered, bool) {
	if !cliPullDedupEnabled() || req.Force || req.ReceiptID == "" || req.Handle == "" {
		return cliPullAlreadyDelivered{}, false
	}
	if req.OpeningBytes < 0 || req.OpeningBytes > 768 || (req.OpeningBytes != 0 && req.Span == nil) {
		return cliPullAlreadyDelivered{}, false
	}
	if req.Span != nil && (req.Span.Offset < 0 || req.Span.Offset >= 65536 || req.Span.Length <= 0 || req.Span.Length > 65536) {
		return cliPullAlreadyDelivered{}, false
	}
	var hit cliPullAlreadyDelivered
	var found bool
	withCLIPullDedupLock(session, socketPath, tokenPath, func(state *cliPullDedupState, now int64) bool {
		if req.RequestID != "" {
			sig := cliReqSignature(req)
			for _, prev := range state.SeenRequests {
				if prev == sig {
					return false
				}
			}
		}
		var ref *cliPullHandle
		for i := range state.Handles {
			if state.Handles[i].ReceiptID == req.ReceiptID && state.Handles[i].Handle == req.Handle {
				ref = &state.Handles[i]
				break
			}
		}
		if ref == nil {
			return false
		}
		scope := ref.Scope
		if scope == "" && state.ReceiptScopes != nil {
			scope = state.ReceiptScopes[req.ReceiptID]
		}
		for i := range state.Delivered {
			d := &state.Delivered[i]
			if d.Scope != scope || d.RecordID != ref.RecordID {
				continue
			}
			if req.Span != nil && (ref.RequiresWhole || d.RequiresWhole) {
				return false
			}
			covered := false
			if d.WholeVersion == ref.Version {
				covered = true
			} else if req.Span != nil {
				reqEnd := req.Span.Offset + req.Span.Length
				for _, s := range d.Spans[ref.Version] {
					if s.Offset <= req.Span.Offset && (s.End >= reqEnd || s.EOF) && req.OpeningBytes <= s.OpeningBytes {
						covered = true
						break
					}
				}
			}
			if covered {
				d.At = now
				item := *d
				state.Delivered = append(append(state.Delivered[:i], state.Delivered[i+1:]...), item)
				hit = cliPullAlreadyDelivered{
					AlreadyDelivered: true,
					RecordID:         ref.RecordID,
					Version:          ref.Version,
					Span:             req.Span,
					Message:          "already delivered in this session; pass --force to re-read",
				}
				found = true
				return true
			}
		}
		return false
	})
	return hit, found
}

func recordCLIPullResult(session *core.AgentSessionRef, socketPath, tokenPath string, req core.ExpandRequest, raw json.RawMessage) {
	if !cliPullDedupEnabled() {
		return
	}
	var exp core.Expansion
	if err := json.Unmarshal(raw, &exp); err != nil {
		return
	}
	rec := exp.Selection.Record
	if rec.RecordID == "" || rec.Version <= 0 {
		return
	}
	withCLIPullDedupLock(session, socketPath, tokenPath, func(state *cliPullDedupState, now int64) bool {
		if req.RequestID != "" {
			sig := cliReqSignature(req)
			exists := false
			for _, prev := range state.SeenRequests {
				if prev == sig {
					exists = true
					break
				}
			}
			if !exists {
				state.SeenRequests = append(state.SeenRequests, sig)
			}
		}
		scope := ""
		if state.ReceiptScopes != nil {
			scope = state.ReceiptScopes[req.ReceiptID]
		}
		requiresWhole := rec.Class == "C" || len(exp.Competing) > 0 || len(exp.Selection.Conflicts) > 0
		if req.ReceiptID != "" && req.Handle != "" {
			putCLIHandle(state, cliPullHandle{
				ReceiptID:     req.ReceiptID,
				Handle:        req.Handle,
				RecordID:      rec.RecordID,
				Version:       rec.Version,
				RequiresWhole: requiresWhole,
				Scope:         scope,
				At:            now,
			})
		}
		if exp.Span != nil {
			d := ensureCLIDelivered(state, scope, rec.RecordID, now)
			if d.Spans == nil {
				d.Spans = make(map[int][]cliPullSpan)
			}
			eof := exp.Span.TotalBytes > 0 && exp.Span.End >= exp.Span.TotalBytes
			d.Spans[rec.Version] = append(d.Spans[rec.Version], cliPullSpan{
				Offset:       exp.Span.Offset,
				End:          exp.Span.End,
				EOF:          eof,
				OpeningBytes: req.OpeningBytes,
			})
		} else {
			d := ensureCLIDelivered(state, scope, rec.RecordID, now)
			d.WholeVersion = rec.Version
			d.RequiresWhole = requiresWhole
			for _, comp := range exp.Competing {
				if comp.Record.RecordID != "" && comp.Record.Version > 0 {
					cd := ensureCLIDelivered(state, scope, comp.Record.RecordID, now)
					cd.WholeVersion = comp.Record.Version
					cd.RequiresWhole = true
				}
			}
		}
		pruneCLIPullDedup(state, now)
		return true
	})
}

func ensureCLIDelivered(state *cliPullDedupState, scope, recordID string, now int64) *cliPullDelivered {
	for i := range state.Delivered {
		if state.Delivered[i].Scope == scope && state.Delivered[i].RecordID == recordID {
			item := state.Delivered[i]
			item.At = now
			state.Delivered = append(append(state.Delivered[:i], state.Delivered[i+1:]...), item)
			return &state.Delivered[len(state.Delivered)-1]
		}
	}
	state.Delivered = append(state.Delivered, cliPullDelivered{
		Scope:    scope,
		RecordID: recordID,
		At:       now,
	})
	return &state.Delivered[len(state.Delivered)-1]
}

func agentPullJSON(ctx context.Context, client *localapi.Client, session *core.AgentSessionRef, socketPath, tokenPath string, input io.Reader) (any, error) {
	limit := localapi.RequestBodyLimit("expand")
	body, err := io.ReadAll(io.LimitReader(input, limit+1))
	if err != nil {
		return nil, err
	}
	if int64(len(body)) > limit {
		return nil, invalid("request exceeds 128 KiB")
	}
	if !json.Valid(body) {
		return nil, invalid("expected a JSON request")
	}
	var req core.ExpandRequest
	if err := json.Unmarshal(body, &req); err == nil {
		if hit, ok := checkCLIPullDedup(session, socketPath, tokenPath, req); ok {
			rawHit, err := json.Marshal(hit)
			if err != nil {
				return nil, err
			}
			return json.RawMessage(rawHit), nil
		}
	}
	var result json.RawMessage
	if err = client.Call(ctx, "expand", json.RawMessage(body), &result); err != nil {
		return nil, err
	}
	recordCLIPullResult(session, socketPath, tokenPath, req, result)
	return result, nil
}
