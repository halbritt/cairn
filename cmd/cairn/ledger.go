package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"os"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
	"github.com/halbritt/cairn/internal/jsontext"
	"github.com/halbritt/cairn/localapi"
)

const ledgerHelp = `Cairn hosted campaign ledger (authenticated API or --operator; no private journal files)

Subcommands:
  cairn ledger get --campaign NAME [--kind assignment|decision|blocker|hold] [--status STATUS] [--owner PRINCIPAL] [--op-limit N]
  cairn ledger set --campaign NAME --kind assignment|decision|blocker|hold --key KEY [--status STATUS] [--summary TEXT] [--owner PRINCIPAL] [--detail TEXT] [--source-id UUID --source-version N] [--result-id UUID --result-version N] [--event-id UUID] [--delivery-id UUID] [--due RFC3339] [--expected-revision N] [--request-id UUID]
  cairn ledger sync-context --campaign NAME [--request-id UUID]
  cairn ledger publish --campaign NAME --op-key KEY (--to PRINCIPAL | --topic TOPIC | --pool POOL [--workspace PATH --harness NAME --model NAME --capability CAP]) --kind EVENT_KIND --version N RECORD_UUID [--summary TEXT] [--blocker] [--action none|review|decide] [--supersedes KEY] [--causation-id UUID] [--correlation-id UUID] [--expect-by RFC3339] [--expect-next-by RFC3339] [--item-key KEY] [--item-kind KIND] [--item-status STATUS]
  cairn ledger send --campaign NAME --op-key KEY (--to PRINCIPAL | --topic TOPIC | --pool POOL [--workspace PATH --harness NAME --model NAME --capability CAP]) --kind EVENT_KIND --stdin [--plain] [--shareable] [--source-kind NOTE_KIND] [--summary TEXT] [--blocker] [--action none|review|decide] [--supersedes KEY] [--causation-id UUID] [--correlation-id UUID] [--expect-by RFC3339] [--expect-next-by RFC3339] [--item-key KEY] [--item-kind KIND] [--item-status STATUS]
  cairn ledger complete --campaign NAME --op-key KEY [--lease LEASE_UUID] --stdin [--shareable] [--source-kind NOTE_KIND] [--reply-to PRINCIPAL] [--causation-id UUID] [--correlation-id UUID] [--summary TEXT] [--item-key KEY] [--item-status STATUS] DELIVERY_UUID
  cairn ledger ack --campaign NAME --op-key KEY [--lease LEASE_UUID] [--disposition handled|ignored|failed] [--code CODE] [--item-key KEY] [--item-kind KIND] [--item-status STATUS] DELIVERY_UUID
  cairn ledger handoff-check --campaign NAME
  cairn ledger compact --campaign NAME [--request-id UUID]
  cairn ledger handoff --campaign NAME [--if-needed] (--to-session AGENT_UUID:EXECUTION_UUID | --to-agent-id UUID --to-execution-id UUID | --new-binding BINDING --new-native-session NATIVE_ID [--workspace PATH --harness NAME --project NAME --model NAME --delivery-mode existing-session|fresh-worker]) [--shareable] [--reason TEXT] [--note TEXT] [--request-id UUID]

Common flags: --profile NAME | --token-file FILE | --operator, --socket PATH, --repo COLLECTION | --collection COLLECTION, --session AGENT_UUID:EXECUTION_UUID | (--agent-id UUID --execution-id UUID)
The get command returns the campaign description, members with their role labels, and ledger items in one call.
Do not keep private intent, result, or receipt files on disk; retry uncertain publish, send, complete, or ack calls with the same --campaign and --op-key.
`

