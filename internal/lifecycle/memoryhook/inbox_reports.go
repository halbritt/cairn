package memoryhook

import (
	"context"
	"encoding/json"
	"errors"
	"math"
	"os"
	"path/filepath"
	"strings"
	"syscall"
	"time"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
	"github.com/halbritt/cairn/internal/lifecycle/value"
)

// RunInboxReports performs only the already-materialized optional observations.
// Its caller schedules this operation after releasing Coordination's lock. It
// neither opens a session ledger nor changes an output, lease or grant.
func RunInboxReports(ctx context.Context, config, trace, meter *value.Value, started time.Time) (result *value.Value) {
	result = value.Mapping(value.Field("ok", value.Boolean(true)), value.Field("trace_status", value.Text("not_requested")), value.Field("meter_status", value.Text("not_requested")))
	defer func() {
		if recover() != nil {
			if trace != nil && trace.Kind != value.Null {
				result.Set("trace_status", value.Text("unknown"))
			}
			if meter != nil && meter.Kind != value.Null {
				result.Set("meter_status", value.Text("unknown"))
			}
		}
	}()
	if trace != nil && trace.Kind != value.Null {
		result.Set("trace_status", value.Text("unknown"))
		if sameBool(config.Get("inbox_recall_trace"), true) && inboxTraceShape(trace) && ctx.Err() == nil {
			if err := inboxFlushTrace(ctx, config, trace); err == nil {
				result.Set("trace_status", value.Text("written"))
			}
		}
	}
	if meter == nil || meter.Kind == value.Null {
		return
	}
	result.Set("meter_status", value.Text("unknown"))
	if sameBool(config.Get("recall_observations"), false) {
		return
	}
	remaining := 13*time.Second - 750*time.Millisecond - time.Since(started)
	if deadline, ok := ctx.Deadline(); ok {
		remaining = min(remaining, time.Until(deadline)-750*time.Millisecond)
	}
	if remaining < 250*time.Millisecond {
		return
	}
	body, err := inboxMeterRequest(config, meter)
	if err != nil {
		return
	}
	api, err := inboxAPI(config, "lifecycle-memory")
	if err != nil {
		return
	}
	defer api.Close()
	limited, cancel := context.WithTimeout(ctx, min(time.Second, remaining))
	defer cancel()
	var response json.RawMessage
	if err = api.Call(limited, "recall-observation", body, &response); err == nil {
		// A confirmed API envelope is the report result; it certifies neither
		// measurement truth nor delivery/qualification of any native output.
		result.Set("meter_status", value.Text("recorded"))
	}
	return
}

func inboxMeterRequest(config, meter *value.Value) (core.RecallObservationRequest, error) {
	var body core.RecallObservationRequest
	if meter.Kind != value.Object {
		return body, errors.New("invalid materialized inbox meter")
	}
	allowed := strings.Fields("request_id repo harness hook_event method status error_class elapsed_ms search_ms pulls_ms selector_ms search_calls pull_calls selector_calls injected_bytes receipt_ids")
	for _, m := range meter.Members {
		if !contains(allowed, string(m.Key)) {
			return body, errors.New("invalid materialized inbox meter field")
		}
	}
	if !inboxUUID(meter.Get("request_id")) || !value.SameText(meter.Get("repo"), text(config.Get("repo"))) || !value.SameText(meter.Get("method"), "cairn-lifecycle/inbox-recall-meter/1") || !contains([]string{"codex", "claude"}, text(meter.Get("harness"))) || !value.SameText(meter.Get("hook_event"), "UserPromptSubmit") || !contains([]string{"completed", "error", "timeout"}, text(meter.Get("status"))) {
		return body, errors.New("invalid materialized inbox meter identity")
	}
	harness := text(config.Get("harness"))
	if harness == "" {
		harness = "claude"
	}
	if !value.SameText(meter.Get("harness"), harness) {
		return body, errors.New("inbox meter harness differs")
	}
	for _, key := range []string{"elapsed_ms", "search_ms", "pulls_ms", "selector_ms", "search_calls", "pull_calls", "injected_bytes"} {
		if n := meter.Get(key); n != nil && (n.Kind != value.Int || number(n) < 0) {
			return body, errors.New("invalid materialized inbox meter counter")
		}
	}
	if calls := meter.Get("selector_calls"); calls != nil && (calls.Kind != value.Array || len(calls.Items) != 0) {
		return body, errors.New("unexpected selector observation")
	}
	if ids := meter.Get("receipt_ids"); ids != nil {
		if ids.Kind != value.Array || len(ids.Items) > 8 {
			return body, errors.New("invalid inbox meter receipts")
		}
		for _, id := range ids.Items {
			if !inboxUUID(id) {
				return body, errors.New("invalid inbox meter receipt")
			}
		}
	}
	raw, err := encoded(meter, false)
	if err != nil {
		return body, err
	}
	err = json.Unmarshal(raw, &body)
	return body, err
}

