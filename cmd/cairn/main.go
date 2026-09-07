package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"os/signal"
	"syscall"

	"github.com/halbritt/cairn/core"
	"github.com/halbritt/cairn/runner"
)

type response struct {
	Schema  string `json:"schema"`
	OK      bool   `json:"ok"`
	Status  string `json:"status"`
	Data    any    `json:"data,omitempty"`
	Message string `json:"message,omitempty"`
}

func main() {
	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer cancel()
	data, err := run(ctx, os.Args[1:], os.Stdin)
	envelope := response{Schema: "cairn.response/1", OK: err == nil, Status: "OK", Data: data}
	exitCode := 0
	if err != nil {
		envelope.Status = core.Code(err)
		envelope.Message = err.Error()
		switch envelope.Status {
		case "RUN_FAILED":
			exitCode = 1
		case "INVALID_REQUEST":
			exitCode = 2
		case "NOT_FOUND":
			exitCode = 3
		case "VERSION_CONFLICT", "IDEMPOTENCY_CONFLICT", "SCHEMA_MISMATCH", "STALE_PACKAGE", "RUN_ALREADY_STARTED":
			exitCode = 4
		case "AUTHORITY_DENIED", "SELF_PROMOTION_DENIED":
			exitCode = 6
		case "BUDGET_REFUSED", "POLICY_UNENFORCEABLE", "OPEN_CONFLICT", "DESTINATION_PROHIBITED", "EVIDENCE_UNAVAILABLE", "ATTRIBUTION_UNRECONCILED", "ATTRIBUTION_CONTRADICTED":
			exitCode = 2
		default:
			exitCode = 7
			// pgx errors may include connection credentials or rejected payloads.
			envelope.Message = "operation failed; inspect the local store and task runtime"
		}
	}
	output := os.Stdout
	if len(os.Args) > 1 && os.Args[1] == "run" {
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
	body, err := io.ReadAll(io.LimitReader(input, 128*1024+1))
	if err != nil {
		return err
	}
	if len(body) > 128*1024 {
		return errors.New("request exceeds 128 KiB")
	}
	decoder := json.NewDecoder(bytes.NewReader(body))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(target); err != nil {
		return err
	}
	var extra any
	if err := decoder.Decode(&extra); err != io.EOF {
		return errors.New("expected exactly one JSON request")
	}
	return nil
}
