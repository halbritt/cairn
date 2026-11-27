package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"os"
	"strings"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
	"github.com/halbritt/cairn/internal/jsontext"
	"github.com/halbritt/cairn/localapi"
)

const teamHelp = `Cairn team hierarchy configuration (authenticated API or --operator)

Subcommands:
  cairn team set-campaign --campaign NAME [--root-agent PRINCIPAL] [--description TEXT | --description-file PATH] [--out-of-chain-policy allow|warn|reroute] [--max-coordinator-age-seconds N] [--max-coordinator-compactions N] [--no-coordinator-limits] [--active | --inactive] [--expected-revision N] [--local | --shareable] [--request-id UUID]
  cairn team set-member --campaign NAME --member PRINCIPAL [--lead PRINCIPAL] [--role lead|worker] [--role-label TEXT] [--active | --inactive] [--request-id UUID]
  cairn team get --campaign NAME
  cairn team list [--active-only] [--after CAMPAIGN] [--limit N]

Defaults on campaign creation: max_coordinator_age_seconds=14400, max_coordinator_compactions=3 (pass 0 or --no-coordinator-limits to opt out).
For a swarm, record the owner's prose (up to 8192 bytes) with --description or --description-file (- for stdin).
Give each member a free-text --role-label (up to 160 bytes on one line). Use only --role-label to edit the root's member metadata.
Omit either field on update to keep its value. Pass an empty value to clear it.
Common flags: --profile NAME | --token-file FILE | --operator, --socket PATH, --repo COLLECTION, --agent-id UUID, --execution-id UUID
`

var teamSubcommandAllowedFlags = map[string]map[string]bool{
	"set-campaign": {
		"campaign": true, "root-agent": true, "out-of-chain-policy": true,
		"description": true, "description-file": true,
		"max-coordinator-age-seconds": true, "max-coordinator-compactions": true,
		"no-coordinator-limits": true,
		"active":                true, "inactive": true, "expected-revision": true,
		"local": true, "shareable": true, "request-id": true,
	},
	"set-member": {
		"campaign": true, "member": true, "lead": true, "role": true,
		"role-label": true,
		"active":     true, "inactive": true, "request-id": true,
	},
	"get": {
		"campaign": true,
	},
	"list": {
		"active-only": true, "after": true, "limit": true,
	},
}