var ledgerSubcommandAllowedFlags = map[string]map[string]bool{
	"get": {
		"campaign": true, "kind": true, "status": true, "owner": true, "op-limit": true,
	},
	"set": {
		"campaign": true, "kind": true, "key": true, "status": true, "summary": true,
		"owner": true, "detail": true, "source-id": true, "source-version": true,
		"result-id": true, "result-version": true, "event-id": true, "delivery-id": true,
		"due": true, "expected-revision": true, "request-id": true,
	},
	"sync-context": {
		"campaign": true, "request-id": true,
	},
	"publish": {
		"campaign": true, "op-key": true, "to": true, "topic": true, "pool": true,
		"workspace": true, "harness": true, "model": true, "capability": true,
		"kind": true, "version": true, "summary": true, "blocker": true,
		"action": true, "supersedes": true, "causation-id": true, "correlation-id": true,
		"expect-by": true, "expect-next-by": true,
		"item-key": true, "item-kind": true, "item-status": true,
	},
	"send": {
		"campaign": true, "op-key": true, "to": true, "topic": true, "pool": true,
		"workspace": true, "harness": true, "model": true, "capability": true,
		"kind": true, "stdin": true, "plain": true, "shareable": true, "source-kind": true,
		"summary": true, "blocker": true, "action": true, "supersedes": true,
		"causation-id": true, "correlation-id": true,
		"expect-by": true, "expect-next-by": true,
		"item-key": true, "item-kind": true, "item-status": true,
	},
	"complete": {
		"campaign": true, "op-key": true, "lease": true, "stdin": true,
		"shareable": true, "source-kind": true, "reply-to": true,
		"causation-id": true, "correlation-id": true, "summary": true,
		"item-key": true, "item-status": true,
	},
	"ack": {
		"campaign": true, "op-key": true, "lease": true, "disposition": true,
		"code": true, "item-key": true, "item-kind": true, "item-status": true,
	},
	"handoff-check": {
		"campaign": true,
	},
	"compact": {
		"campaign": true, "request-id": true,
	},
	"handoff": {
		"campaign": true, "if-needed": true, "to-session": true, "to-agent-id": true, "to-execution-id": true,
		"new-binding": true, "new-native-session": true, "workspace": true, "harness": true, "project": true, "model": true, "delivery-mode": true,
		"shareable": true, "reason": true, "note": true, "request-id": true,
	},
}

