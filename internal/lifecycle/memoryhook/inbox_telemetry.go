package memoryhook

import (
	"encoding/json"
	"errors"
	"math"
	"time"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/internal/lifecycle/memoryclient"
	"github.com/halbritt/cairn/internal/lifecycle/value"
)

// inboxTelemetry materializes selected observations while the caller owns the
// locks. No callback, client, token, query or source body crosses the IPC reply.
type inboxTelemetry struct {
	in                    InboxInvocation
	started, phaseStarted time.Time
	current               string
	data                  *value.Value
	durations             map[string]time.Duration
}

func newInboxTelemetry(in InboxInvocation, id inboxIdentity) *inboxTelemetry {
	t := &inboxTelemetry{in: in, started: time.Now(), phaseStarted: time.Now(), current: "required", durations: map[string]time.Duration{"search": 0, "pull": 0}}
	if sameBool(in.Memory.Get("inbox_recall_trace"), true) {
		identity := value.Mapping()
		for _, key := range []string{"event_id", "delivery_id", "attempt_id"} {
			if v := id.value.Get(key); inboxUUID(v) {
				identity.Set(key, clone(v))
			}
		}
		for _, key := range []string{"agent_id", "execution_id"} {
			if v := id.value.Get("session").Get(key); inboxUUID(v) {
				identity.Set(key, clone(v))
			}
		}
		t.data = value.Mapping(value.Field("schema", value.Text("cairn.inbox-route-trace/1")), value.Field("complete", value.Boolean(true)), value.Field("dropped", integer(0)), value.Field("identity", identity), value.Field("rpc", array()), value.Field("routes", array()), value.Field("stages", array()), value.Field("renders", integer(0)))
	}
	return t
}

func sameBool(v *value.Value, b bool) bool { return v != nil && v.Kind == value.Bool && v.Boolean == b }
func ms(d time.Duration) *value.Value {
	return floating(math.Max(0, math.Round(float64(d)/float64(time.Microsecond))/1000))
}
func wholeMS(d time.Duration) *value.Value {
	return integer(max(0, int(math.Round(float64(d)/float64(time.Millisecond)))))
}

func (t *inboxTelemetry) bounded(key string, row *value.Value, limit int) {
	if t.data == nil {
		return
	}
	items := t.data.Get(key)
	if len(items.Items) < limit {
		items.Items = append(items.Items, row)
	} else {
		t.data.Set("complete", value.Boolean(false))
		t.data.Set("dropped", integer(number(t.data.Get("dropped"))+1))
	}
}
func (t *inboxTelemetry) phase(phase string) {
	if t.data == nil {
		return
	}
	t.bounded("stages", value.Mapping(value.Field("phase", value.Text(t.current)), value.Field("elapsed_ms", ms(time.Since(t.phaseStarted)))), 8)
	t.current = phase
	t.phaseStarted = time.Now()
}
func (t *inboxTelemetry) operation(name string, d time.Duration) { t.durations[name] += d }
func (t *inboxTelemetry) rpc(operation string, started time.Time, payload, response json.RawMessage, err error) {
	if t.data == nil {
		return
	}
	outcome := "returned"
	if err != nil {
		outcome = "error"
	}
	row := value.Mapping(value.Field("operation", value.Text(operation)), value.Field("phase", value.Text(t.current)), value.Field("at_ms", ms(time.Since(t.started))), value.Field("elapsed_ms", ms(time.Since(started))), value.Field("outcome", value.Text(outcome)))
	if p, e := value.DecodeText(payload, "utf-8", 0); e == nil && p.Get("span") != nil {
		span := value.Mapping()
		for _, key := range []string{"offset", "length"} {
			if v := p.Get("span").Get(key); v != nil && v.Kind == value.Int && number(v) >= 0 {
				span.Set(key, clone(v))
			}
		}
		row.Set("requested_span", span)
	}
	if r, e := value.DecodeText(response, "utf-8", 0); e == nil && r.Kind == value.Object {
		if operation == "search" {
			if status := text(r.Get("status")); contains([]string{"READY", "SCOPE_EMPTY", "DEGRADED_NO_EMBEDDINGS"}, status) {
				row.Set("search_status", value.Text(status))
			}
			discovery := r.Get("discovery")
			if discovery == nil {
				discovery = value.Mapping()
			}
			if state := text(discovery.Get("state")); contains([]string{"ready", "not_needed", "unavailable", "invalid_result"}, state) {
				row.Set("discovery_state", value.Text(state))
			}
			coverage := discovery.Get("coverage")
			if coverage == nil && discovery.Kind == value.Object {
				coverage = value.Mapping()
			}
			if coverage != nil && coverage.Kind == value.Object {
				selected := value.Mapping()
				for _, key := range []string{"indexed", "eligible"} {
					if v := coverage.Get(key); v != nil && v.Kind == value.Int && number(v) >= 0 {
						selected.Set(key, clone(v))
					}
				}
				row.Set("coverage", selected)
			}
		}
		for _, key := range []string{"credits_remaining", "bytes_remaining"} {
			if v := r.Get(key); v != nil && v.Kind == value.Int && number(v) >= 0 {
				row.Set(key, clone(v))
			}
		}
		if v := r.Get("receipt_id"); inboxUUID(v) {
			row.Set("receipt_id", clone(v))
		}
	} else if err == nil {
		t.data.Set("complete", value.Boolean(false))
	}
	t.bounded("rpc", row, 8)
}

