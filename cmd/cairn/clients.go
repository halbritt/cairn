package main

import (
	"context"
	"encoding/json"
	"flag"
	"io"
	"os"
	"strconv"

	"github.com/halbritt/cairn/core"
	"github.com/halbritt/cairn/localapi"
)

const clientsHelpDetail = `List the client implementations this API process has observed for your own
principal and machine. Each client declares itself in a transport-only header;
a declaration is a reported claim, never identity. Observations are volatile:
they cover one API process, are kept for 24 hours after the last contact, and
reset when the API restarts (a new observation_epoch). Cohorts group identical
declarations; they are not process counts. A missing or invalid declaration is
shown as such, never as an old client. An empty list means no retained
observations, not that every client is current. Listing does not refresh any
cohort. Output is the bounded, safe JSON view; no header, token, path or prompt
is included. A missing route returns UNSUPPORTED_DIAGNOSTICS. Remote denial may mean access
is denied or the API predates the view; observed clients remain unknown.`

// cliDiagnostics declares this executing CLI process. Origin comes only from
// the dedicated bounded caller value, validated against the fixed component
// list; the CLI's own build is never presented as the caller's identity.
func cliDiagnostics() localapi.ClientDiagnostics {
	d := localapi.ClientDiagnostics{Surface: "cli", Harness: "unknown", RetrievalCapabilities: localapi.CurrentRetrievalCapabilities()}
	if origin, ok := localapi.ParseCallerOrigin(os.Getenv("CAIRN_CALLER_DIAGNOSTICS")); ok {
		d.Origin = origin
	}
	return d
}

// agentClients serves `cairn agent ... clients`: one authenticated, read-only
// call. The request has no principal, machine or "all" selector.
func agentClients(ctx context.Context, client *localapi.Client, args []string) (any, error) {
	f := flags("clients")
	limit := f.Int("limit", 0, "maximum cohorts to return, 1 to 128 (default 50)")
	if err := f.Parse(args); err != nil {
		if err == flag.ErrHelp {
			return agentFlagHelp("cairn agent [--token-file FILE] [--socket PATH] clients [--limit N]", clientsHelpDetail, f), nil
		}
		return nil, invalid(err.Error())
	}
	if f.NArg() != 0 {
		return nil, invalid("clients takes no positional arguments")
	}
	var request localapi.ClientsRequest
	f.Visit(func(option *flag.Flag) {
		if option.Name == "limit" {
			request.Limit = limit
		}
	})
	if request.Limit != nil && (*request.Limit < 1 || *request.Limit > localapi.ClientsMaxLimit) {
		return nil, invalid("--limit must be an integer from 1 to " + strconv.Itoa(localapi.ClientsMaxLimit))
	}
	var result json.RawMessage
	if err := client.Call(ctx, "clients", request, &result); err != nil {
		if core.Code(err) == "NOT_FOUND" {
			return nil, &core.Error{Code: "UNSUPPORTED_DIAGNOSTICS", Message: "this API does not provide client observations; observed clients are unknown, not an empty list"}
		}
		if core.Code(err) == "AUTHORITY_DENIED" {
			return nil, &core.Error{Code: "AUTHORITY_DENIED", Message: "client observations unavailable: access denied or this API predates the view; observed clients are unknown"}
		}
		return nil, err
	}
	pageLimit := localapi.ClientsDefaultLimit
	if request.Limit != nil {
		pageLimit = *request.Limit
	}
	view, err := localapi.ParseClientsResponse(result, pageLimit)
	if err != nil {
		return nil, err
	}
	return view, nil
}

// clientsCommand is the top-level `cairn clients`. It only translates the
// profile connection flags and delegates to the agent operation, so connection
// defaults, enrolled machines and error masking stay in one place.
func clientsCommand(ctx context.Context, args []string, input io.Reader) (any, error) {
	f := flags("clients")
	token := f.String("token-file", "", "owner-only API token file")
	profile := f.String("profile", "", "provisioned profile name")
	socket := f.String("socket", "", "Cairn Unix socket")
	limit := f.Int("limit", 0, "maximum cohorts to return, 1 to 128 (default 50)")
	if err := f.Parse(args); err != nil {
		if err == flag.ErrHelp {
			return agentFlagHelp("cairn clients [--profile NAME | --token-file FILE] [--socket PATH] [--limit N]", clientsHelpDetail, f), nil
		}
		return nil, invalid(err.Error())
	}
	if f.NArg() != 0 {
		return nil, invalid("clients takes no positional arguments")
	}
	var forwarded []string
	provided := map[string]bool{}
	f.Visit(func(option *flag.Flag) { provided[option.Name] = true })
	if provided["profile"] || provided["token-file"] {
		path, err := agentProfileToken(*profile, *token)
		if err != nil {
			return nil, err
		}
		forwarded = append(forwarded, "--token-file", path)
	}
	if provided["socket"] {
		forwarded = append(forwarded, "--socket", *socket)
	}
	forwarded = append(forwarded, "clients")
	if provided["limit"] {
		forwarded = append(forwarded, "--limit", strconv.Itoa(*limit))
	}
	return agentRequest(ctx, forwarded, input)
}
