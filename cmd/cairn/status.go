package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"strings"
	"time"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
	"github.com/halbritt/cairn/internal/jsontext"
	"github.com/halbritt/cairn/localapi"
)

const statusHelp = `Cairn workstream status board (authenticated API or --operator; wakes no one)

Subcommands:
  cairn status set --stream NAME --state active|waiting|blocked|done [--current TEXT] [--last-landed TEXT] [--next-checkpoint RFC3339] [--blocker TEXT] [--decision-needed TEXT] [--detail TEXT] [--owner PRINCIPAL] [--expected-revision N] [--local | --shareable] [--request-id UUID]
  cairn status get [--stream NAME] [--state active|waiting|blocked|done] [--after STREAM] [--limit N] [--format json|markdown]
  cairn status history --stream NAME [--before-revision N] [--limit N]
  cairn status authorize --stream NAME [--writer PRINCIPAL] [--revoke] [--transfer-owner PRINCIPAL] [--request-id UUID]

Common flags: --profile NAME | --token-file FILE | --operator, --socket PATH, --repo COLLECTION, --agent-id UUID, --execution-id UUID
`

func renderWorkstreamMarkdown(board core.WorkstreamBoard) string {
	var b strings.Builder
	fmt.Fprintf(&b, "# Workstream Status Board (%s)\n\n", board.Repo)
	b.WriteString("| Stream | State | Current | Next checkpoint | Blocker | Decision needed | Writer | Rev | Updated |\n")
	b.WriteString("|---|---|---|---|---|---|---|---:|---|\n")
	escape := func(s string) string {
		s = strings.ReplaceAll(s, "\r", " ")
		s = strings.ReplaceAll(s, "\n", " ")
		s = strings.ReplaceAll(s, "\\", "\\\\")
		s = strings.ReplaceAll(s, "|", "\\|")
		if strings.TrimSpace(s) == "" {
			return "—"
		}
		return s
	}
	for _, st := range board.Streams {
		cp := "—"
		if st.NextCheckpoint != nil {
			cp = st.NextCheckpoint.UTC().Format(time.RFC3339)
		}
		fmt.Fprintf(&b, "| %s | %s | %s | %s | %s | %s | %s | %d | %s |\n",
			escape(st.Stream), escape(st.State), escape(st.Current), cp,
			escape(st.Blocker), escape(st.DecisionNeeded), escape(st.Writer),
			st.Revision, st.UpdatedAt.UTC().Format(time.RFC3339))
	}
	if board.More && board.NextAfter != "" {
		fmt.Fprintf(&b, "\n*(More streams available; pass --after %s)*\n", escape(board.NextAfter))
	}
	return b.String()
}

var statusSubcommandAllowedFlags = map[string]map[string]bool{
	"set": {
		"stream": true, "state": true, "current": true, "last-landed": true,
		"next-checkpoint": true, "blocker": true, "decision-needed": true,
		"detail": true, "owner": true, "expected-revision": true,
		"local": true, "shareable": true, "request-id": true,
	},
	"get": {
		"stream": true, "state": true, "after": true, "limit": true, "format": true,
	},
	"history": {
		"stream": true, "before-revision": true, "limit": true,
	},
	"authorize": {
		"stream": true, "writer": true, "revoke": true, "transfer-owner": true, "request-id": true,
	},
}

