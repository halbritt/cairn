package main

import (
	"context"
	"encoding/json"
	"flag"
	"io"
	"path/filepath"
	"regexp"
	"strings"

	"github.com/halbritt/cairn/core"
	"github.com/halbritt/cairn/localapi"
)

const eventHelp = `Cairn agent events (authenticated API; no operator database access)

Common flags: --profile NAME | --token-file FILE, --socket PATH, --repo COLLECTION
--profile selects ~/.local/share/cairn/event-profiles/NAME.token (or CAIRN_HOME).
Inbox identity is the authenticated principal. --agent only asserts that identity.
Provide a stable --request-id UUID for publish, subscribe, unsubscribe, ack and complete.
Retry an uncertain mutation with identical arguments and the same request UUID.

  publish --request-id UUID (--to PRINCIPAL | --topic TOPIC) --kind KIND --version N RECORD_UUID
    [--causation-id UUID] [--correlation-id UUID]
  inbox next [--agent PRINCIPAL] [--lease-seconds N]
  ack --request-id UUID --lease UUID [--disposition handled|ignored|failed] [--code CODE] DELIVERY_UUID
  complete --request-id UUID --lease UUID --stdin [--shareable] [--kind note] DELIVERY_UUID
  retry --lease UUID DELIVERY_UUID
  renew --lease UUID [--lease-seconds N] DELIVERY_UUID
  subscribe --request-id UUID --topic TOPIC
  unsubscribe --request-id UUID --topic TOPIC
  subscriptions [--topic AFTER_TOPIC]
  events [--after POSITION] [--topic TOPIC] [--limit N]
  event-status [--after-delivery UUID] [--limit N] EVENT_UUID
  event-stats

Put flags before positional arguments. Source references accept UUID or cairn:UUID.
Read the exact source with agent history using the returned ref.record_id and ref.version.
complete creates one ordinary result and records handling atomically. Other external
effects need their own idempotency. ack reports handling, not verified task success.
An empty inbox returns delivery:null. Expired leases refuse with STALE_LEASE.
Ignored/failed dispositions require unsupported_kind, source_unavailable or processing_failed.
`

func isEventCommand(command string) bool {
	switch command {
	case "publish", "inbox", "ack", "complete", "retry", "renew", "subscribe", "unsubscribe", "subscriptions", "events", "event-status", "event-stats":
		return true
	}
	return false
}

