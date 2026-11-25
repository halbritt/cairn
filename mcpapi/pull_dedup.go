package mcpapi

import (
	"os"
	"sync"

	"github.com/halbritt/cairn/core"
)

const maxTrackedPullRecords = 256
const maxTrackedPullHandles = 1024
const maxTrackedPullRequests = 256

type pullHandleKey struct {
	receiptID string
	handle    string
}

type pullRecordVersion struct {
	recordID      string
	version       int
	requiresWhole bool
}

type pullSpanRange struct {
	offset       int
	end          int
	eof          bool
	openingBytes int
}

type pullDeliveredRecord struct {
	wholeVersion  int
	requiresWhole bool
	spans         map[int][]pullSpanRange
}

type pullRequestSig struct {
	receiptID    string
	handle       string
	hasSpan      bool
	offset       int
	length       int
	openingBytes int
}

type pullAlreadyDelivered struct {
	AlreadyDelivered bool                  `json:"already_delivered"`
	RecordID         string                `json:"record_id"`
	Version          int                   `json:"version"`
	Span             *core.ByteSpanRequest `json:"span,omitempty"`
	Message          string                `json:"message"`
}

type pullDedupTracker struct {
	mu           sync.Mutex
	handles      map[pullHandleKey]pullRecordVersion
	handleOrder  []pullHandleKey
	delivered    map[string]*pullDeliveredRecord
	recordOrder  []string
	seenRequests map[string]pullRequestSig
	requestOrder []string
}

func newPullDedupTracker() *pullDedupTracker {
	return &pullDedupTracker{
		handles:      make(map[pullHandleKey]pullRecordVersion),
		delivered:    make(map[string]*pullDeliveredRecord),
		seenRequests: make(map[string]pullRequestSig),
	}
}

func pullDedupEnabled() bool {
	return os.Getenv("CAIRN_RECALL_DEDUP") != "0"
}

func requestSig(args core.ExpandRequest) pullRequestSig {
	sig := pullRequestSig{
		receiptID:    args.ReceiptID,
		handle:       args.Handle,
		openingBytes: args.OpeningBytes,
	}
	if args.Span != nil {
		sig.hasSpan = true
		sig.offset = args.Span.Offset
		sig.length = args.Span.Length
	}
	return sig
}

func (t *pullDedupTracker) recordSearch(index core.IndexResult) {
	if t == nil || !pullDedupEnabled() || index.Package.ReceiptID == "" || len(index.Handles) == 0 {
		return
	}
	requiresWholeByRef := make(map[string]bool, len(index.Package.Semantic.Index))
	for _, e := range index.Package.Semantic.Index {
		if e.Class == "C" || len(e.Conflicts) > 0 {
			requiresWholeByRef[e.RecordID] = true
		}
	}
	t.mu.Lock()
	defer t.mu.Unlock()
	for _, h := range index.Handles {
		t.putHandleLocked(pullHandleKey{receiptID: index.Package.ReceiptID, handle: h.Handle}, pullRecordVersion{
			recordID:      h.RecordID,
			version:       h.Version,
			requiresWhole: requiresWholeByRef[h.RecordID],
		})
	}
}

func (t *pullDedupTracker) putHandleLocked(key pullHandleKey, val pullRecordVersion) {
	if _, exists := t.handles[key]; !exists {
		t.handleOrder = append(t.handleOrder, key)
		for len(t.handleOrder) > maxTrackedPullHandles {
			evict := t.handleOrder[0]
			t.handleOrder = t.handleOrder[1:]
			delete(t.handles, evict)
		}
	}
	t.handles[key] = val
}

func (t *pullDedupTracker) checkPull(args core.ExpandRequest) (pullAlreadyDelivered, bool) {
	if t == nil || !pullDedupEnabled() || args.Force {
		return pullAlreadyDelivered{}, false
	}
	if args.OpeningBytes < 0 || args.OpeningBytes > 768 || (args.OpeningBytes != 0 && args.Span == nil) {
		return pullAlreadyDelivered{}, false
	}
	if args.Span != nil && (args.Span.Offset < 0 || args.Span.Offset >= 65536 || args.Span.Length <= 0 || args.Span.Length > 65536) {
		return pullAlreadyDelivered{}, false
	}
	t.mu.Lock()
	defer t.mu.Unlock()
	if args.RequestID != "" {
		if prev, ok := t.seenRequests[args.RequestID]; ok && prev == requestSig(args) {
			return pullAlreadyDelivered{}, false
		}
	}
	ref, ok := t.handles[pullHandleKey{receiptID: args.ReceiptID, handle: args.Handle}]
	if !ok || ref.recordID == "" || ref.version <= 0 {
		return pullAlreadyDelivered{}, false
	}
	rec, ok := t.delivered[ref.recordID]
	if !ok {
		return pullAlreadyDelivered{}, false
	}
	if args.Span != nil && (ref.requiresWhole || rec.requiresWhole) {
		return pullAlreadyDelivered{}, false
	}
	if ref.requiresWhole && !rec.requiresWhole {
		return pullAlreadyDelivered{}, false
	}
	covered := false
	if rec.wholeVersion == ref.version {
		covered = true
	} else if args.Span != nil {
		reqEnd := args.Span.Offset + args.Span.Length
		for _, s := range rec.spans[ref.version] {
			if s.offset <= args.Span.Offset && (s.end >= reqEnd || s.eof) && openingBytesCovered(args.OpeningBytes, s.openingBytes) {
				covered = true
				break
			}
		}
	}
	if !covered {
		return pullAlreadyDelivered{}, false
	}
	t.touchRecordLocked(ref.recordID)
	return pullAlreadyDelivered{
		AlreadyDelivered: true,
		RecordID:         ref.recordID,
		Version:          ref.version,
		Span:             args.Span,
		Message:          "already delivered in this session; pass force=true to re-read",
	}, true
}

