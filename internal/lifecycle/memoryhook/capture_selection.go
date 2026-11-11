package memoryhook

import (
	"context"
	"errors"
	"strconv"
	"strings"
	"time"

	"github.com/halbritt/cairn/core"
	"github.com/halbritt/cairn/internal/lifecycle/memoryclient"
	"github.com/halbritt/cairn/internal/lifecycle/value"
	"github.com/halbritt/cairn/localapi"
)

// The caller owns admission, both capture locks, and the final state CAS. The
// client owns complete selection validation and ordered, confirmed API writes.
func captureSelection(ctx context.Context, config, event, state, observed *value.Value, directory string, messages *value.Value, digest string) (*value.Value, error) {
	project, err := projectRoot(event, directory)
	if err != nil {
		return nil, err
	}
	socket, err := pathValue(config.Get("socket"))
	if err != nil {
		return nil, err
	}
	token, err := pathValue(config.Get("token_file"))
	if err != nil {
		return nil, err
	}
	api, err := localapi.NewClient(socket, token)
	if err != nil {
		return nil, classify(err)
	}
	defer api.Close()
	diagnostics := localapi.ClientDiagnostics{Surface: "cli", Harness: "unknown", Origin: &localapi.ClientOrigin{Component: "lifecycle-memory", ImplementationID: executingSHA(), Basis: "reported"}, RetrievalCapabilities: localapi.CurrentRetrievalCapabilities(), ReaderCapabilities: localapi.CurrentReaderCapabilities()}
	session := text(event.Get("session_id"))
	scope := core.Scope{Repo: text(config.Get("repo")), TaskID: text(config.Get("harness")) + "/" + session, RunID: session}
	if v := config.Get("task_id"); v != nil {
		scope.TaskID = text(v)
	}
	if v := config.Get("run_id"); v != nil {
		scope.RunID = text(v)
	}
	pins := contextPins(config.Get("context"))
	client, err := memoryclient.New(api, memoryclient.Options{Scope: scope, Context: &pins, CommandPrefix: []string{text(config.Get("cairn")), "agent", "--socket", socket, "--token-file", token}, Diagnostics: diagnostics})
	if err != nil {
		return nil, classify(err)
	}
	title := "Handoff: " + clip(strings.ReplaceAll(baseName(project), "\"", ""), 70) + " / Claude session " + session
	if v := state.Get("workstream"); v != nil {
		if v.Kind != value.String {
			return nil, &value.Error{Class: "TypeError", Message: "checkpoint title must be a string"}
		}
		title = text(v)
	}
	previousRaw, err := client.Checkpoint(ctx, title)
	if err != nil {
		return nil, classify(err)
	}
	var previous *value.Value
	if previousRaw != nil {
		previous, err = value.DecodeText(previousRaw, "utf-8", 0)
		if err != nil {
			return nil, err
		}
	}
	handoffs, err := captureCandidates(ctx, client, event, state, messages, project, true)
	if err != nil {
		return nil, classify(err)
	}
	candidates, err := captureCandidates(ctx, client, event, value.Mapping(), messages, project, false)
	if err != nil {
		return nil, classify(err)
	}
	input := memoryclient.CaptureInput{ProjectBase: baseName(project), ExplicitWorkstream: text(event.Get("workstream"))}
	if previous != nil {
		r, err := captureRecord(previous)
		if err != nil {
			return nil, err
		}
		input.Previous = &r
	}
	for _, group := range []struct {
		records []*value.Value
		target  *[]memoryclient.CaptureRecord
	}{{handoffs, &input.Handoffs}, {candidates, &input.Candidates}} {
		for _, record := range group.records {
			r, err := captureRecord(record)
			if err != nil {
				return nil, err
			}
			*group.target = append(*group.target, r)
		}
	}
	handoffInput, memoryInput := array(), array()
	for _, record := range handoffs {
		body := text(record.Get("body"))
		title, _, _ := strings.Cut(body, "\n")
		handoffInput.Items = append(handoffInput.Items, value.Mapping(value.Field("topic", value.Text(strings.TrimPrefix(title, workstreamPrefix(project)))), value.Field("body", record.Get("body"))))
	}
	for _, record := range candidates {
		memoryInput.Items = append(memoryInput.Items, value.Mapping(value.Field("record_id", record.Get("record_id")), value.Field("kind", record.Get("kind")), value.Field("body", record.Get("body"))))
	}
	explicit := event.Get("workstream")
	if !value.Truth(explicit) {
		explicit = nil
	}
	excerpt := value.Mapping(value.Field("project", value.Text(project)), value.Field("event", event.Get("hook_event_name")), value.Field("explicit_workstream", explicit), value.Field("previous_checkpoint", previous.Get("body")), value.Field("existing_handoffs", handoffInput), value.Field("existing_memories", memoryInput), value.Field("messages", messages))
	selector := value.Mapping()
	observed.Set("selector", selector)
	result, err := captureProvider(ctx, config, excerpt, selector)
	if err != nil {
		if selector.Get("outcome") != nil {
			outcome := "error"
			if captureTimedOut(err) {
				outcome = "timeout"
			}
			observed.Set("selector_outcome", value.Text(outcome))
		}
		return nil, err
	}
	if selector.Get("outcome") != nil {
		outcome := "completed"
		if value.Truth(result.Get("is_error")) {
			outcome = "error"
		}
		observed.Set("selector_outcome", value.Text(outcome))
	}
	if value.Truth(result.Get("is_error")) {
		return nil, fail("checkpoint selection failed; no note saved")
	}
	// ASCII JSON retains isolated surrogate escapes for the client's explicit
	// finite-domain refusal instead of silently replacing their code points.
	selection, err := encoded(result.Get("structured_output"), true)
	if err != nil {
		return nil, err
	}
	input.Selection = selection
	written, err := client.CommitCaptureSelection(ctx, input)
	if written.Workstream != nil {
		state.Set("workstream", value.Text(*written.Workstream))
	}
	if err != nil {
		if errors.Is(err, memoryclient.ErrCaptureSelection) || errors.Is(err, memoryclient.ErrCaptureDomain) {
			return nil, fail(err.Error())
		}
		if errors.Is(err, memoryclient.ErrCaptureWrite) {
			observed.Set("outcome", value.Text("write_error"))
			if err.Error() == "Cairn did not confirm the selected record" {
				return nil, fail(err.Error())
			}
			err = errors.Unwrap(err)
		}
		return nil, classify(err)
	}
	state.Set("captured_digest", value.Text(digest))
	state.Set("captured_messages", integer(len(messages.Items)))
	outcome := "nothing_selected"
	if len(written.Saved) != 0 {
		outcome = "saved"
	}
	captureStatus(state, outcome)
	last := state.Get("last_capture")
	last.Set("selector_calls", integer(1))
	for _, id := range written.Saved {
		last.Get("records").Items = append(last.Get("records").Items, value.Text(id))
	}
	if len(written.Saved) != 0 {
		return value.Mapping(value.Field("systemMessage", value.Text("Cairn selected memories saved: "+strings.Join(written.Saved, ", ")))), nil
	}
	return value.Mapping(), nil
}