func (t *inboxTelemetry) source(record *value.Value, extent string, span *value.Value) *value.Value {
	if !inboxUUID(record.Get("record_id")) || record.Get("version") == nil || record.Get("version").Kind != value.Int || number(record.Get("version")) <= 0 {
		t.data.Set("complete", value.Boolean(false))
		return &value.Value{Kind: value.Null}
	}
	v := value.Mapping(value.Field("extent", value.Text(extent)), value.Field("record_id", clone(record.Get("record_id"))), value.Field("version", clone(record.Get("version"))))
	digest := record.Get("body_sha256")
	if digest == nil && extent == "whole" && validText(record.Get("body")) {
		body := text(record.Get("body"))
		digest = value.Text(hashText(body))
		v.Set("bytes", integer(len(body)))
	}
	if bodyHash.MatchString(text(digest)) {
		v.Set("body_sha256", clone(digest))
	}
	if span != nil && span.Kind == value.Object {
		selected := value.Mapping()
		for _, key := range []string{"offset", "length", "end", "total_bytes"} {
			if n := span.Get(key); n != nil && n.Kind == value.Int && number(n) >= 0 {
				selected.Set(key, clone(n))
			}
		}
		for _, key := range []string{"sha256", "source_sha256"} {
			if n := span.Get(key); bodyHash.MatchString(text(n)) {
				selected.Set(key, clone(n))
			}
		}
		v.Set("span", selected)
	}
	return v
}
func (t *inboxTelemetry) task(source *value.Value) {
	if t.data != nil {
		t.data.Set("task_source", t.source(source, "whole", nil))
	}
}

func (t *inboxTelemetry) ObservationLost() {
	if t.data != nil {
		t.data.Set("complete", value.Boolean(false))
	}
}
func (t *inboxTelemetry) Route(reason string, group []*value.Value) {
	if t.data == nil {
		return
	}
	if reason == "delegation_context_budget" {
		t.data = t.data.Without("final")
	}
	sources := array()
	for _, record := range group[:min(16, len(group))] {
		sources.Items = append(sources.Items, t.source(record, "candidate", nil))
	}
	if len(group) > 16 {
		t.ObservationLost()
	}
	t.bounded("routes", value.Mapping(value.Field("at_ms", ms(time.Since(t.started))), value.Field("reason", value.Text(reason)), value.Field("sources", sources)), 32)
}
func (t *inboxTelemetry) Validated(extent string, response *value.Value) {
	if t.data == nil {
		return
	}
	selections := append([]*value.Value{response.Get("selection")}, inboxItems(response.Get("competing"))...)
	sources := array()
	for _, item := range selections[:min(16, len(selections))] {
		sources.Items = append(sources.Items, t.source(item.Get("record"), extent, response.Get("span")))
	}
	if len(selections) > 16 {
		t.ObservationLost()
	}
	t.bounded("routes", value.Mapping(value.Field("at_ms", ms(time.Since(t.started))), value.Field("reason", value.Text("checked_source_returned")), value.Field("sources", sources)), 32)
}
func (t *inboxTelemetry) Rendered(entries, bodies []*value.Value, selected *value.Value, budget int, measured candidateMeasuredText, remaining int, packing *value.Value) {
	if t.data == nil {
		return
	}
	final := value.Mapping(value.Field("previews", array()), value.Field("bodies", array()), value.Field("required", array()), value.Field("budget_bytes", integer(budget)), value.Field("serialized_bytes", integer(measured.WireBytes)), value.Field("remaining_bytes", integer(remaining)), value.Field("packing", clone(packing)))
	for _, entry := range entries {
		t.renderSource(final, "previews", entry, "preview", entry.Get("summary_span"))
	}
	for _, item := range inboxItems(selected) {
		t.renderSource(final, "required", item.Get("record"), "whole", nil)
	}
	for _, body := range bodies {
		response := body.Get("response")
		selections := append([]*value.Value{response.Get("selection")}, inboxItems(response.Get("competing"))...)
		extent := "whole"
		if value.Truth(response.Get("span")) {
			extent = "span"
		}
		for _, item := range selections {
			t.renderSource(final, "bodies", item.Get("record"), extent, response.Get("span"))
		}
		if opening := body.Get("source_opening_excerpt"); value.SameText(opening.Get("status"), "provided") {
			t.renderSource(final, "bodies", response.Get("selection").Get("record"), "opening", opening.Get("span"))
		}
	}
	t.data.Set("renders", integer(number(t.data.Get("renders"))+1))
	t.data.Set("final", final)
}
func (t *inboxTelemetry) renderSource(final *value.Value, key string, record *value.Value, extent string, span *value.Value) {
	items := final.Get(key)
	if len(items.Items) >= 16 {
		t.data.Set("complete", value.Boolean(false))
		t.data.Set("dropped", integer(number(t.data.Get("dropped"))+1))
		return
	}
	items.Items = append(items.Items, t.source(record, extent, span))
}