func ledgerCommand(ctx context.Context, args []string, input io.Reader, client *localapi.Client, callerSession *core.AgentSessionRef) (any, error) {
	if len(args) == 0 || args[0] == "--help" || args[0] == "-h" || args[0] == "help" {
		return commandHelp(ledgerHelp), nil
	}
	subcommand := args[0]
	allowedSub, validSub := ledgerSubcommandAllowedFlags[subcommand]
	if !validSub {
		return nil, invalid("unknown ledger subcommand; expected get, set, sync-context, publish, send, complete, ack, handoff-check, compact, or handoff")
	}
	f := flags("ledger " + subcommand)
	token := f.String("token-file", "", "API token file")
	profile := f.String("profile", "", "provisioned event profile name")
	socket := f.String("socket", "", "API socket")
	operatorMode := f.Bool("operator", false, "run directly against the local operator database")
	repo := f.String("repo", "", "collection repository, defaults to profile")
	collection := f.String("collection", "", "alias for --repo")
	sessionFlag := f.String("session", "", "registered AGENT_UUID:EXECUTION_UUID")
	agentID := f.String("agent-id", "", "registered agent UUID")
	executionID := f.String("execution-id", "", "registered execution UUID")

	campaign := f.String("campaign", "", "campaign name")
	kind := f.String("kind", "", "ledger item kind or event kind")
	itemKeyFlag := f.String("key", "", "ledger item key")
	status := f.String("status", "", "item status")
	summary := f.String("summary", "", "concise single-line summary")
	owner := f.String("owner", "", "owner principal")
	detail := f.String("detail", "", "optional item detail")
	sourceID := f.String("source-id", "", "source record UUID")
	sourceVer := f.Int("source-version", 0, "source record version")
	resultID := f.String("result-id", "", "result record UUID")
	resultVer := f.Int("result-version", 0, "result record version")
	eventID := f.String("event-id", "", "linked event UUID")
	deliveryIDFlag := f.String("delivery-id", "", "linked delivery UUID")
	dueStr := f.String("due", "", "due timestamp (RFC3339)")
	expectedRevision := f.Int64("expected-revision", 0, "expected item revision")
	requestID := f.String("request-id", "", "stable mutation request UUID")
	opLimit := f.Int("op-limit", 0, "maximum journaled operations to return")

	opKey := f.String("op-key", "", "campaign operation idempotency key")
	to := f.String("to", "", "recipient principal")
	topic := f.String("topic", "", "topic destination")
	pool := f.String("pool", "", "pool destination")
	workspace := f.String("workspace", "", "pool workspace")
	harness := f.String("harness", "", "pool harness")
	model := f.String("model", "", "pool model")
	var capabilities []string
	f.Func("capability", "pool capability; repeat", func(v string) error { capabilities = append(capabilities, v); return nil })
	version := f.Int("version", 0, "record version")
	blocker := f.Bool("blocker", false, "mark event as blocker")
	action := f.String("action", "", "notice action (none|review|decide)")
	supersedes := f.String("supersedes", "", "notice supersedes key")
	causationID := f.String("causation-id", "", "causation event UUID")
	correlationID := f.String("correlation-id", "", "correlation UUID")
	expectByStr := f.String("expect-by", "", "request expect-by timestamp (RFC3339)")
	expectNextByStr := f.String("expect-next-by", "", "notice expect-next-by timestamp (RFC3339)")
	stdin := f.Bool("stdin", false, "read body from stdin")
	plain := f.Bool("plain", false, "prose mode: render any cd1 body to human-readable prose before sending")
	shareable := f.Bool("shareable", false, "mark created note shareable")
	sourceKind := f.String("source-kind", "note", "created note kind")
	lease := f.String("lease", "", "delivery lease UUID")
	replyTo := f.String("reply-to", "", "recipient principal for request completion response")
	disposition := f.String("disposition", "handled", "ack disposition")
	code := f.String("code", "", "ack failure/ignore code")
	linkKey := f.String("item-key", "", "linked ledger item key")
	linkKind := f.String("item-kind", "", "linked ledger item kind")
	linkStatus := f.String("item-status", "", "linked ledger item status")
	ifNeeded := f.Bool("if-needed", false, "only hand off if coordinator age or compaction threshold is reached")
	toSessionFlag := f.String("to-session", "", "successor AGENT_UUID:EXECUTION_UUID")
	toAgentID := f.String("to-agent-id", "", "successor agent UUID")
	toExecutionID := f.String("to-execution-id", "", "successor execution UUID")
	newBinding := f.String("new-binding", "", "launcher binding for fresh coordinator session")
	newNativeSession := f.String("new-native-session", "", "native session ID for fresh coordinator session")
	project := f.String("project", "", "project metadata for fresh coordinator session")
	deliveryMode := f.String("delivery-mode", "", "delivery mode for fresh coordinator session")
	reason := f.String("reason", "", "handoff reason")
	note := f.String("note", "", "optional coordinator handoff note")

	if err := f.Parse(args[1:]); err != nil {
		if err == flag.ErrHelp {
			return commandHelp(ledgerHelp), nil
		}
		return nil, invalid(err.Error())
	}
	provided := map[string]bool{}
	var disallowed string
	f.Visit(func(fl *flag.Flag) {
		provided[fl.Name] = true
		switch fl.Name {
		case "token-file", "profile", "socket", "operator", "repo", "collection", "session", "agent-id", "execution-id":
		default:
			if !allowedSub[fl.Name] && disallowed == "" {
				disallowed = fl.Name
			}
		}
	})
	if disallowed != "" {
		return nil, invalid(fmt.Sprintf("--%s is not valid for ledger %s", disallowed, subcommand))
	}
	if provided["repo"] && provided["collection"] && *repo != *collection {
		return nil, invalid("conflicting --repo and --collection values")
	}
	scopeRepo := *repo
	if scopeRepo == "" {
		scopeRepo = *collection
	}
	if client != nil && (provided["token-file"] || provided["profile"] || provided["socket"] || provided["operator"]) {
		return nil, invalid("agent connection flags must precede ledger")
	}
	if provided["token-file"] && *token == "" {
		return nil, invalid("empty token file path")
	}
	if provided["socket"] && *socket == "" {
		return nil, invalid("empty socket path")
	}
	if *operatorMode && (provided["token-file"] || provided["profile"] || provided["socket"] || provided["session"] || provided["agent-id"] || provided["execution-id"] || callerSession != nil) {
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
	if *sessionFlag != "" {
		parts := strings.SplitN(*sessionFlag, ":", 2)
		if len(parts) != 2 {
			return nil, invalid("--session must be AGENT_UUID:EXECUTION_UUID")
		}
		*agentID, *executionID = parts[0], parts[1]
	}
	sessionRef := callerSession
	if provided["session"] || provided["agent-id"] || provided["execution-id"] {
		ref := core.AgentSessionRef{AgentID: *agentID, ExecutionID: *executionID}
		if err := ref.Validate(); err != nil {
			return nil, err
		}
		if callerSession != nil && ref != *callerSession {
			return nil, invalid("conflicting agent session flags")
		}
		sessionRef = &ref
	}
	if strings.TrimSpace(*campaign) == "" {
		return nil, invalid("--campaign is required")
	}
	reqID := *requestID
	if reqID == "" {
		reqID = uuid.NewString()
	} else if uuid.Validate(reqID) != nil {
		return nil, invalid("--request-id must be a UUID")
	}
	callOp := func(opName string, payload any) (any, error) {
		if err := jsontext.CheckValue(payload); err != nil {
			return nil, invalid(err.Error())
		}
		encoded, mErr := json.Marshal(payload)
		if mErr != nil {
			return nil, mErr
		}
		if *operatorMode {
			return run(ctx, []string{opName}, strings.NewReader(string(encoded)))
		}
		if client == nil {
			connArgs := []string{}
			if *token != "" {
				connArgs = append(connArgs, "--token-file", *token)
			}
			if *socket != "" {
				connArgs = append(connArgs, "--socket", *socket)
			}
			if sessionRef != nil {
				connArgs = append(connArgs, "--agent-id", sessionRef.AgentID, "--execution-id", sessionRef.ExecutionID)
			}
			return agentRequest(ctx, append(connArgs, opName), strings.NewReader(string(encoded)))
		}
		c := client
		if sessionRef != nil {
			c = c.ForAgentSession(*sessionRef)
		}
		var out json.RawMessage
		if err := c.Call(ctx, opName, payload, &out); err != nil {
			return nil, err
		}
		return out, nil
	}
	return dispatchLedgerSubcommand(subcommand, f, input, callOp, provided, scopeRepo, *campaign, reqID,
		*kind, *itemKeyFlag, *status, *summary, *owner, *detail, *sourceID, *sourceVer, *resultID, *resultVer,
		*eventID, *deliveryIDFlag, *dueStr, *expectedRevision, *opLimit,
		*opKey, *to, *topic, *pool, *workspace, *harness, *model, capabilities,
		*version, *blocker, *action, *supersedes, *causationID, *correlationID, *expectByStr, *expectNextByStr,
		*stdin, *plain, *shareable, *sourceKind, *lease, *replyTo, *disposition, *code, *linkKey, *linkKind, *linkStatus,
		*ifNeeded, *toSessionFlag, *toAgentID, *toExecutionID, *newBinding, *newNativeSession, *project, *deliveryMode, *reason, *note)
}

func dispatchLedgerSubcommand(
	subcommand string, f *flag.FlagSet, input io.Reader,
	callOp func(string, any) (any, error), provided map[string]bool,
	repo, campaign, reqID, kind, itemKeyFlag, status, summary, owner, detail string,
	sourceID string, sourceVer int, resultID string, resultVer int,
	eventID, deliveryIDFlag, dueStr string, expectedRevision int64, opLimit int,
	opKey, to, topic, pool, workspace, harness, model string, capabilities []string,
	version int, blocker bool, action, supersedes, causationID, correlationID, expectByStr, expectNextByStr string,
	stdin, plain, shareable bool, sourceKind, lease, replyTo, disposition, code, linkKey, linkKind, linkStatus string,
	ifNeeded bool, toSessionFlag, toAgentID, toExecutionID, newBinding, newNativeSession, project, deliveryMode, reason, note string,
) (any, error) {
	if provided["summary"] && strings.TrimSpace(summary) == "" {
		return nil, invalid("--summary cannot be empty when specified")
	}
	switch subcommand {
	case "get":
		if f.NArg() != 0 {
			return nil, invalid("ledger get takes no positional arguments")
		}
		return callOp("ledger-get", core.GetCampaignLedgerRequest{
			Repo: repo, Campaign: campaign, Kind: kind, Status: status, Owner: owner, OpLimit: opLimit,
		})
	case "set":
		if f.NArg() != 0 || kind == "" || itemKeyFlag == "" {
			return nil, invalid("ledger set requires --kind, --key and no positional arguments")
		}
		var detailPtr *string
		if provided["detail"] {
			detailPtr = &detail
		}
		var srcRef *core.RecordVersionRef
		if sourceID != "" || sourceVer != 0 {
			if sourceID == "" || sourceVer <= 0 {
				return nil, invalid("both --source-id and positive --source-version are required together")
			}
			srcRef = &core.RecordVersionRef{RecordID: strings.TrimPrefix(sourceID, "cairn:"), Version: sourceVer}
		}
		var resRef *core.RecordVersionRef
		if resultID != "" || resultVer != 0 {
			if resultID == "" || resultVer <= 0 {
				return nil, invalid("both --result-id and positive --result-version are required together")
			}
			resRef = &core.RecordVersionRef{RecordID: strings.TrimPrefix(resultID, "cairn:"), Version: resultVer}
		}
		var dueAt *time.Time
		if dueStr != "" {
			t, err := parseEventTimestamp(dueStr)
			if err != nil {
				return nil, invalid("invalid --due RFC3339 timestamp")
			}
			dueAt = &t
		}
		return callOp("ledger-item-set", core.SetCampaignLedgerItemRequest{
			RequestID: reqID, Repo: repo, Campaign: campaign, Kind: kind, ItemKey: itemKeyFlag,
			Owner: owner, Status: status, Summary: summary, Detail: detailPtr,
			Source: srcRef, Result: resRef, EventID: eventID, DeliveryID: deliveryIDFlag,
			DueAt: dueAt, ExpectedRevision: expectedRevision,
		})
	case "sync-context":
		if f.NArg() != 0 {
			return nil, invalid("ledger sync-context takes no positional arguments")
		}
		return callOp("ledger-sync-context", core.SyncCampaignContextRequest{
			RequestID: reqID, Repo: repo, Campaign: campaign,
		})
	case "publish", "send":
		if opKey == "" || kind == "" {
			return nil, invalid("ledger " + subcommand + " requires --op-key and --kind")
		}
		if kind == "notice" && action == "" && os.Getenv("CAIRN_ALLOW_OPTIONAL_NOTICE_ACTION") != "1" {
			return nil, invalid("notice events require --action (none, review, or decide); also set --supersedes TOPIC for repeatable updates and --summary when body exceeds 280 bytes")
		}
		choices := 0
		var dest core.EventDestination
		var poolReqs *core.PoolRequirements
		if to != "" {
			choices++
			dest = core.EventDestination{Type: "agent", Name: to}
		}
		if topic != "" {
			choices++
			dest = core.EventDestination{Type: "topic", Name: topic}
		}
		if pool != "" {
			choices++
			dest = core.EventDestination{Type: "pool", Name: pool}
			poolReqs = &core.PoolRequirements{Workspace: workspace, Harness: harness, Model: model, Capabilities: capabilities}
		} else if workspace != "" || harness != "" || model != "" || len(capabilities) > 0 {
			return nil, invalid("pool selector flags require --pool")
		}
		if choices != 1 {
			return nil, invalid("choose exactly one of --to, --topic and --pool")
		}
		var expBy, expNextBy *time.Time
		if expectByStr != "" {
			t, err := parseEventTimestamp(expectByStr)
			if err != nil {
				return nil, invalid("invalid --expect-by RFC3339 timestamp")
			}
			expBy = &t
		}
		if expectNextByStr != "" {
			t, err := parseEventTimestamp(expectNextByStr)
			if err != nil {
				return nil, invalid("invalid --expect-next-by RFC3339 timestamp")
			}
			expNextBy = &t
		}
		pub := core.PublishEventRequest{
			Repo: repo, Kind: kind, Destination: dest, Pool: poolReqs,
			CausationID: causationID, CorrelationID: correlationID,
			Summary: summary, Blocker: blocker, Action: action, Supersedes: supersedes,
			ExpectBy: expBy, ExpectNextBy: expNextBy,
		}
		if subcommand == "publish" {
			if f.NArg() != 1 || version <= 0 {
				return nil, invalid("ledger publish requires --version N and one RECORD_UUID positional argument")
			}
			pub.Ref = core.RecordVersionRef{RecordID: strings.TrimPrefix(f.Arg(0), "cairn:"), Version: version}
			return callOp("ledger-publish", core.CampaignPublishRequest{
				Repo: repo, Campaign: campaign, OpKey: opKey, Publication: pub,
				ItemKey: linkKey, ItemKind: linkKind, ItemStatus: linkStatus,
			})
		}
		if f.NArg() != 0 || !stdin {
			return nil, invalid("ledger send requires --stdin and no positional arguments")
		}
		body, err := io.ReadAll(io.LimitReader(input, 65537))
		if err != nil {
			return nil, err
		}
		if len(body) == 0 || len(body) > 65536 || strings.TrimSpace(string(body)) == "" || !utf8.Valid(body) {
			return nil, invalid("ledger send requires 1-65536 bytes of nonblank UTF-8 text on stdin")
		}
		if plain || os.Getenv("CAIRN_CD1_PLAIN") == "1" {
			rendered, rErr := core.PlainProseFromBody(string(body), core.CD1ValidateOptions{
				EnvelopeKind: kind, EnvelopeAction: action,
			})
			if rErr != nil {
				return nil, invalid(rErr.Error())
			}
			if len(rendered) > 65536 {
				return nil, invalid("rendered prose exceeds 65536-byte body limit")
			}
			body = []byte(rendered)
		} else if proto, isCompact := core.DetectCompactProtocol(string(body)); isCompact && os.Getenv("CAIRN_ALLOW_UNVALIDATED_CD1") != "1" {
			if (causationID == "" && correlationID == "") || (proto != core.CD1Proto && proto != core.CairnC1Proto) {
				if res := core.ValidateCD1Message(string(body), core.CD1ValidateOptions{
					EnvelopeKind: kind, EnvelopeAction: action,
				}); !res.Valid {
					return nil, invalid("invalid cd1 body: " + strings.Join(res.Errors, "; "))
				}
			}
		}
		if len(body) > core.MaxEventSummaryBytes && summary == "" {
			return nil, invalid("ledger send requires --summary (1-280 single-line UTF-8 bytes) when body exceeds 280 bytes; state what changed in --summary and keep uncompressed details in body")
		}
		return callOp("ledger-send", core.CampaignSendRequest{
			Repo: repo, Campaign: campaign, OpKey: opKey, Body: string(body),
			SourceKind: sourceKind, Shareable: shareable, Publication: pub,
			ItemKey: linkKey, ItemKind: linkKind, ItemStatus: linkStatus,
		})
	case "complete":
		if f.NArg() != 1 || opKey == "" || !stdin {
			return nil, invalid("ledger complete requires --op-key, --stdin and one DELIVERY_UUID positional argument")
		}
		body, err := io.ReadAll(io.LimitReader(input, 65537))
		if err != nil {
			return nil, err
		}
		if len(body) == 0 || len(body) > 65536 || strings.TrimSpace(string(body)) == "" || !utf8.Valid(body) {
			return nil, invalid("ledger complete requires 1-65536 bytes of nonblank UTF-8 text on stdin")
		}
		return callOp("ledger-complete", core.CampaignCompleteRequest{
			Repo: repo, Campaign: campaign, OpKey: opKey, DeliveryID: f.Arg(0),
			LeaseID: lease, Body: string(body), SourceKind: sourceKind, Shareable: shareable,
			ReplyTo: replyTo, CausationID: causationID, CorrelationID: correlationID,
			Summary: summary, ItemKey: linkKey, ItemStatus: linkStatus,
		})
	case "ack":
		if f.NArg() != 1 || opKey == "" {
			return nil, invalid("ledger ack requires --op-key and one DELIVERY_UUID positional argument")
		}
		return callOp("ledger-ack", core.CampaignAckRequest{
			Repo: repo, Campaign: campaign, OpKey: opKey, DeliveryID: f.Arg(0),
			LeaseID: lease, Disposition: disposition, Code: code,
			ItemKey: linkKey, ItemKind: linkKind, ItemStatus: linkStatus,
		})
	case "handoff-check":
		if f.NArg() != 0 {
			return nil, invalid("ledger handoff-check takes no positional arguments")
		}
		return callOp("ledger-handoff-check", core.CheckCoordinatorHandoffRequest{
			Repo: repo, Campaign: campaign,
		})
	case "compact":
		if f.NArg() != 0 {
			return nil, invalid("ledger compact takes no positional arguments")
		}
		return callOp("ledger-compaction", core.RecordCoordinatorCompactionRequest{
			RequestID: reqID, Repo: repo, Campaign: campaign,
		})
	case "handoff":
		if f.NArg() != 0 {
			return nil, invalid("ledger handoff takes no positional arguments")
		}
		if toSessionFlag != "" && (toAgentID != "" || toExecutionID != "") {
			return nil, invalid("cannot combine --to-session with --to-agent-id or --to-execution-id")
		}
		if toSessionFlag != "" {
			parts := strings.SplitN(toSessionFlag, ":", 2)
			if len(parts) != 2 {
				return nil, invalid("--to-session must be AGENT_UUID:EXECUTION_UUID")
			}
			toAgentID, toExecutionID = parts[0], parts[1]
		}
		var succ *core.AgentSessionRef
		if toAgentID != "" || toExecutionID != "" {
			ref := core.AgentSessionRef{AgentID: toAgentID, ExecutionID: toExecutionID}
			if err := ref.Validate(); err != nil {
				return nil, err
			}
			succ = &ref
		}
		var newSess *core.NewCoordinatorSessionSpec
		if newBinding != "" || newNativeSession != "" {
			if newBinding == "" || newNativeSession == "" {
				return nil, invalid("both --new-binding and --new-native-session are required together")
			}
			newSess = &core.NewCoordinatorSessionSpec{Binding: newBinding, NativeSessionID: newNativeSession}
			if workspace != "" || harness != "" || project != "" || model != "" || deliveryMode != "" {
				newSess.Metadata = &core.AgentMetadata{
					Harness: harness, Model: model, Project: project, Workspace: workspace, State: "idle", DeliveryMode: deliveryMode,
				}
			}
		} else if workspace != "" || harness != "" || project != "" || model != "" || deliveryMode != "" {
			return nil, invalid("metadata flags (--workspace, --harness, --project, --model, --delivery-mode) require --new-binding and --new-native-session")
		}
		if (succ == nil) == (newSess == nil) {
			return nil, invalid("specify either --to-session / (--to-agent-id --to-execution-id) or (--new-binding --new-native-session)")
		}
		return callOp("ledger-handoff", core.HandoffCampaignCoordinatorRequest{
			RequestID: reqID, Repo: repo, Campaign: campaign, OnlyIfThresholdReached: ifNeeded,
			Successor: succ, NewSession: newSess, Shareable: shareable, Reason: reason, Note: note,
		})
	default:
		return nil, invalid("unknown ledger subcommand")
	}
}
