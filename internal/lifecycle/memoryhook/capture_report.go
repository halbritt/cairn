package memoryhook

import (
	"context"
	"encoding/json"
	"math"
	"strings"
	"time"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
	"github.com/halbritt/cairn/internal/lifecycle/value"
	"github.com/halbritt/cairn/localapi"
)

// Source report_capture is best effort after all capture/shared locks release.
// No metric can change a capture disposition, marker, or retry eligibility.
func captureReport(ctx context.Context, config, event *value.Value, started time.Time, observed *value.Value) {
	elapsed := time.Since(started).Milliseconds()
	setting := config.Get("capture_observations")
	if setting != nil && setting.Kind == value.Bool && !setting.Boolean {
		return
	}
	remaining := min(time.Second, time.Until(started.Add(145*time.Second)))
	if end, ok := ctx.Deadline(); ok {
		remaining = min(remaining, time.Until(end)-5*time.Second)
	}
	if remaining < 250*time.Millisecond {
		return
	}
	socket, err := pathValue(config.Get("socket"))
	if err != nil {
		return
	}
	token, err := pathValue(config.Get("token_file"))
	if err != nil {
		return
	}
	api, err := localapi.NewClient(socket, token)
	if err != nil {
		return
	}
	defer api.Close()
	api = api.WithDiagnostics(localapi.ClientDiagnostics{Surface: "cli", Harness: "unknown", Origin: &localapi.ClientOrigin{Component: "lifecycle-memory", ImplementationID: executingSHA(), Basis: "reported"}, RetrievalCapabilities: localapi.CurrentRetrievalCapabilities(), ReaderCapabilities: localapi.CurrentReaderCapabilities()})
	outcome := text(observed.Get("outcome"))
	if outcome == "" {
		outcome = "error"
	}
	now := time.Now().UTC()
	body := core.CaptureObservationRequest{
		RequestID:          uuid.NewString(),
		Repo:               text(config.Get("repo")),
		Harness:            text(config.Get("harness")),
		HookEvent:          text(event.Get("hook_event_name")),
		Method:             "cairn-lifecycle/capture-meter/2",
		SessionSHA256:      hashText(text(event.Get("session_id"))),
		SnapshotSHA256:     text(observed.Get("snapshot_sha256")),
		Outcome:            outcome,
		AdmissionCandidate: text(observed.Get("admission_candidate")),
		DurationMS:         &elapsed,
		ClientGeneratedAt:  &now,
	}
	if sid := text(event.Get("session_id")); validTelemetryLabel(sid, 256) {
		body.NativeSessionID = strings.TrimSpace(sid)
	}
	if aid := text(event.Get("attempt_id")); aid == "" {
		aid = text(observed.Get("attempt_id"))
	} else if _, parseErr := uuid.Parse(aid); parseErr == nil {
		body.AttemptID = aid
	}
	if body.AttemptID == "" {
		if aid := text(observed.Get("attempt_id")); aid != "" {
			if _, parseErr := uuid.Parse(aid); parseErr == nil {
				body.AttemptID = aid
			}
		}
	}
	if did := text(event.Get("delivery_id")); did != "" {
		if _, parseErr := uuid.Parse(did); parseErr == nil {
			body.DeliveryID = did
		}
	} else if did := text(observed.Get("delivery_id")); did != "" {
		if _, parseErr := uuid.Parse(did); parseErr == nil {
			body.DeliveryID = did
		}
	}
	if msgs := observed.Get("dialogue_messages"); msgs != nil && msgs.Kind == value.Int {
		n := number(msgs)
		if n >= 0 {
			body.DialogueMessages = &n
		}
	}
	if dbytes := observed.Get("dialogue_bytes"); dbytes != nil && dbytes.Kind == value.Int {
		if n, ok := captureMetric(dbytes, 16*1024*1024); ok {
			b := int64(n)
			body.DialogueBytes = &b
		}
	}
	if selectorOutcome := text(observed.Get("selector_outcome")); selectorOutcome != "" {
		body.SelectorOutcome = selectorOutcome
		selector := observed.Get("selector")
		if v := selector.Get("requested_model"); captureModelName(v) {
			body.RequestedModel = text(v)
		}
		if v := selector.Get("reported_model"); captureModelName(v) {
			body.ReportedModel = text(v)
		}
		for _, key := range []string{"elapsed_ms", "reported_duration_ms"} {
			if n, ok := captureMetric(selector.Get(key), 3600000); ok {
				rounded := int64(math.RoundToEven(n))
				body.SelectorDurationMS = &rounded
				break
			}
		}
		for _, metric := range []struct {
			key string
			dst **int64
		}{{"input_tokens", &body.InputTokens}, {"output_tokens", &body.OutputTokens}, {"cache_creation_input_tokens", &body.CacheCreationInputTokens}, {"cache_read_input_tokens", &body.CacheReadInputTokens}, {"reasoning_tokens", &body.ReasoningTokens}} {
			v := selector.Get("usage").Get(metric.key)
			if n, ok := captureMetric(v, 1<<40); ok && v.Kind == value.Int {
				count := int64(n)
				*metric.dst = &count
			}
		}
		if p := selector.Get("partial_usage"); p != nil && p.Kind == value.Bool && p.Boolean {
			partial := true
			body.PartialUsage = &partial
		}
		if n, ok := captureMetric(selector.Get("reported_cost_usd"), 1000); ok && n < 1000 {
			body.CostUSD = &n
		}
	}
	limited, cancel := context.WithTimeout(ctx, remaining)
	defer cancel()
	var response json.RawMessage
	_ = api.Call(limited, "capture-observation", body, &response) // Explicit Source best-effort report.
}
