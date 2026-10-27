package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"path/filepath"
	"regexp"

	"github.com/halbritt/cairn/core"
	"github.com/halbritt/cairn/internal/buildinfo"
	"github.com/halbritt/cairn/localapi"
)

type agentSubcommandSpec struct {
	Name          string
	Operation     string
	AcceptsArgs   bool
	DistinctQuery bool
	Help          func() (commandHelp, error)
	Dispatch      func(ctx context.Context, client *localapi.Client, session *core.AgentSessionRef, socketPath, tokenPath string, args []string, input io.Reader) (any, error)
}

var eventCommandOperations = map[string]string{
	"publish":         "event-publish",
	"notice-flush":    "event-notice-flush",
	"inbox":           "event-next",
	"ack":             "event-complete",
	"complete":        "event-complete",
	"retry":           "event-retry",
	"renew":           "event-renew",
	"subscribe":       "event-subscribe",
	"unsubscribe":     "event-subscribe",
	"subscriptions":   "event-subscriptions",
	"events":          "event-list",
	"event-status":    "event-inspect",
	"event-stats":     "event-metrics",
	"response-group":  "event-group",
	"response-groups": "event-groups",
}

var agentSubcommands map[string]agentSubcommandSpec

func init() {
	agentSubcommands = buildAgentSubcommandCatalog()
}

func lookupAgentSubcommand(name string) (agentSubcommandSpec, bool) {
	spec, ok := agentSubcommands[name]
	return spec, ok
}

