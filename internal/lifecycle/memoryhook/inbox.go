package memoryhook

import (
	"context"
	"errors"
	"syscall"
	"time"

	"github.com/halbritt/cairn/internal/lifecycle/binding"
	"github.com/halbritt/cairn/internal/lifecycle/ledger"
	"github.com/halbritt/cairn/internal/lifecycle/value"
)

// InboxInvocation contains captured producer data and checked configuration
// snapshots. It is neither an activation decision nor an inbox claim.
type InboxInvocation struct {
	Config, Memory, Event, Observation, State *value.Value
	Control                                   string
	Started                                   time.Time
	Directory                                 string
}

type InboxResult struct {
	Message, Stage           string
	Err                      error
	TraceReport, MeterReport *value.Value
}

func (r InboxResult) Wire() *value.Value {
	result := value.Mapping(value.Field("ok", value.Boolean(r.Err == nil)))
	if r.Err == nil {
		result.Set("message", value.Text(r.Message))
	} else {
		class, message := "ValueError", r.Err.Error()
		var source *value.Error
		var refused *ledger.InboxRefusal
		var number syscall.Errno
		if errors.As(r.Err, &source) {
			class, message = source.Class, source.Message
		}
		failure := value.Mapping()
		if errors.As(r.Err, &number) {
			class = "OSError"
			failure.Set("errno", integer(int(number)))
		}
		failure.Set("class", value.Text(class))
		failure.Set("message", value.Text(message))
		details := value.Mapping(value.Field("refusal_stage", value.Text(r.Stage)), value.Field("refusal_reason", value.Text("unknown")))
		if errors.As(r.Err, &refused) {
			details.Set("refusal_stage", value.Text(refused.Stage))
			details.Set("refusal_reason", value.Text(refused.Reason))
			if refused.Details != nil {
				for _, member := range refused.Details.Members {
					details.Members = append(details.Members, member)
				}
			}
		}
		failure.Set("cairn_inbox_refusal", details)
		result.Set("error", failure)
	}
	if r.TraceReport != nil {
		result.Set("trace_report", r.TraceReport)
	}
	if r.MeterReport != nil {
		result.Set("meter_report", r.MeterReport)
	}
	return result
}

// RunInbox owns only memory consumption; Coordination retains its lock,
// admission journal, settlement and post-lock report scheduling.
func RunInbox(ctx context.Context, in InboxInvocation) (out InboxResult) {
	out.Stage = "identity"
	defer func() {
		if recover() != nil {
			out.Message = ""
			out.Err = &value.Error{Class: "ValueError", Message: "unexpected inbox recall failure"}
		}
	}()
	if !binding.IsInboxWake(in.Event) {
		out.Message = in.Control
		return out
	}
	private, err := inboxPrivate(in.Event, in.State, in.Directory)
	if err != nil {
		out.Err = err
		return out
	}
	if private {
		out.Message = in.Control
		return out
	}
	identity, err := inboxClaim(in.Config, in.Event, in.Observation, in.State)
	if err != nil {
		out.Err = err
		return out
	}
	return inboxDeliver(ctx, in, identity)
}

func inboxDeliver(ctx context.Context, in InboxInvocation, identity inboxIdentity) InboxResult {
	// This reached constructor refuses until the remaining search/grant body is
	// installed; it must never turn an unfinished supported branch into success.
	return InboxResult{Stage: "inbox_ledger", Err: &value.ProfileUnavailable{}}
}