func statusCommand(ctx context.Context, args []string, input io.Reader, client *localapi.Client, callerSession *core.AgentSessionRef) (any, error) {
	if len(args) == 0 || args[0] == "--help" || args[0] == "-h" || args[0] == "help" {
		return commandHelp(statusHelp), nil
	}
	subcommand := args[0]
	allowedSub, validSub := statusSubcommandAllowedFlags[subcommand]
	if !validSub {
		return nil, invalid("unknown status subcommand; expected set, get, history, or authorize")
	}
	subArgs := args[1:]

	f := flags("status " + subcommand)
	token := f.String("token-file", "", "API token file")
	profile := f.String("profile", "", "provisioned event profile name")
	socket := f.String("socket", "", "API socket")
	operatorMode := f.Bool("operator", false, "run directly against the local operator database")
	repo := f.String("repo", "", "collection repository, defaults to profile")
	collection := f.String("collection", "", "alias for repo")
	session := f.String("session", "", "registered session ref (agent-id:execution-id)")
	agentID := f.String("agent-id", "", "registered session UUID")
	executionID := f.String("execution-id", "", "current session execution UUID")

	requestID := f.String("request-id", "", "stable retry UUID (generated when omitted)")
	stream := f.String("stream", "", "workstream name (1..64 ASCII letters, digits or _.-)")
	state := f.String("state", "", "workstream state (active|waiting|blocked|done)")
	current := f.String("current", "", "current work item and summary (<= 512 bytes)")
	lastLanded := f.String("last-landed", "", "last landed commit and summary (<= 512 bytes)")
	nextCheckpoint := f.String("next-checkpoint", "", "next checkpoint timestamp (RFC3339)")
	blocker := f.String("blocker", "", "current blocker (<= 512 bytes)")
	decisionNeeded := f.String("decision-needed", "", "decision needed from lead/coordinator/owner (<= 512 bytes)")
	detail := f.String("detail", "", "paths to receipts or notes (<= 1024 bytes)")
	owner := f.String("owner", "", "initial or transferred owner principal")
	expectedRev := f.Int64("expected-revision", 0, "expected current revision for optimistic concurrency")
	localOnly := f.Bool("local", false, "set sensitivity to local")
	shareableFlag := f.Bool("shareable", false, "explicitly set sensitivity to shareable")
	after := f.String("after", "", "exclusive stream cursor for pagination")
	beforeRev := f.Int64("before-revision", 0, "exclusive revision cursor for history")
	limit := f.Int("limit", 0, "page size")
	format := f.String("format", "json", "output format for get (json|markdown)")
	writer := f.String("writer", "", "principal to authorize or revoke on stream")
	revoke := f.Bool("revoke", false, "revoke writer permission")
	transferOwner := f.String("transfer-owner", "", "new owner principal for stream")

	if err := f.Parse(subArgs); err != nil {
		if err == flag.ErrHelp {
			return commandHelp(statusHelp), nil
		}
		return nil, invalid(err.Error())
	}
	if f.NArg() > 0 {
		return nil, invalid("unexpected positional arguments")
	}
	provided := map[string]bool{}
	f.Visit(func(opt *flag.Flag) { provided[opt.Name] = true })
	commonFlags := map[string]bool{
		"token-file": true, "profile": true, "socket": true, "operator": true,
		"repo": true, "collection": true, "session": true, "agent-id": true, "execution-id": true,
	}
	for name := range provided {
		if !commonFlags[name] && !allowedSub[name] {
			return nil, invalid(fmt.Sprintf("--%s is not valid for status %s", name, subcommand))
		}
	}
	if provided["repo"] && provided["collection"] && *repo != *collection {
		return nil, invalid("conflicting --repo and --collection values")
	}
	scopeRepo := *repo
	if scopeRepo == "" {
		scopeRepo = *collection
	}
	if client != nil && (provided["token-file"] || provided["profile"] || provided["socket"] || provided["operator"]) {
		return nil, invalid("agent connection flags must precede status")
	}
	if provided["token-file"] && *token == "" {
		return nil, invalid("empty token file path")
	}
	if provided["socket"] && *socket == "" {
		return nil, invalid("empty socket path")
	}
	if *operatorMode && (provided["token-file"] || provided["profile"] || provided["socket"] || provided["session"] || provided["agent-id"] || provided["execution-id"]) {
		return nil, invalid("--operator cannot combine with API connection or session flags")
	}
	if *profile != "" {
		var err error
		*token, err = agentProfileToken(*profile, *token)
		if err != nil {
			return nil, err
		}
	}
	if provided["session"] && (provided["agent-id"] || provided["execution-id"]) {
		return nil, invalid("cannot combine --session with --agent-id or --execution-id")
	}
	if *session != "" {
		parts := strings.SplitN(*session, ":", 2)
		if len(parts) != 2 {
			return nil, invalid("--session must be AGENT_UUID:EXECUTION_UUID")
		}
		*agentID, *executionID = parts[0], parts[1]
	}
	sessionSelected := provided["session"] || provided["agent-id"] || provided["execution-id"]
	var sessionRef *core.AgentSessionRef
	if sessionSelected {
		ref := core.AgentSessionRef{AgentID: *agentID, ExecutionID: *executionID}
		if err := ref.Validate(); err != nil {
			return nil, err
		}
		if callerSession != nil && ref != *callerSession {
			return nil, invalid("conflicting agent session flags")
		}
		if callerSession == nil {
			sessionRef = &ref
		}
	}
	if *localOnly && *shareableFlag {
		return nil, invalid("cannot combine --local and --shareable")
	}
	sens := ""
	if *localOnly {
		sens = "local"
	} else if *shareableFlag {
		sens = "shareable"
	}
	return dispatchStatusSubcommand(ctx, subcommand, scopeRepo, *requestID, *stream, *state, *current, *lastLanded, *nextCheckpoint, *blocker, *decisionNeeded, *detail, *owner, *expectedRev, sens, *after, *beforeRev, *limit, *format, *writer, *revoke, *transferOwner, *token, *socket, *operatorMode, client, sessionRef)
}