// eventCommand also serves top-level aliases, always through a scoped token.
func eventCommand(ctx context.Context, command string, args []string, input io.Reader, client *localapi.Client) (any, error) {
	if command == "inbox" {
		if len(args) == 0 {
			return nil, invalid("inbox requires next")
		}
		if args[0] == "--help" || args[0] == "-h" {
			return commandHelp(eventHelp), nil
		}
		if args[0] != "next" {
			return nil, invalid("inbox requires next")
		}
		args = args[1:]
	}
	f := flags(command)
	token := f.String("token-file", "", "API token file")
	profile := f.String("profile", "", "provisioned event profile name")
	socket := f.String("socket", "", "API socket")
	repo := f.String("repo", "", "collection repository, defaults to profile")
	agent := f.String("agent", "", "assert authenticated inbox owner")
	request := f.String("request-id", "", "stable retry UUID")
	to := f.String("to", "", "recipient principal")
	topic := f.String("topic", "", "topic (exclusive cursor for subscriptions)")
	kind := f.String("kind", "", "event or result kind")
	version := f.Int("version", 0, "source version")
	causation := f.String("causation-id", "", "source event UUID")
	correlation := f.String("correlation-id", "", "interaction UUID")
	lease := f.String("lease", "", "current lease UUID")
	seconds := f.Int("lease-seconds", 0, "lease duration, default 300")
	disposition := f.String("disposition", "handled", "reported terminal state")
	code := f.String("code", "", "terminal failure code")
	after := f.Int64("after", 0, "exclusive event position")
	afterDelivery := f.String("after-delivery", "", "exclusive delivery UUID")
	limit := f.Int("limit", 0, "maximum page size")
	stdin := f.Bool("stdin", false, "read selected result from stdin")
	shareable := f.Bool("shareable", false, "allow hosted result reads")
	if err := f.Parse(args); err != nil {
		if err == flag.ErrHelp {
			return commandHelp(eventHelp), nil
		}
		return nil, invalid(err.Error())
	}
	allowed := " token-file socket profile "
	switch command {
	case "publish":
		allowed += "repo request-id to topic kind version causation-id correlation-id "
	case "inbox":
		allowed += "repo agent lease-seconds "
	case "ack":
		allowed += "request-id lease disposition code "
	case "complete":
		allowed += "repo request-id lease stdin shareable kind "
	case "retry":
		allowed += "lease "
	case "renew":
		allowed += "lease lease-seconds "
	case "subscribe", "unsubscribe":
		allowed += "repo agent request-id topic "
	case "subscriptions":
		allowed += "repo agent topic limit "
	case "events":
		allowed += "repo agent after topic limit "
	case "event-status":
		allowed += "after-delivery limit "
	case "event-stats":
		allowed += "repo agent "
	}
	var unsupported string
	f.Visit(func(option *flag.Flag) {
		if !strings.Contains(allowed, " "+option.Name+" ") {
			unsupported = option.Name
		}
	})
	if unsupported != "" {
		return nil, invalid("--" + unsupported + " is not supported by " + command)
	}
	if *profile != "" {
		if client != nil || *token != "" || !regexp.MustCompile(`^[a-z][a-z0-9-]{0,63}$`).MatchString(*profile) {
			return nil, invalid("--profile requires a top-level command and cannot combine with --token-file")
		}
		directory, err := dataDirectory()
		if err != nil {
			return nil, err
		}
		*token = filepath.Join(directory, "event-profiles", *profile+".token")
	}
	if client != nil && (*token != "" || *socket != "") {
		return nil, invalid("agent connection flags must precede the operation")
	}
	var req any
	operation := ""
	expect := 0
	switch command {
	case "publish", "ack", "complete", "retry", "renew", "event-status":
		expect = 1
	}
	if f.NArg() != expect {
		return nil, invalid("unexpected positional arguments; use --help")
	}
	if command == "publish" || command == "subscribe" || command == "unsubscribe" || command == "ack" || command == "complete" {
		if *request == "" {
			return nil, invalid("--request-id UUID is required for retryable mutations")
		}
	}
	switch command {
	case "publish":
		if (*to == "") == (*topic == "") {
			return nil, invalid("choose exactly one of --to and --topic")
		}
		dest := core.EventDestination{Type: "agent", Name: *to}
		if *topic != "" {
			dest = core.EventDestination{Type: "topic", Name: *topic}
		}
		operation = "event-publish"
		req = core.PublishEventRequest{RequestID: *request, Repo: *repo, Kind: *kind, Ref: core.RecordVersionRef{RecordID: strings.TrimPrefix(f.Arg(0), "cairn:"), Version: *version}, Destination: dest, CausationID: *causation, CorrelationID: *correlation}
	case "inbox":
		operation = "event-next"
		req = core.NextEventRequest{Repo: *repo, Agent: *agent, LeaseSeconds: *seconds}
	case "subscribe", "unsubscribe":
		operation = "event-subscribe"
		req = core.SubscriptionRequest{RequestID: *request, Repo: *repo, Agent: *agent, Topic: *topic, Active: command == "subscribe"}
	case "subscriptions", "events", "event-stats":
		operation = "event-list"
		if command == "event-stats" {
			operation = "event-metrics"
		}
		if command == "subscriptions" {
			operation = "event-subscriptions"
		}
		req = core.EventQuery{Repo: *repo, Agent: *agent, Topic: *topic, After: *after, Limit: *limit}
	case "event-status":
		operation = "event-inspect"
		req = core.EventStatusRequest{EventID: f.Arg(0), After: *afterDelivery, Limit: *limit}
	case "retry", "renew":
		operation = "event-" + command
		req = core.EventLeaseRequest{DeliveryID: f.Arg(0), LeaseID: *lease, LeaseSeconds: *seconds}
	case "ack", "complete":
		operation = "event-complete"
		complete := core.CompleteEventRequest{RequestID: *request, DeliveryID: f.Arg(0), LeaseID: *lease, Disposition: *disposition, Code: *code}
		if command == "complete" {
			if !*stdin {
				return nil, invalid("complete requires --stdin with selected result text")
			}
			body, err := io.ReadAll(io.LimitReader(input, 65537))
			if err != nil {
				return nil, err
			}
			if len(body) > 65536 {
				return nil, invalid("result exceeds 64 KiB")
			}
			sensitivity := "local"
			if *shareable {
				sensitivity = "shareable"
			}
			resultKind := *kind
			if resultKind == "" {
				resultKind = "note"
			}
			complete.Draft = &core.Draft{Body: string(body), Kind: resultKind, ClaimType: "self", Sensitivity: sensitivity, Scope: core.Scope{Repo: *repo, TaskID: "*", RunID: "*"}}
		}
		req = complete
	}
	if client == nil {
		connectionArgs := []string{}
		if *token != "" {
			connectionArgs = append(connectionArgs, "--token-file", *token)
		}
		if *socket != "" {
			connectionArgs = append(connectionArgs, "--socket", *socket)
		}
		// Raw operation dispatch uses the same default paths and client diagnostics.
		encoded, err := json.Marshal(req)
		if err != nil {
			return nil, err
		}
		return agentRequest(ctx, append(connectionArgs, operation), strings.NewReader(string(encoded)))
	}
	var result json.RawMessage
	if err := client.Call(ctx, operation, req, &result); err != nil {
		return nil, err
	}
	return result, nil
}