func buildAgentSubcommandCatalog() map[string]agentSubcommandSpec {
	catalog := make(map[string]agentSubcommandSpec, len(localapi.Operations())+len(eventCommandOperations)+8)
	for _, op := range localapi.Operations() {
		name := op.Name
		sub := agentSubcommandSpec{
			Name:          name,
			Operation:     name,
			DistinctQuery: name == "compile" || name == "index" || name == "recompile",
		}
		if topic, ok := agentJSONHelp[name]; ok {
			topic := topic
			sub.Help = func() (commandHelp, error) {
				return formatAgentJSONHelp(name, topic), nil
			}
		} else {
			// Every catalog operation has offline help: hand-written topics
			// carry examples; the rest is derived from the declared spec.
			spec := op
			sub.Help = func() (commandHelp, error) {
				return derivedAgentJSONHelp(spec), nil
			}
		}
		catalog[name] = sub
	}
	for cmd, backingOp := range eventCommandOperations {
		cmd := cmd
		catalog[cmd] = agentSubcommandSpec{
			Name:        cmd,
			Operation:   backingOp,
			AcceptsArgs: true,
			Help:        func() (commandHelp, error) { return commandHelp(eventHelp), nil },
			Dispatch: func(ctx context.Context, client *localapi.Client, _ *core.AgentSessionRef, _, _ string, args []string, input io.Reader) (any, error) {
				return eventCommand(ctx, cmd, args, input, client)
			},
		}
	}
	overrides := []agentSubcommandSpec{
		{
			Name:      "version",
			Operation: "version",
			Dispatch: func(ctx context.Context, client *localapi.Client, _ *core.AgentSessionRef, _, _ string, _ []string, _ io.Reader) (any, error) {
				var server localapi.VersionInfo
				if err := client.Call(ctx, "version", struct{}{}, &server); err != nil {
					return nil, err
				}
				serverProtocol, declared := server.ServerProtocol()
				return struct {
					Schema         string                 `json:"schema"`
					Client         buildinfo.Info         `json:"client"`
					Server         buildinfo.Info         `json:"server"`
					ClientProtocol localapi.ProtocolRange `json:"client_protocol"`
					ServerProtocol localapi.ProtocolRange `json:"server_protocol"`
					Protocol       int                    `json:"protocol"`
				}{"cairn.version/1", buildinfo.Read(), server.Info, localapi.Protocol, serverProtocol, localapi.Protocol.Effective(serverProtocol, declared)}, nil
			},
		},
		{
			Name:        "clients",
			Operation:   "clients",
			AcceptsArgs: true,
			Help:        clientsOperationHelp,
			Dispatch: func(ctx context.Context, client *localapi.Client, _ *core.AgentSessionRef, _, _ string, args []string, _ io.Reader) (any, error) {
				return agentClients(ctx, client, args)
			},
		},
		{
			Name:        "evidence",
			Operation:   "evidence",
			AcceptsArgs: true,
			Help:        evidenceOperationHelp,
			Dispatch: func(ctx context.Context, client *localapi.Client, _ *core.AgentSessionRef, _, _ string, args []string, input io.Reader) (any, error) {
				if len(args) > 0 {
					req, err := evidenceFileRequest(args)
					if err != nil {
						return nil, err
					}
					var result json.RawMessage
					if err := client.Call(ctx, "evidence", req, &result); err != nil {
						return nil, err
					}
					return result, nil
				}
				return callAgentJSONOperation(ctx, client, agentSubcommandSpec{Name: "evidence", Operation: "evidence"}, input)
			},
		},
		{
			Name:        "remember",
			Operation:   "create",
			AcceptsArgs: true,
			Help:        rememberOperationHelp,
			Dispatch: func(ctx context.Context, client *localapi.Client, _ *core.AgentSessionRef, _, _ string, args []string, input io.Reader) (any, error) {
				req, err := rememberRequest(args, input)
				if err != nil {
					return nil, err
				}
				var result json.RawMessage
				if err := client.Call(ctx, "create", req, &result); err != nil {
					return nil, err
				}
				return result, nil
			},
		},
		{
			Name:        "send",
			Operation:   "create",
			AcceptsArgs: true,
			Help:        func() (commandHelp, error) { return commandHelp(sendHelp), nil },
			Dispatch: func(ctx context.Context, client *localapi.Client, session *core.AgentSessionRef, _, _ string, args []string, input io.Reader) (any, error) {
				return agentSend(ctx, client, session, args, input)
			},
		},
		{
			Name:        "run",
			Operation:   "compile",
			AcceptsArgs: true,
			Dispatch: func(ctx context.Context, client *localapi.Client, _ *core.AgentSessionRef, _, _ string, args []string, _ io.Reader) (any, error) {
				return runTask(ctx, client, args)
			},
		},
		{
			Name:        "start",
			Operation:   "index",
			AcceptsArgs: true,
			Dispatch: func(ctx context.Context, client *localapi.Client, _ *core.AgentSessionRef, _, _ string, args []string, _ io.Reader) (any, error) {
				return prepareAgentStart(ctx, client, args)
			},
		},
		{
			Name:        "search",
			Operation:   "index",
			AcceptsArgs: true,
			Help:        searchOperationHelp,
			Dispatch: func(ctx context.Context, client *localapi.Client, _ *core.AgentSessionRef, socketPath, tokenPath string, args []string, input io.Reader) (any, error) {
				return agentSearch(ctx, client, args, socketPath, tokenPath, input)
			},
		},
		{
			Name:        "pull",
			Operation:   "expand",
			AcceptsArgs: true,
			Help:        func() (commandHelp, error) { return pullOperationHelp("pull"), nil },
			Dispatch: func(ctx context.Context, client *localapi.Client, _ *core.AgentSessionRef, _, _ string, args []string, input io.Reader) (any, error) {
				if len(args) > 0 {
					return agentPull(ctx, client, "pull", args)
				}
				return callAgentJSONOperation(ctx, client, agentSubcommandSpec{Name: "pull", Operation: "expand"}, input)
			},
		},
		{
			Name:        "pull-evidence",
			Operation:   "expand-evidence",
			AcceptsArgs: true,
			Help:        func() (commandHelp, error) { return pullOperationHelp("pull-evidence"), nil },
			Dispatch: func(ctx context.Context, client *localapi.Client, _ *core.AgentSessionRef, _, _ string, args []string, input io.Reader) (any, error) {
				if len(args) > 0 {
					return agentPull(ctx, client, "pull-evidence", args)
				}
				return callAgentJSONOperation(ctx, client, agentSubcommandSpec{Name: "pull-evidence", Operation: "expand-evidence"}, input)
			},
		},
	}
	for _, sub := range overrides {
		spec, ok := localapi.LookupOperation(sub.Operation)
		if !ok {
			panic("agent subcommand " + sub.Name + " references unknown localapi operation " + sub.Operation)
		}
		if sub.Help == nil && !sub.AcceptsArgs {
			sub.Help = func() (commandHelp, error) { return derivedAgentJSONHelp(spec), nil }
		}
		catalog[sub.Name] = sub
	}
	return catalog
}