func inboxTraceShape(trace *value.Value) bool {
	if trace.Kind != value.Object || !value.SameText(trace.Get("schema"), "cairn.inbox-route-trace/1") {
		return false
	}
	// A trace is selected fixed metadata. Reject text-bearing or executable
	// fields instead of turning a malformed report into a raw workspace dump.
	allowed := strings.Fields("schema complete dropped identity event_id delivery_id attempt_id agent_id execution_id rpc routes stages renders operation phase at_ms elapsed_ms outcome requested_span offset length end total_bytes sha256 source_sha256 search_status discovery_state coverage indexed eligible credits_remaining bytes_remaining receipt_id reason sources extent record_id version body_sha256 bytes span task_source final previews bodies required budget_bytes serialized_bytes remaining_bytes packing ceiling_bytes base_bytes preview_bytes returned_entries stop status returned_context_bytes final_unobserved failure_meta recall_phase recall_operation recall_attempts recall_max_attempts deadline_remaining_ms")
	var walk func(*value.Value, int) bool
	walk = func(v *value.Value, depth int) bool {
		if v == nil || depth > 10 {
			return false
		}
		switch v.Kind {
		case value.Object:
			for _, m := range v.Members {
				if !contains(allowed, string(m.Key)) || !walk(m.Value, depth+1) {
					return false
				}
			}
		case value.Array:
			if len(v.Items) > 32 {
				return false
			}
			for _, item := range v.Items {
				if !walk(item, depth+1) {
					return false
				}
			}
		case value.String:
			if !validText(v) || len(v.String) > 256 {
				return false
			}
		case value.Int:
			if number(v) < 0 {
				return false
			}
		case value.Float:
			if v.Number < 0 || math.IsNaN(v.Number) || math.IsInf(v.Number, 0) {
				return false
			}
		case value.Bool, value.Null:
		default:
			return false
		}
		return true
	}
	return walk(trace, 0)
}

func inboxFlushTrace(ctx context.Context, config, trace *value.Value) error {
	if err := ctx.Err(); err != nil {
		return err
	}
	parent, err := pathValue(config.Get("state_dir"))
	if err != nil {
		return err
	}
	directory := filepath.Join(parent, "inbox-recall-traces")
	for _, path := range []string{parent, directory} {
		if path == directory {
			if err = os.Mkdir(path, 0700); err != nil && !errors.Is(err, os.ErrExist) {
				return err
			}
		}
		info, err := os.Lstat(path)
		if err != nil {
			return err
		}
		stat, ok := info.Sys().(*syscall.Stat_t)
		if !ok || !info.IsDir() || info.Mode()&os.ModeSymlink != 0 || info.Mode().Perm()&0077 != 0 || stat.Uid != uint32(os.Getuid()) {
			return errors.New("trace directory is not owner-controlled")
		}
	}
	raw, err := encoded(trace, true)
	if err != nil {
		return err
	}
	if len(raw) > 32768 {
		trace = clone(trace)
		trace.Set("complete", value.Boolean(false))
		trace.Set("dropped", integer(number(trace.Get("dropped"))+1))
		trace.Set("rpc", array())
		trace.Set("routes", array())
		trace = trace.Without("final")
		raw, err = encoded(trace, true)
		if err != nil {
			return err
		}
		if len(raw) > 32768 {
			return errors.New("trace report exceeds limit")
		}
	}
	if err = ctx.Err(); err != nil {
		return err
	}
	file, err := os.OpenFile(filepath.Join(directory, uuid.NewString()+".json"), os.O_WRONLY|os.O_CREATE|os.O_EXCL|syscall.O_NOFOLLOW, 0600)
	if err != nil {
		return err
	}
	_, writeErr := file.Write(raw)
	closeErr := file.Close()
	if writeErr != nil {
		return writeErr
	}
	return closeErr
}