func dispatchStatusSubcommand(
	ctx context.Context, subcommand, scopeRepo, requestID, stream, state, current, lastLanded, nextCheckpoint, blocker, decisionNeeded, detail, owner string,
	expectedRev int64, sens, after string, beforeRev int64, limit int, format, writer string, revoke bool, transferOwner, token, socket string,
	operatorMode bool, client *localapi.Client, sessionRef *core.AgentSessionRef,
) (any, error) {
	if format != "json" && format != "markdown" {
		return nil, invalid("--format must be json or markdown")
	}
	var operation string
	var req any

	switch subcommand {
	case "set":
		if strings.TrimSpace(stream) == "" {
			return nil, invalid("--stream is required for status set")
		}
		if strings.TrimSpace(state) == "" {
			return nil, invalid("--state is required for status set (active|waiting|blocked|done)")
		}
		if requestID == "" {
			requestID = uuid.NewString()
		} else if uuid.Validate(requestID) != nil {
			return nil, invalid("--request-id must be a UUID")
		}
		var parsedCheckpoint *time.Time
		if nextCheckpoint != "" {
			t, err := parseEventTimestamp(nextCheckpoint)
			if err != nil || t.IsZero() || t.Year() < 1 || t.Year() > 9999 {
				return nil, invalid("--next-checkpoint must be an RFC3339 timestamp")
			}
			parsedCheckpoint = &t
		}
		operation = "status-set"
		req = core.SetWorkstreamStatusRequest{
			RequestID: requestID, Repo: scopeRepo, Stream: stream, State: state,
			Current: current, LastLanded: lastLanded, NextCheckpoint: parsedCheckpoint,
			Blocker: blocker, DecisionNeeded: decisionNeeded, Detail: detail,
			Sensitivity: sens, Owner: owner, ExpectedRevision: expectedRev,
		}
	case "get":
		operation = "status-get"
		req = core.GetWorkstreamStatusRequest{
			Repo: scopeRepo, Stream: stream, State: state, After: after, Limit: limit,
		}
	case "history":
		if strings.TrimSpace(stream) == "" {
			return nil, invalid("--stream is required for status history")
		}
		operation = "status-history"
		req = core.WorkstreamStatusHistoryRequest{
			Repo: scopeRepo, Stream: stream, BeforeRevision: beforeRev, Limit: limit,
		}
	case "authorize":
		if strings.TrimSpace(stream) == "" {
			return nil, invalid("--stream is required for status authorize")
		}
		if writer == "" && transferOwner == "" {
			return nil, invalid("supply --writer and/or --transfer-owner for status authorize")
		}
		if requestID == "" {
			requestID = uuid.NewString()
		} else if uuid.Validate(requestID) != nil {
			return nil, invalid("--request-id must be a UUID")
		}
		operation = "status-authorize"
		req = core.AuthorizeWorkstreamStatusRequest{
			RequestID: requestID, Repo: scopeRepo, Stream: stream,
			Writer: writer, Revoke: revoke, TransferOwner: transferOwner,
		}
	}

	if err := jsontext.CheckValue(req); err != nil {
		return nil, invalid(err.Error())
	}
	encoded, err := json.Marshal(req)
	if err != nil {
		return nil, err
	}
	if operatorMode {
		res, err := run(ctx, []string{operation}, strings.NewReader(string(encoded)))
		if err != nil || format != "markdown" {
			return res, err
		}
		board, ok := res.(core.WorkstreamBoard)
		if !ok {
			return res, nil
		}
		return commandHelp(renderWorkstreamMarkdown(board)), nil
	}
	var raw json.RawMessage
	if client == nil {
		connArgs := []string{}
		if token != "" {
			connArgs = append(connArgs, "--token-file", token)
		}
		if socket != "" {
			connArgs = append(connArgs, "--socket", socket)
		}
		if sessionRef != nil {
			connArgs = append(connArgs, "--agent-id", sessionRef.AgentID, "--execution-id", sessionRef.ExecutionID)
		}
		res, err := agentRequest(ctx, append(connArgs, operation), strings.NewReader(string(encoded)))
		if err != nil {
			return nil, err
		}
		rawBytes, ok := res.(json.RawMessage)
		if !ok {
			return res, nil
		}
		raw = rawBytes
	} else {
		if sessionRef != nil {
			client = client.ForAgentSession(*sessionRef)
		}
		if err := client.Call(ctx, operation, req, &raw); err != nil {
			return nil, err
		}
	}
	if format == "markdown" {
		var board core.WorkstreamBoard
		if err := json.Unmarshal(raw, &board); err != nil {
			return nil, err
		}
		return commandHelp(renderWorkstreamMarkdown(board)), nil
	}
	return raw, nil
}
