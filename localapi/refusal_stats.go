package localapi

import (
	"sort"
	"strings"
	"sync"
	"time"

	"github.com/halbritt/cairn/core"
)

const (
	RefusalStatsSchema            = "cairn.refusal-stats/1"
	maxRefusalBucketsPerPrincipal = 256
)

type RefusalStatsRequest struct {
	Operation string `json:"operation,omitempty"`
	Code      string `json:"code,omitempty"`
}

type RefusalStatRow struct {
	Operation string    `json:"operation"`
	Code      string    `json:"code"`
	Status    int       `json:"status"`
	Count     uint64    `json:"count"`
	FirstAt   time.Time `json:"first_at"`
	LastAt    time.Time `json:"last_at"`
}

type RefusalStatsResponse struct {
	Schema          string           `json:"schema"`
	Storage         string           `json:"storage"`
	Principal       string           `json:"principal"`
	MachineID       string           `json:"machine_id,omitempty"`
	StartedAt       time.Time        `json:"started_at"`
	ObservedAt      time.Time        `json:"observed_at"`
	TotalRefusals   uint64           `json:"total_refusals"`
	MatchedRefusals uint64           `json:"matched_refusals"`
	DroppedRefusals uint64           `json:"dropped_refusals"`
	Rows            []RefusalStatRow `json:"rows"`
}

type refusalKey struct {
	operation string
	code      string
	status    int
}

type principalRefusalKey struct {
	principal string
	machineID string
}

type principalRefusalState struct {
	total           uint64
	droppedRefusals uint64
	buckets         map[refusalKey]*RefusalStatRow
}

type refusalTracker struct {
	mu         sync.Mutex
	startedAt  time.Time
	principals map[principalRefusalKey]*principalRefusalState
}

func newRefusalTracker() *refusalTracker {
	return &refusalTracker{
		startedAt:  time.Now().UTC(),
		principals: make(map[principalRefusalKey]*principalRefusalState),
	}
}

func (t *refusalTracker) record(principal, machineID, operation, code string, status int) {
	if principal == "" {
		return
	}
	if operation == "" {
		operation = "unknown"
	}
	if code == "" {
		code = "UNKNOWN"
	}
	now := time.Now().UTC()
	pKey := principalRefusalKey{principal: principal, machineID: machineID}
	t.mu.Lock()
	defer t.mu.Unlock()
	st, ok := t.principals[pKey]
	if !ok {
		if len(t.principals) >= maxCounterPrincipals {
			return
		}
		st = &principalRefusalState{buckets: make(map[refusalKey]*RefusalStatRow)}
		t.principals[pKey] = st
	}
	if st.total < counterCeiling {
		st.total++
	}
	key := refusalKey{operation: operation, code: code, status: status}
	if existing, ok := st.buckets[key]; ok {
		if existing.Count < counterCeiling {
			existing.Count++
		}
		existing.LastAt = now
		return
	}
	if len(st.buckets) >= maxRefusalBucketsPerPrincipal {
		if st.droppedRefusals < counterCeiling {
			st.droppedRefusals++
		}
		return
	}
	st.buckets[key] = &RefusalStatRow{
		Operation: operation,
		Code:      code,
		Status:    status,
		Count:     1,
		FirstAt:   now,
		LastAt:    now,
	}
}

func (t *refusalTracker) view(principal, machineID string, req RefusalStatsRequest) (RefusalStatsResponse, error) {
	opFilter := strings.TrimSpace(req.Operation)
	codeFilter := strings.TrimSpace(req.Code)
	if len(opFilter) > 128 || len(codeFilter) > 128 {
		return RefusalStatsResponse{}, &core.Error{Code: "INVALID_REQUEST", Message: "refusal filter exceeds 128 bytes"}
	}
	now := time.Now().UTC()
	pKey := principalRefusalKey{principal: principal, machineID: machineID}
	t.mu.Lock()
	st := t.principals[pKey]
	out := RefusalStatsResponse{
		Schema:     RefusalStatsSchema,
		Storage:    "volatile",
		Principal:  principal,
		MachineID:  machineID,
		StartedAt:  t.startedAt,
		ObservedAt: now,
		Rows:       []RefusalStatRow{},
	}
	if st != nil {
		out.TotalRefusals = st.total
		out.DroppedRefusals = st.droppedRefusals
		out.Rows = make([]RefusalStatRow, 0, len(st.buckets))
		for _, row := range st.buckets {
			if opFilter != "" && row.Operation != opFilter {
				continue
			}
			if codeFilter != "" && row.Code != codeFilter {
				continue
			}
			out.Rows = append(out.Rows, *row)
			out.MatchedRefusals += row.Count
		}
	}
	t.mu.Unlock()
	sort.Slice(out.Rows, func(i, j int) bool {
		if out.Rows[i].Count != out.Rows[j].Count {
			return out.Rows[i].Count > out.Rows[j].Count
		}
		if out.Rows[i].Operation != out.Rows[j].Operation {
			return out.Rows[i].Operation < out.Rows[i].Operation
		}
		if out.Rows[i].Code != out.Rows[j].Code {
			return out.Rows[i].Code < out.Rows[j].Code
		}
		return out.Rows[i].Status < out.Rows[j].Status
	})
	return out, nil
}
