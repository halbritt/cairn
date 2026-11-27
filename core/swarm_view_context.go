package core

import (
	"context"
	"time"

	"github.com/jackc/pgx/v5"
)

// Context room (CAIRN-832): what Cairn can state truthfully about how full an
// agent's window is. The true fill is known only to the harness. The values
// come from the CAIRN-816 observations; no second counting mechanism exists.
//
// The types below are the wire contract of snapshot.context_room. Add a field
// when a reader needs one; do not rename or remove one without the lead.

// SwarmContextRoom is snapshot.context_room. The session counters are an
// observation of their own: ObservedAt is the newest counter row read, so the
// page can mark the counters stale without hiding the rest.
type SwarmContextRoom struct {
	// ObservedAt is nil when no counter row exists for any member.
	ObservedAt *time.Time `json:"observed_at,omitempty"`
	// NearLimitRule states, in one sentence, the threshold that marks an
	// agent as near its limit.
	NearLimitRule string `json:"near_limit_rule"`
	// ByAgent is keyed by agent UUID. A member without an entry, or with
	// Known false, shows "unknown", never zero.
	ByAgent map[string]SwarmAgentContext `json:"by_agent"`
}

// SwarmAgentContext is the context room of one session. A nil pointer means
// the store has no data for that value.
type SwarmAgentContext struct {
	Known bool `json:"known"`
	// Compactions so far, and when the last one happened.
	Compactions      *int       `json:"compactions,omitempty"`
	LastCompactionAt *time.Time `json:"last_compaction_at,omitempty"`
	// InjectedBytes is what Cairn put in front of the model since the last
	// compaction (or since the session started): deliveries, recall, cues.
	// It is a proxy. The page labels it "Cairn-injected since compaction",
	// not "window used".
	InjectedBytes      *int64 `json:"injected_bytes,omitempty"`
	InjectedDeliveries *int   `json:"injected_deliveries,omitempty"`
	InjectedRecalls    *int   `json:"injected_recalls,omitempty"`
	// CountingSince is the start of the interval InjectedBytes covers.
	CountingSince *time.Time `json:"counting_since,omitempty"`
	// NearLimit is set by NearLimitRule. Reasons says which part matched.
	NearLimit bool     `json:"near_limit"`
	Reasons   []string `json:"reasons,omitempty"`
}

func emptySwarmContextRoom(sc swarmScope) SwarmContextRoom {
	return SwarmContextRoom{NearLimitRule: "", ByAgent: map[string]SwarmAgentContext{}}
}

// swarmContextRoomTx reads snapshot.context_room. CAIRN-832 replaces this stub.
func (s *Store) swarmContextRoomTx(ctx context.Context, tx pgx.Tx, sc swarmScope) (SwarmContextRoom, error) {
	return emptySwarmContextRoom(sc), nil
}
