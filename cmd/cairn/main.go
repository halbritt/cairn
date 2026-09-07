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
	"strconv"
	"syscall"
	"time"

	"github.com/halbritt/cairn/core"
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
	ctx, timeout := context.WithTimeout(ctx, 30*time.Second)
	defer timeout()
	data, err := run(ctx, os.Args[1:], os.Stdin)
	envelope := response{Schema: "cairn.response/1", OK: err == nil, Status: "OK", Data: data}
	exitCode := 0
	if err != nil {
		envelope.Status = core.Code(err)
		envelope.Message = err.Error()
		switch envelope.Status {
		case "INVALID_REQUEST":
			exitCode = 2
		case "NOT_FOUND":
			exitCode = 3
		case "VERSION_CONFLICT", "IDEMPOTENCY_CONFLICT", "SCHEMA_MISMATCH":
			exitCode = 4
		case "AUTHORITY_DENIED":
			exitCode = 6
		default:
			exitCode = 7
			// pgx errors may include connection credentials or rejected payloads.
			envelope.Message = "database operation failed; inspect database health and schema"
		}
	}
	if encodeErr := json.NewEncoder(os.Stdout).Encode(envelope); encodeErr != nil {
		fmt.Fprintln(os.Stderr, encodeErr)
		os.Exit(1)
	}
	os.Exit(exitCode)
}

func run(ctx context.Context, args []string, input io.Reader) (any, error) {
	invalid := func(message string) error { return &core.Error{Code: "INVALID_REQUEST", Message: message} }
	if len(args) == 1 && (args[0] == "--help" || args[0] == "help") {
		return "cairn migrate | create < request.json | edit < request.json | get UUID; requires CAIRN_DATABASE_URL; local advisory administration only", nil
	}
	if len(args) < 1 {
		return nil, invalid("expected migrate, create, edit or get; use --help")
	}
	switch args[0] {
	case "migrate", "create", "edit":
		if len(args) != 1 {
			return nil, invalid("unexpected arguments")
		}
	case "get":
		if len(args) != 2 {
			return nil, invalid("get requires a record UUID")
		}
	default:
		return nil, invalid("unknown command; use --help")
	}
	channel := core.Channel{Principal: "local-uid:" + strconv.Itoa(os.Geteuid())}
	store, err := core.Open(ctx, os.Getenv("CAIRN_DATABASE_URL"), channel)
	if err != nil {
		return nil, err
	}
	defer store.Close()
	switch args[0] {
	case "migrate":
		return nil, store.Migrate(ctx)
	case "get":
		return store.Get(ctx, args[1])
	case "create":
		var req core.CreateRequest
		if err = decode(input, &req); err != nil {
			return nil, invalid(err.Error())
		}
		return store.Create(ctx, req)
	case "edit":
		var req core.EditRequest
		if err = decode(input, &req); err != nil {
			return nil, invalid(err.Error())
		}
		return store.Edit(ctx, req)
	}
	panic("validated command not handled")
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