func (t *pullDedupTracker) recordPull(args core.ExpandRequest, exp core.Expansion) {
	if t == nil || !pullDedupEnabled() {
		return
	}
	t.mu.Lock()
	defer t.mu.Unlock()
	if args.RequestID != "" {
		if _, exists := t.seenRequests[args.RequestID]; !exists {
			t.requestOrder = append(t.requestOrder, args.RequestID)
			for len(t.requestOrder) > maxTrackedPullRequests {
				evict := t.requestOrder[0]
				t.requestOrder = t.requestOrder[1:]
				delete(t.seenRequests, evict)
			}
		}
		t.seenRequests[args.RequestID] = requestSig(args)
	}
	rec := exp.Selection.Record
	if rec.RecordID == "" || rec.Version <= 0 {
		return
	}
	requiresWhole := rec.Class == "C" || len(exp.Competing) > 0 || len(exp.Selection.Conflicts) > 0
	if args.ReceiptID != "" && args.Handle != "" {
		t.putHandleLocked(pullHandleKey{receiptID: args.ReceiptID, handle: args.Handle}, pullRecordVersion{
			recordID:      rec.RecordID,
			version:       rec.Version,
			requiresWhole: requiresWhole,
		})
	}
	if exp.Span != nil {
		entry := t.ensureRecordLocked(rec.RecordID)
		if entry.spans == nil {
			entry.spans = make(map[int][]pullSpanRange)
		}
		eof := exp.Span.TotalBytes > 0 && exp.Span.End >= exp.Span.TotalBytes
		entry.spans[rec.Version] = mergePullSpans(entry.spans[rec.Version], pullSpanRange{
			offset:       exp.Span.Offset,
			end:          exp.Span.End,
			eof:          eof,
			openingBytes: args.OpeningBytes,
		})
		return
	}
	entry := t.ensureRecordLocked(rec.RecordID)
	entry.wholeVersion = rec.Version
	entry.requiresWhole = requiresWhole
	for _, comp := range exp.Competing {
		if comp.Record.RecordID != "" && comp.Record.Version > 0 {
			ce := t.ensureRecordLocked(comp.Record.RecordID)
			ce.wholeVersion = comp.Record.Version
			ce.requiresWhole = true
		}
	}
}

func (t *pullDedupTracker) invalidate(recordID string) {
	if t == nil || recordID == "" {
		return
	}
	t.mu.Lock()
	defer t.mu.Unlock()
	delete(t.delivered, recordID)
}

func (t *pullDedupTracker) ensureRecordLocked(recordID string) *pullDeliveredRecord {
	rec, ok := t.delivered[recordID]
	if !ok {
		rec = &pullDeliveredRecord{}
		t.delivered[recordID] = rec
	}
	t.touchRecordLocked(recordID)
	for len(t.recordOrder) > maxTrackedPullRecords {
		evict := t.recordOrder[0]
		t.recordOrder = t.recordOrder[1:]
		delete(t.delivered, evict)
	}
	return rec
}

func (t *pullDedupTracker) touchRecordLocked(recordID string) {
	for i, id := range t.recordOrder {
		if id == recordID {
			t.recordOrder = append(t.recordOrder[:i], t.recordOrder[i+1:]...)
			break
		}
	}
	t.recordOrder = append(t.recordOrder, recordID)
}

func openingBytesCovered(requested, delivered int) bool {
	if requested == 0 {
		return delivered == 0 || delivered >= 96
	}
	return requested <= delivered
}

func mergePullSpans(existing []pullSpanRange, added pullSpanRange) []pullSpanRange {
	if added.openingBytes == 0 {
		// Merge overlapping/adjacent ranges with compatible opening coverage.
		out := make([]pullSpanRange, 0, len(existing)+1)
		merged := added
		for _, cur := range existing {
			if cur.openingBytes != merged.openingBytes || cur.end < merged.offset || merged.end < cur.offset {
				out = append(out, cur)
				continue
			}
			if cur.offset < merged.offset {
				merged.offset = cur.offset
			}
			if cur.end > merged.end {
				merged.end = cur.end
			}
			merged.eof = merged.eof || cur.eof
		}
		return append(out, merged)
	}
	return append(existing, added)
}