func agentRequest(ctx context.Context, args []string, input io.Reader) (any, error) {
	f := flags("agent")
	tokenFile := f.String("token-file", "", "owner-only API token file")
	socket := f.String("socket", "", "Cairn Unix socket")
	agentID := f.String("agent-id", "", "registered session UUID")
	executionID := f.String("execution-id", "", "current session execution UUID")
	if err := f.Parse(args); err != nil {
		if err == flag.ErrHelp {
			return commandHelp(agentHelp), nil
		}
		return nil, invalid(err.Error())
	}
	if f.NArg() >= 2 && (f.Arg(1) == "--help" || f.Arg(1) == "-h") {
		if f.NArg() != 2 {
			return nil, invalid("operation help takes no additional arguments")
		}
		return agentOperationHelp(f.Arg(0))
	}
	provided := map[string]bool{}
	f.Visit(func(fl *flag.Flag) { provided[fl.Name] = true })
	var session *core.AgentSessionRef
	if provided["agent-id"] || provided["execution-id"] {
		session = &core.AgentSessionRef{AgentID: *agentID, ExecutionID: *executionID}
		if err := session.Validate(); err != nil {
			return nil, err
		}
	}
	socketPath := *socket
	tokenPath := *tokenFile
	enrolled, err := enrolledMachine()
	if err != nil {
		return nil, err
	}
	if enrolled != nil {
		agentCollection = enrolled.Collection
	}
	if provided["socket"] && socketPath == "" {
		return nil, invalid("empty agent socket path")
	}
	if provided["token-file"] && tokenPath == "" {
		return nil, invalid("empty agent token file path")
	}
	if !provided["socket"] || !provided["token-file"] {
		directory, err := dataDirectory()
		if err != nil {
			return nil, err
		}
		if !provided["socket"] {
			socketPath = filepath.Join(directory, "api.sock")
		}
		if !provided["token-file"] {
			tokenPath = filepath.Join(directory, defaultAgentToken(enrolled))
		}
	}
	if f.NArg() < 1 {
		return nil, invalid("agent requires an API operation and JSON on stdin")
	}
	operation := f.Arg(0)
	sub, ok := lookupAgentSubcommand(operation)
	if !ok {
		return nil, invalid("unknown agent operation")
	}
	if !sub.AcceptsArgs && f.NArg() != 1 {
		return nil, invalid("agent operation requires one JSON request on stdin")
	}
	client, err := localapi.NewClient(socketPath, tokenPath)
	if err != nil {
		return nil, err
	}
	defer client.Close()
	client = client.WithDiagnostics(cliDiagnostics())
	if session != nil {
		client = client.ForAgentSession(*session)
	}
	if sub.Dispatch != nil {
		return sub.Dispatch(ctx, client, session, socketPath, tokenPath, f.Args()[1:], input)
	}
	return callAgentJSONOperation(ctx, client, sub, input)
}

func callAgentJSONOperation(ctx context.Context, client *localapi.Client, sub agentSubcommandSpec, input io.Reader) (any, error) {
	operation := sub.Operation
	limit := localapi.RequestBodyLimit(operation)
	body, err := io.ReadAll(io.LimitReader(input, limit+1))
	if err != nil {
		return nil, err
	}
	if int64(len(body)) > limit {
		return nil, invalid(fmt.Sprintf("request exceeds %d KiB", limit/1024))
	}
	if !json.Valid(body) {
		return nil, invalid("expected a JSON request")
	}
	// The explicit JSON compile/replay commands need the same mixed-version
	// guard as search. Legacy requests gain no additional version lookup.
	distinct := false
	if sub.DistinctQuery {
		var fields struct {
			SemanticQuery string `json:"semantic_query"`
		}
		if err := json.Unmarshal(body, &fields); err != nil {
			return nil, invalid("invalid query input request")
		}
		distinct = fields.SemanticQuery != ""
		if distinct {
			choice, err := new(localapi.PreviewCapabilityChoice).ResolveInputs(ctx, client)
			if err != nil {
				return nil, err
			}
			if err = choice.RequireDistinctInput(operation == "recompile"); err != nil {
				return nil, err
			}
		}
	}
	var result json.RawMessage
	if err = client.Call(ctx, operation, json.RawMessage(body), &result); err != nil {
		return nil, err
	}
	if distinct {
		var pkg core.Package
		if operation == "index" {
			var index core.IndexResult
			if err = json.Unmarshal(result, &index); err == nil {
				pkg = index.Package
			}
		} else if operation == "recompile" {
			var replay struct {
				Package core.Package `json:"package"`
			}
			if err = json.Unmarshal(result, &replay); err == nil {
				pkg = replay.Package
			}
		} else {
			err = json.Unmarshal(result, &pkg)
		}
		if err != nil {
			return nil, invalid("invalid distinct query response; do not retry")
		}
		if operation == "recompile" {
			err = localapi.ConfirmDistinctReplaySchema(pkg.Semantic.Schema)
		} else {
			err = localapi.ConfirmDistinctQuerySchema(pkg.Semantic.Schema)
		}
		if err != nil {
			return nil, err
		}
	}
	return result, nil
}

func agentProfileToken(profile, token string) (string, error) {
	if profile == "" {
		return token, nil
	}
	if token != "" || !regexp.MustCompile(`^[a-z][a-z0-9-]{0,63}$`).MatchString(profile) {
		return "", invalid("--profile must name a provisioned profile and cannot combine with --token-file")
	}
	directory, err := dataDirectory()
	if err != nil {
		return "", err
	}
	return filepath.Join(directory, "event-profiles", profile+".token"), nil
}
