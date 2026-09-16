package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"os"
	"os/signal"
	"syscall"

	"github.com/halbritt/cairn/core"
	"github.com/halbritt/cairn/internal/jsontext"
	"github.com/halbritt/cairn/runner"
)

type response struct {
	RefusalID string `json:"refusal_id,omitempty"`
	Schema    string `json:"schema"`
	OK        bool   `json:"ok"`
	Status    string `json:"status"`
	Data      any    `json:"data,omitempty"`
	Message   string `json:"message,omitempty"`
}

func main() {
	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer cancel()
	if len(os.Args) > 1 && (os.Args[1] == "mcp" || os.Args[1] == "opencode-config" || os.Args[1] == "codex-config" || os.Args[1] == "claude-config") {
		var err error
		if os.Args[1] == "mcp" {
			err = serveMCP(ctx, os.Args[2:])
		} else {
			var executable string
			executable, err = os.Executable()
			if err == nil {
				if os.Args[1] == "codex-config" {
					err = writeCodexConfig(os.Stdout, os.Args[2:], executable)
				} else if os.Args[1] == "claude-config" {
					err = writeClaudeConfig(os.Stdout, os.Args[2:], executable)
				} else {
					err = writeOpenCodeConfig(os.Stdout, os.Args[2:], executable)
				}
			}
		}
		if errors.Is(err, flag.ErrHelp) {
			return
		}
		if err != nil {
			fmt.Fprintf(os.Stderr, "cairn %s: %v\n", os.Args[1], err)
			os.Exit(1)
		}
		return
	}
	data, err := run(ctx, os.Args[1:], os.Stdin)
	if text, ok := data.(commandHelp); ok && err == nil {
		if _, err := fmt.Fprint(os.Stdout, text); err != nil {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(1)
		}
		return
	}
	watchOutput := false
	if plan, ok := data.(watchPlan); ok {
		watchOutput = true
		data = nil
		if err == nil {
			var output *os.File
			var cleanup func()
			output, cleanup, err = watchOutputFile(ctx, os.Stdout)
			if err == nil {
				err = plan.execute(ctx, output, os.Stderr)
				cleanup()
			}
		}
		if err == nil || ctx.Err() != nil {
			return
		}
	}
	if plan, ok := data.(agentStartPlan); ok {
		data = nil // Never echo prepared task or memory text in an error envelope.
		if err == nil {
			err = plan.execute(ctx)
		}
	}
	envelope := response{Schema: "cairn.response/1", OK: err == nil, Status: "OK", Data: data}
	exitCode := 0
	if err != nil {
		envelope.Status = core.Code(err)
		var refusal *core.Error
		if errors.As(err, &refusal) {
			envelope.RefusalID = refusal.RefusalID
		}
		envelope.Message = err.Error()
		switch envelope.Status {
		case "CLIENT_SETUP_FAILED", "API_CONNECTION_FAILED":
			exitCode = 7
			envelope.Message = refusal.Error()
		case "INSTALL_FAILED":
			exitCode = 7
		case "RESTORE_PAUSED", "RESTORE_INCOMPLETE", "PURGE_UNRECORDED", "REFUSAL_UNRECORDED", "INTEGRITY_FAILURE", "CHECKPOINT_MISMATCH":
			exitCode = 7
		case "RUN_FAILED":
			exitCode = 1
		case "INVALID_REQUEST":
			exitCode = 2
		case "NOT_FOUND":
			exitCode = 3
		case "STALE_CURSOR", "STALE_LEASE", "STALE_RESTORE", "RESTORE_IN_PROGRESS", "RESTORE_NOT_PAUSED", "ARTIFACT_CHANGED", "ARTIFACT_UNSAFE", "PAYLOAD_UNAVAILABLE", "DEPENDENCY_CONFLICT", "STALE_HANDLE", "STALE_PROPOSAL", "STALE_PREVIEW", "VERSION_CONFLICT", "IDEMPOTENCY_CONFLICT", "SCHEMA_MISMATCH", "STALE_PACKAGE", "RUN_ALREADY_STARTED", "ATTEMPT_TERMINAL":
			exitCode = 4
		case "AUTHORITY_DENIED", "AUTHORITY_INACTIVE", "SCOPE_AUTHORITY_INACTIVE", "SELF_PROMOTION_DENIED":
			exitCode = 6
		case "REPLAY_INCOMPLETE", "IMPACT_PREVIEW_REQUIRED", "BUDGET_REFUSED", "POLICY_UNENFORCEABLE", "OPEN_CONFLICT", "DESTINATION_PROHIBITED", "EVIDENCE_UNAVAILABLE", "INAPPLICABLE", "ATTRIBUTION_UNRECONCILED", "ATTRIBUTION_CONTRADICTED":
			exitCode = 2
		default:
			exitCode = 7
			// pgx errors may include connection credentials or rejected payloads.
			envelope.Message = "operation failed; inspect the local store and task runtime"
		}
	}
	output := os.Stdout
	if watchOutput {
		output = os.Stderr
	}
	_, observedRun := data.(runner.Result)
	if observedRun || (len(os.Args) > 1 && os.Args[1] == "run") {
		output = os.Stderr
		if err != nil {
			fmt.Fprintln(output, "MEM-STATUS/"+envelope.Status)
		}
		if r, ok := data.(runner.Result); ok && err == nil {
			if r.ExitCode != nil && *r.ExitCode != 0 {
				exitCode = 1
			}
			if r.ProcessState == "timeout" {
				exitCode = 124
			}
			if r.ProcessState == "cancelled" {
				exitCode = 130
			}
		}
	}
	if encodeErr := json.NewEncoder(output).Encode(envelope); encodeErr != nil {
		fmt.Fprintln(os.Stderr, encodeErr)
		os.Exit(1)
	}
	os.Exit(exitCode)
}

func decode(input io.Reader, target any) error {
	return decodeBounded(input, target, 128*1024)
}

func decodeBounded(input io.Reader, target any, limit int64) error {
	body, err := io.ReadAll(io.LimitReader(input, limit+1))
	if err != nil {
		return err
	}
	if int64(len(body)) > limit {
		if limit == 128*1024 {
			return errors.New("request exceeds 128 KiB")
		}
		return errors.New("request exceeds size limit")
	}
	if err := jsontext.CheckUnicode(body); err != nil {
		return err
	}
	decoder := json.NewDecoder(bytes.NewReader(body))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(target); err != nil {
		return err
	}
	var extra any
	if err := decoder.Decode(&extra); !errors.Is(err, io.EOF) {
		return errors.New("expected exactly one JSON request")
	}
	return nil
}
