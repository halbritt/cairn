package coordination

import (
	"context"
	"time"

	"github.com/halbritt/cairn/internal/lifecycle/value"
)

// ServiceBindings connects the Source service owners while allowing the memory
// bridge and native queue transport to provide their existing implementations.
type ServiceBindings struct {
	Profile        *value.SourceReaderProfile
	MemoryStateDir func(*value.Value) (*value.Value, error)
	GrantLoader    func(string, *value.Value) (*value.Value, error)
	UID            *uint32
	Now            func() time.Time
	Endpoint       func(context.Context, *value.Value, *value.Value) (*value.Value, error)
	Inspector      func(context.Context, *value.Value, *value.Value, *value.Value, *value.Value) (*value.Value, error)
}

func (b ServiceBindings) bind(host *Host, hooks RecoveryHooks, root string, clock func() time.Time) RecoveryHooks {
	if hooks.ObserveTime == nil {
		hooks.ObserveTime = ObserveTime
	}
	g := GrantBindings{Profile: b.Profile, MemoryStateDir: b.MemoryStateDir, Loader: b.GrantLoader, UID: b.UID}
	if hooks.GrantEvidence == nil {
		hooks.GrantEvidence = g.GrantEvidence
	}
	if hooks.GrantState == nil {
		hooks.GrantState = g.GrantState
	}
	if hooks.PreGrantLedgerAbsent == nil {
		hooks.PreGrantLedgerAbsent = g.PreGrantLedgerAbsent
	}
	if hooks.ProbeCodex == nil {
		if b.Now != nil {
			clock = b.Now
		}
		health := CodexHealthBindings{ProcRoot: root, Now: clock, Alive: hooks.Alive, Endpoint: b.Endpoint, Inspector: b.Inspector}
		health.SessionRef = func(agent *value.Value) (*value.Value, error) {
			h := host
			if h == nil {
				h = &Host{}
			}
			return (&NativeSession{Host: h}).sessionRef(agent)
		}
		hooks.ProbeCodex = health.ProbeCodex
	}
	return hooks
}
