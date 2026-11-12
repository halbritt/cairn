package journal

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"testing"
	"time"
)

type r05Outcome struct {
	Stdout     string  `json:"stdout"`
	Stderr     string  `json:"stderr"`
	ReturnCode int     `json:"returncode"`
	Seconds    float64 `json:"execute_seconds"`
}

type r05Trace struct {
	Native       map[string]any `json:"native"`
	Outcomes     []r05Outcome   `json:"outcomes"`
	ReplayCalls  []any          `json:"replay_calls"`
	FenceJournal map[string]any `json:"fence_journal"`
	FinalCalls   []any          `json:"final_calls"`
	FinalJournal map[string]any `json:"final_journal"`
}

func r05Execute(t *testing.T, native Context, request Request, options Options) r05Outcome {
	t.Helper()
	started := time.Now()
	result, err := Execute(context.Background(), native, request, options)
	if err != nil {
		var public *Error
		if !errors.As(err, &public) {
			t.Fatal(err)
		}
		body, encodeErr := encode(map[string]any{"schema": "cairn.response/1", "ok": false, "status": public.Code, "message": public.Error()})
		if encodeErr != nil {
			t.Fatal(encodeErr)
		}
		result = Result{Stdout: string(body), ExitCode: public.ExitCode}
	}
	return r05Outcome{result.Stdout, result.Stderr, result.ExitCode, time.Since(started).Seconds()}
}

func r05CheckTrace(t *testing.T, trace r05Trace) {
	t.Helper()
	if len(trace.Outcomes) != 6 {
		t.Fatalf("core062 outcomes = %d; want 6", len(trace.Outcomes))
	}
	result := map[string]any{"record_id": resultID, "version": json.Number("1")}
	assertJSON(t, dict(t, dict(t, parse(t, trace.Outcomes[1].Stdout))["data"])["result"], result)
	if len(trace.ReplayCalls) != 2 {
		t.Fatalf("replay API sends = %d; want 2", len(trace.ReplayCalls))
	}
	assertJSON(t, dict(t, trace.ReplayCalls[0])["argv"], dict(t, trace.ReplayCalls[1])["argv"])
	assertJSON(t, dict(t, parse(t, trace.Outcomes[2].Stdout))["status"], "API_CONNECTION_FAILED")
	assertJSON(t, dict(t, parse(t, trace.Outcomes[3].Stdout))["status"], "STALE_SESSION")
	slot := dict(t, trace.FenceJournal["completion"])
	assertJSON(t, []any{slot["status"], slot["result"], slot["replay_code"]}, []any{"committed", result, "STALE_SESSION"})
	for _, outcome := range trace.Outcomes[4:] {
		assertJSON(t, dict(t, parse(t, outcome.Stdout))["status"], "IDEMPOTENCY_CONFLICT")
		assertJSON(t, dict(t, parse(t, outcome.Stdout))["message"],
			"IDEMPOTENCY_CONFLICT: this attempt already journaled a different completion (committed); "+
				"its request cannot change. The host reconciles this attempt; further work needs a new request from its sender or the operator")
	}

	// The Source method leaves exits and the final API effect set unchecked.
	// Keep those additional checks distinct from its eight assertion expressions.
	for i, exit := range []int{0, 0, 4, 4, 4, 4} {
		assertJSON(t, trace.Outcomes[i].ReturnCode, exit)
		assertJSON(t, trace.Outcomes[i].Stderr, "")
	}
	if len(trace.FinalCalls) != 4 {
		t.Fatalf("final API sends = %d; want 4", len(trace.FinalCalls))
	}
	for _, call := range trace.FinalCalls {
		assertJSON(t, call, trace.ReplayCalls[0])
	}
	assertJSON(t, trace.FinalJournal, trace.FenceJournal)
	assertJSON(t, slot["request_id"], dict(t, trace.Native["request_ids"])["completion"])
}

func r05ReadJSON(t *testing.T, name string, target any) {
	t.Helper()
	f, err := os.Open(name)
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	d := json.NewDecoder(f)
	d.UseNumber()
	if err := d.Decode(target); err != nil {
		t.Fatal(err)
	}
}

func TestR05CommittedCommandCore062(t *testing.T) {
	if name := os.Getenv("CAIRN_R05_062_TRACE"); name != "" {
		var trace r05Trace
		r05ReadJSON(t, name, &trace)
		r05CheckTrace(t, trace)
		return
	}
	if name := os.Getenv("CAIRN_R05_062_COMMAND"); name != "" {
		var command struct {
			Context string `json:"context"`
			Kind    string `json:"kind"`
			Input   string `json:"input"`
			Output  string `json:"output"`
		}
		r05ReadJSON(t, name, &command)
		if err := os.Unsetenv("CAIRN_R05_062_COMMAND"); err != nil {
			t.Fatal(err)
		}
		var frame struct {
			StateDir   string              `json:"state_dir"`
			AttemptID  string              `json:"attempt_id"`
			DeliveryID string              `json:"delivery_id"`
			Kind       string              `json:"kind"`
			AgentID    string              `json:"agent_id"`
			Execution  string              `json:"execution_id"`
			Commands   map[string][]string `json:"commands"`
			RequestIDs map[string]string   `json:"request_ids"`
		}
		r05ReadJSON(t, command.Context, &frame)
		native := Context{frame.StateDir, frame.AttemptID, frame.DeliveryID, frame.Kind,
			Session{frame.AgentID, frame.Execution}, frame.Commands, frame.RequestIDs}
		outcome := r05Execute(t, native, Request{Kind: command.Kind, Input: command.Input}, Options{})
		encoded, err := json.Marshal(outcome)
		if err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(command.Output, encoded, 0600); err != nil {
			t.Fatal(err)
		}
		return
	}

	native := fixtureContext(t)
	runner := &scripted{t: t, replies: []Result{handled(t), handled(t), refusal("API_CONNECTION_FAILED", 4), refusal("STALE_SESSION", 4)}}
	trace := r05Trace{Native: native.raw().Values}
	for i, request := range []Request{{Kind: "complete", Input: "Selected result"}, {Kind: "complete", Input: "Selected result"},
		{Kind: "complete", Input: "Selected result"}, {Kind: "complete", Input: "Selected result"},
		{Kind: "complete", Input: "A different result"}, {Kind: "ack"}} {
		trace.Outcomes = append(trace.Outcomes, r05Execute(t, native, request, Options{Runner: runner}))
		if i == 1 {
			trace.ReplayCalls = append([]any(nil), runner.calls...)
		}
		if i == 3 {
			trace.FenceJournal = read(t, native)
		}
	}
	trace.FinalCalls, trace.FinalJournal = runner.calls, read(t, native)
	r05CheckTrace(t, trace)
}

func TestR05DifferentCompletionConflictDiagnostic(t *testing.T) {
	for _, status := range []string{"pending", "committed", "refused"} {
		t.Run(status, func(t *testing.T) {
			native := fixtureContext(t)
			journal := dict(t, dict(t, golden(t, "finish_reply"))["journal"])
			dict(t, journal["completion"])["status"] = status
			put(t, native.StateDir, native.AttemptID, journal)
			_, err := Execute(context.Background(), native, Request{Kind: "complete", Input: "A different result"}, Options{})
			requireError(t, err, "IDEMPOTENCY_CONFLICT", 4)
			assertJSON(t, err.Error(), "IDEMPOTENCY_CONFLICT: this attempt already journaled a different completion ("+status+
				"); its request cannot change. The host reconciles this attempt; further work needs a new request from its sender or the operator")
			assertJSON(t, read(t, native), journal)
		})
	}
}
