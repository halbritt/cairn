package main

import (
	"context"
	"io"
	"net/url"
	"os"
	"os/user"
	"path/filepath"
	"strconv"
	"time"

	"github.com/halbritt/cairn/core"
)

const help = `Cairn: local memory for agents

Everyday commands:
  remember [--repo PATH] [--shareable] TEXT
  search [--repo PATH] [--purpose context] [--destination local] QUERY
  run [--repo PATH] [--prompt TEXT] [--carrier stdin|argv] [--destination local|hosted] -- COMMAND ARGS...
  list REPO | get UUID | report REPO | docket REPO | impact UUID | replay RECEIPT_UUID

JSON commands (read one request from stdin):
  create edit compile bootstrap grant revoke-grant capture-evidence
  promote issue correct retract dispute resolve usage
  grants (no input)
  recover-run RECEIPT_UUID (retry a runner-owned pending outcome)

Administration: migrate
Default store: ~/.local/share/cairn/socket, database cairn.
Override with CAIRN_DATABASE_URL. Initialize with scripts/local-store.sh start.
CLI is trusted operator administration. Agents use a host-established core Channel.
run forwards process output; its final receipt envelope is on stderr.
replay is historical inspection, not a current delivery authorization.`

func invalid(message string) error { return &core.Error{Code: "INVALID_REQUEST", Message: message} }
func dataDirectory() (string, error) {
	if configured := os.Getenv("CAIRN_HOME"); configured != "" {
		return filepath.Abs(configured)
	}
	home, err := os.UserHomeDir()
	if err != nil {
		return "", err
	}
	return filepath.Join(home, ".local", "share", "cairn"), nil
}
func databaseURL() (string, error) {
	if dsn := os.Getenv("CAIRN_DATABASE_URL"); dsn != "" {
		return dsn, nil
	}
	directory, err := dataDirectory()
	if err != nil {
		return "", err
	}
	owner, err := user.Current()
	if err != nil {
		return "", err
	}
	uri := url.URL{Scheme: "postgresql", User: url.User(owner.Username), Path: "/cairn", RawQuery: url.Values{"host": {filepath.Join(directory, "socket")}, "sslmode": {"disable"}}.Encode()}
	return uri.String(), nil
}
func run(ctx context.Context, args []string, input io.Reader) (any, error) {
	if len(args) == 1 && (args[0] == "help" || args[0] == "--help") {
		return help, nil
	}
	if len(args) == 0 {
		return nil, invalid("expected a command; use --help")
	}
	channel := core.Channel{Principal: "local-uid:" + strconv.Itoa(os.Geteuid()), Operator: true}
	if args[0] == "run" || args[0] == "recover-run" {
		channel.Instrumented = true
	}
	dsn, err := databaseURL()
	if err != nil {
		return nil, err
	}
	connectCtx, cancel := context.WithTimeout(ctx, 5*time.Second)
	store, err := core.Open(connectCtx, dsn, channel)
	cancel()
	if err != nil {
		return nil, err
	}
	defer store.Close()
	if args[0] == "run" {
		return runTask(ctx, store, args[1:])
	}
	ctx, cancel = context.WithTimeout(ctx, 30*time.Second)
	defer cancel()
	switch args[0] {
	case "recover-run":
		if len(args) != 2 {
			return nil, invalid("recover-run requires a receipt UUID")
		}
		return recoverRun(ctx, store, args[1])
	case "remember":
		return remember(ctx, store, args[1:])
	case "search":
		return search(ctx, store, args[1:])
	case "get", "replay", "report", "list", "impact", "docket":
		if len(args) != 2 {
			return nil, invalid("command requires one identifier or repository")
		}
		switch args[0] {
		case "docket":
			return store.Docket(ctx, args[1])
		case "get":
			return store.Get(ctx, args[1])
		case "replay":
			p, err := store.Replay(ctx, args[1])
			return struct {
				Historical bool         `json:"historical"`
				Package    core.Package `json:"package"`
			}{true, p}, err
		case "report":
			return store.Report(ctx, args[1])
		case "list":
			return store.List(ctx, core.ListRequest{Repo: args[1], Limit: 100})
		case "impact":
			return store.Impact(ctx, args[1], 0)
		}
	}
	if len(args) != 1 {
		return nil, invalid("unexpected arguments")
	}
	switch args[0] {
	case "migrate":
		return nil, store.Migrate(ctx)
	case "grants":
		return store.Grants(ctx)
	case "create":
		return invoke(ctx, input, store.Create)
	case "edit":
		return invoke(ctx, input, store.Edit)
	case "bootstrap":
		return invoke(ctx, input, store.Bootstrap)
	case "grant":
		return invoke(ctx, input, store.Grant)
	case "revoke-grant":
		return invoke(ctx, input, store.RevokeGrant)
	case "capture-evidence":
		return invoke(ctx, input, store.CaptureEvidence)
	case "promote":
		return invoke(ctx, input, store.Promote)
	case "issue":
		return invoke(ctx, input, store.Issue)
	case "correct":
		return invoke(ctx, input, store.Correct)
	case "retract":
		return invoke(ctx, input, store.Retract)
	case "dispute":
		return invoke(ctx, input, store.Dispute)
	case "resolve":
		return invoke(ctx, input, store.Resolve)
	case "usage":
		return invoke(ctx, input, store.RecordUsage)
	case "compile":
		return invoke(ctx, input, func(ctx context.Context, req core.CompileRequest) (core.Package, error) {
			return store.Compile(ctx, req, core.Destination{Name: "local", AllowLocal: true})
		})
	default:
		return nil, invalid("unknown command; use --help")
	}
}

func recoverRun(ctx context.Context, store *core.Store, id string) (core.Observation, error) {
	// Validate the UUID with the store before using it as a filesystem component.
	if _, err := store.Replay(ctx, id); err != nil {
		return core.Observation{}, err
	}
	root, err := dataDirectory()
	if err != nil {
		return core.Observation{}, err
	}
	path := filepath.Join(root, "runs", id, "outcome.pending.json")
	info, err := os.Lstat(path)
	if err != nil {
		return core.Observation{}, err
	}
	if !info.Mode().IsRegular() {
		return core.Observation{}, invalid("pending outcome must be a regular runner-owned file")
	}
	file, err := os.Open(path)
	if err != nil {
		return core.Observation{}, err
	}
	defer file.Close()
	var req core.OutcomeRequest
	if err = decode(file, &req); err != nil {
		return core.Observation{}, err
	}
	if req.ReceiptID != id {
		return core.Observation{}, invalid("pending outcome names another receipt")
	}
	observed, err := store.RecordOutcome(ctx, req)
	if err != nil {
		return observed, err
	}
	return observed, os.Rename(path, filepath.Join(root, "runs", id, "outcome.json"))
}
func invoke[Request, Response any](ctx context.Context, input io.Reader, call func(context.Context, Request) (Response, error)) (Response, error) {
	var req Request
	var zero Response
	if err := decode(input, &req); err != nil {
		return zero, invalid(err.Error())
	}
	return call(ctx, req)
}
