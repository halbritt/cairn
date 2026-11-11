package memoryhook

import (
	"context"
	"encoding/json"
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
	if setting == nil || setting.Kind != value.Bool || !setting.Boolean {
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
	body := core.CaptureObservationRequest{RequestID: uuid.NewString(), Repo: text(config.Get("repo")), Harness: text(config.Get("harness")), HookEvent: text(event.Get("hook_event_name")), Method: "cairn-lifecycle/capture-meter/2", SessionSHA256: hashText(text(event.Get("session_id"))), SnapshotSHA256: text(observed.Get("snapshot_sha256")), Outcome: outcome, AdmissionCandidate: text(observed.Get("admission_candidate")), DurationMS: &elapsed}
	limited, cancel := context.WithTimeout(ctx, remaining)
	defer cancel()
	var response json.RawMessage
	_ = api.Call(limited, "capture-observation", body, &response) // Explicit Source best-effort report.
}