func (t *inboxTelemetry) finish(out *InboxResult, client *memoryclient.Client, eligible bool) {
	if client == nil {
		return
	}
	// Optional trace and metric materialization fail independently. A lost
	// observation cannot mask delivery, refusal, or the other report.
	func() { defer func() { _ = recover() }(); t.finishTrace(out, client, eligible) }()
	func() { defer func() { _ = recover() }(); t.finishMeter(out, client, eligible) }()
}

func inboxItems(v *value.Value) []*value.Value {
	if v == nil || v.Kind != value.Array {
		return nil
	}
	return v.Items
}

func (t *inboxTelemetry) finishTrace(out *InboxResult, client *memoryclient.Client, eligible bool) {
	traceEligible := eligible
	var h *hookError
	if errors.As(out.Err, &h) && h.reason != "NOT_HOSTED" {
		traceEligible = true
	}
	if t.data != nil && traceEligible {
		t.phase("finished")
		status := "bridge_returned"
		n := len(out.Message)
		if out.Err != nil {
			status = "error"
			n = 0
			t.data = t.data.Without("final")
		}
		t.data.Set("status", value.Text(status))
		t.data.Set("elapsed_ms", ms(time.Since(t.started)))
		t.data.Set("returned_context_bytes", integer(n))
		if out.Err == nil && t.data.Get("final") == nil {
			t.data.Set("complete", value.Boolean(false))
			t.data.Set("final_unobserved", value.Boolean(true))
		}
		if !client.RPCObservationComplete() {
			t.data.Set("complete", value.Boolean(false))
		}
		if out.FailureMeta != nil {
			t.data.Set("failure_meta", clone(out.FailureMeta))
		}
		out.TraceReport = clone(t.data)
	}
}

func (t *inboxTelemetry) finishMeter(out *InboxResult, client *memoryclient.Client, eligible bool) {
	if !eligible || sameBool(t.in.Memory.Get("recall_observations"), false) {
		return
	}
	status, errorClass := "completed", ""
	if out.Err != nil {
		status = "error"
		errorClass = errorFamily(out.Err)
		var coordination *inboxCoordinationError
		if errors.As(out.Err, &coordination) {
			errorClass = "Exception"
		}
		if inboxCode(out.Err) == "TIMEOUT" {
			status = "timeout"
		}
	}
	accounting := client.Accounting()
	body := value.Mapping(value.Field("request_id", value.Text(uuid.NewString())), value.Field("repo", clone(t.in.Memory.Get("repo"))), value.Field("harness", value.Text(text(t.in.Memory.Get("harness")))), value.Field("hook_event", clone(t.in.Event.Get("hook_event_name"))), value.Field("method", value.Text("cairn-lifecycle/inbox-recall-meter/1")), value.Field("status", value.Text(status)), value.Field("elapsed_ms", wholeMS(time.Since(t.in.Started))), value.Field("search_ms", wholeMS(t.durations["search"])), value.Field("pulls_ms", wholeMS(t.durations["pull"])), value.Field("selector_ms", integer(0)), value.Field("search_calls", integer(accounting.OperationCalls["search"])), value.Field("pull_calls", integer(accounting.OperationCalls["pull"])), value.Field("selector_calls", array()), value.Field("injected_bytes", integer(len(out.Message))))
	if t.in.Memory.Get("harness") == nil {
		body.Set("harness", value.Text("claude"))
	}
	if len(accounting.SearchReceipts) > 0 {
		receipts := array()
		for _, id := range accounting.SearchReceipts {
			receipts.Items = append(receipts.Items, value.Text(id))
		}
		body.Set("receipt_ids", receipts)
	}
	if errorClass != "" {
		body.Set("error_class", value.Text(errorClass))
		body.Set("injected_bytes", integer(0))
	}
	out.MeterReport = body
}