func teamCommand(ctx context.Context, args []string, input io.Reader, client *localapi.Client, callerSession *core.AgentSessionRef) (any, error) {
	if len(args) == 0 || args[0] == "--help" || args[0] == "-h" || args[0] == "help" {
		return commandHelp(teamHelp), nil
	}
	subcommand := args[0]
	allowedSub, validSub := teamSubcommandAllowedFlags[subcommand]
	if !validSub {
		return nil, invalid("unknown team subcommand; expected set-campaign, set-member, get, or list")
	}
	f := flags("team " + subcommand)
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
	campaign := f.String("campaign", "", "campaign name (1..64 ASCII letters, digits or _.-)")
	description := f.String("description", "", "owner's campaign description")
	descriptionFile := f.String("description-file", "", "campaign description file (- for stdin)")
	rootAgent := f.String("root-agent", "", "campaign root coordinator principal (e.g. agent/UUID)")
	outPolicy := f.String("out-of-chain-policy", "", "out-of-chain direct publish policy (allow|warn|reroute)")
	member := f.String("member", "", "team member principal (e.g. agent/UUID)")
	lead := f.String("lead", "", "immediate lead principal in campaign (e.g. agent/UUID)")
	role := f.String("role", "", "member role (lead|worker)")
	roleLabel := f.String("role-label", "", "free-text role label")
	activeFlag := f.Bool("active", false, "mark campaign or member active")
	inactiveFlag := f.Bool("inactive", false, "mark campaign or member inactive")
	expectedRev := f.Int64("expected-revision", 0, "expected current revision")
	localOnly := f.Bool("local", false, "set sensitivity to local")
	shareableFlag := f.Bool("shareable", false, "set sensitivity to shareable")
	maxCoordAge := f.Int("max-coordinator-age-seconds", 0, "coordinator session age threshold in seconds (defaults to 14400 on create; 0 clears)")
	maxCoordComp := f.Int("max-coordinator-compactions", 0, "coordinator session compaction threshold (defaults to 3 on create; 0 clears)")
	noCoordLimits := f.Bool("no-coordinator-limits", false, "disable default coordinator age and compaction handoff limits")
	activeOnly := f.Bool("active-only", false, "list only active campaigns")
	after := f.String("after", "", "exclusive campaign cursor for pagination")
	limit := f.Int("limit", 0, "page size")

	if err := f.Parse(args[1:]); err != nil {
		if err == flag.ErrHelp {
			return commandHelp(teamHelp), nil
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
			return nil, invalid(fmt.Sprintf("--%s is not valid for team %s", name, subcommand))
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
		return nil, invalid("agent connection flags must precede team")
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
	if *activeFlag && *inactiveFlag {
		return nil, invalid("cannot combine --active and --inactive")
	}
	var activePtr *bool
	if *activeFlag {
		v := true
		activePtr = &v
	} else if *inactiveFlag {
		v := false
		activePtr = &v
	}
	sens := ""
	if *localOnly {
		sens = "local"
	} else if *shareableFlag {
		sens = "shareable"
	}
	var maxAgePtr, maxCompPtr *int
	if *noCoordLimits {
		if provided["max-coordinator-age-seconds"] || provided["max-coordinator-compactions"] {
			return nil, invalid("cannot combine --no-coordinator-limits with --max-coordinator-age-seconds or --max-coordinator-compactions")
		}
		zero := 0
		maxAgePtr = &zero
		maxCompPtr = &zero
	} else {
		if provided["max-coordinator-age-seconds"] {
			maxAgePtr = maxCoordAge
		}
		if provided["max-coordinator-compactions"] {
			maxCompPtr = maxCoordComp
		}
	}
	var descriptionPtr, roleLabelPtr *string
	switch {
	case provided["description"] && provided["description-file"]:
		return nil, invalid("cannot combine --description and --description-file")
	case provided["description"]:
		descriptionPtr = description
	case provided["description-file"]:
		value, err := readTeamDescription(*descriptionFile, input)
		if err != nil {
			return nil, err
		}
		descriptionPtr = &value
	}
	if provided["role-label"] {
		roleLabelPtr = roleLabel
	}
	return dispatchTeamSubcommand(ctx, subcommand, scopeRepo, *requestID, *campaign, *rootAgent, *outPolicy, *member, *lead, *role, descriptionPtr, roleLabelPtr, activePtr, maxAgePtr, maxCompPtr, *expectedRev, sens, *activeOnly, *after, *limit, *token, *socket, *operatorMode, client, sessionRef)
}

func readTeamDescription(path string, input io.Reader) (string, error) {
	if path == "" {
		return "", invalid("empty --description-file path")
	}
	if path != "-" {
		file, err := os.Open(path)
		if err != nil {
			return "", invalid(fmt.Sprintf("open --description-file: %v", err))
		}
		defer file.Close()
		input = file
	}
	body, err := io.ReadAll(io.LimitReader(input, core.MaxCampaignDescriptionBytes+1))
	if err != nil {
		return "", invalid(fmt.Sprintf("read --description-file: %v", err))
	}
	if len(body) > core.MaxCampaignDescriptionBytes {
		return "", invalid("description must be at most 8192 bytes")
	}
	return string(body), nil
}

func dispatchTeamSubcommand(
	ctx context.Context, subcommand, scopeRepo, requestID, campaign, rootAgent, outPolicy, member, lead, role string,
	description, roleLabel *string, activePtr *bool, maxAgePtr, maxCompPtr *int, expectedRev int64, sens string, activeOnly bool, after string, limit int,
	token, socket string, operatorMode bool, client *localapi.Client, sessionRef *core.AgentSessionRef,
) (any, error) {
	var operation string
	var req any

	switch subcommand {
	case "set-campaign":
		if strings.TrimSpace(campaign) == "" {
			return nil, invalid("--campaign is required for team set-campaign")
		}
		if requestID == "" {
			requestID = uuid.NewString()
		} else if uuid.Validate(requestID) != nil {
			return nil, invalid("--request-id must be a UUID")
		}
		operation = "team-campaign-set"
		req = core.SetTeamCampaignRequest{
			RequestID: requestID, Repo: scopeRepo, Campaign: campaign, Description: description,
			RootAgent: rootAgent, OutOfChainPolicy: outPolicy,
			Sensitivity: sens, MaxCoordinatorAgeSeconds: maxAgePtr, MaxCoordinatorCompactions: maxCompPtr,
			Active: activePtr, ExpectedRevision: expectedRev,
		}
	case "set-member":
		if strings.TrimSpace(campaign) == "" {
			return nil, invalid("--campaign is required for team set-member")
		}
		if strings.TrimSpace(member) == "" {
			return nil, invalid("--member is required for team set-member")
		}
		if requestID == "" {
			requestID = uuid.NewString()
		} else if uuid.Validate(requestID) != nil {
			return nil, invalid("--request-id must be a UUID")
		}
		operation = "team-member-set"
		req = core.SetTeamMemberRequest{
			RequestID: requestID, Repo: scopeRepo, Campaign: campaign,
			Member: member, Lead: lead, Role: role, RoleLabel: roleLabel, Active: activePtr,
		}
	case "get":
		if strings.TrimSpace(campaign) == "" {
			return nil, invalid("--campaign is required for team get")
		}
		operation = "team-campaign-get"
		req = core.GetTeamCampaignRequest{Repo: scopeRepo, Campaign: campaign}
	case "list":
		operation = "team-campaign-list"
		req = core.ListTeamCampaignsRequest{Repo: scopeRepo, ActiveOnly: activeOnly, After: after, Limit: limit}
	}

	if err := jsontext.CheckValue(req); err != nil {
		return nil, invalid(err.Error())
	}
	encoded, err := json.Marshal(req)
	if err != nil {
		return nil, err
	}
	if operatorMode {
		return run(ctx, []string{operation}, strings.NewReader(string(encoded)))
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
		return agentRequest(ctx, append(connArgs, operation), strings.NewReader(string(encoded)))
	}
	if sessionRef != nil {
		client = client.ForAgentSession(*sessionRef)
	}
	if err := client.Call(ctx, operation, req, &raw); err != nil {
		return nil, err
	}
	return raw, nil
}