func captureTimedOut(err error) bool {
	var hook *hookError
	return errors.As(err, &hook) && (hook.code == "TIMEOUT" || hook.message == "retrieval time budget exhausted")
}

func captureRecord(record *value.Value) (memoryclient.CaptureRecord, error) {
	refused := func() (memoryclient.CaptureRecord, error) {
		return memoryclient.CaptureRecord{}, fail(memoryclient.ErrCaptureDomain.Error())
	}
	for _, key := range []string{"record_id", "body", "kind"} {
		v := record.Get(key)
		if v == nil && key == "kind" {
			continue
		}
		if !validText(v) {
			return refused()
		}
	}
	version := record.Get("version")
	if version == nil || version.Kind != value.Int {
		return refused()
	}
	n, err := strconv.Atoi(version.Integer)
	if err != nil {
		return refused()
	}
	return memoryclient.CaptureRecord{RecordID: text(record.Get("record_id")), Version: n, Kind: text(record.Get("kind")), Body: text(record.Get("body"))}, nil
}

func captureCandidates(ctx context.Context, client *memoryclient.Client, event, state, messages *value.Value, project string, handoff bool) ([]*value.Value, error) {
	var prompt []string
	for _, message := range messages.Items {
		if value.SameText(message.Get("role"), "user") {
			prompt = append(prompt, text(message.Get("text")))
		}
	}
	queryEvent := clone(event)
	queryEvent.Set("hook_event_name", value.Text("UserPromptSubmit"))
	queryEvent.Set("prompt", value.Text(strings.Join(prompt, "\n")))
	intent, err := detailedRetrievalIntent(queryEvent, state, project)
	if err != nil {
		return nil, err
	}
	kinds, limit := []string{"decision", "preference", "lesson", "procedure"}, 3
	if handoff {
		kinds, limit = []string{"note"}, 2
	}
	room := 32000
	view, err := client.Search(ctx, intent.query, memoryclient.SearchOptions{Room: &room, Entities: intent.files, Kinds: kinds})
	if err != nil {
		return nil, err
	}
	var records []*value.Value
	matched := 0
	for _, entry := range view.Index {
		preview, err := decoded(entry)
		if err != nil {
			return nil, err
		}
		if !relevantPreview(preview, intent) {
			continue
		}
		matched++
		if matched > limit {
			break
		}
		raw, err := client.Pull(ctx, entry.PullArguments, 5*time.Second)
		if pullBudget(err) {
			break
		}
		if err != nil {
			return nil, err
		}
		pulled, err := value.DecodeText(raw, "utf-8", 0)
		if err != nil {
			return nil, err
		}
		record := pulled.Get("selection").Get("record")
		body := text(record.Get("body"))
		if value.SameText(record.Get("class"), "A") && (handoff && strings.HasPrefix(body, workstreamPrefix(project)) && !strings.Contains(strings.SplitN(body, "\n", 2)[0], " / Claude session ") || !handoff && contains(kinds, text(record.Get("kind"))) && len(body) <= 6000) {
			records = append(records, record)
		}
	}
	return records, nil
}
