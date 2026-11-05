package main

import (
	"context"
	"flag"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
)

const workHelp = `Cairn work coordination (operator commands)

Subcommands:
  cairn work features status [--feature NAME] [--collection REPO]
  cairn work features enable --feature NAME [--request-id UUID] [--collection REPO]
  cairn work features disable --feature NAME [--request-id UUID] [--collection REPO]
  cairn work features retire --feature NAME [--request-id UUID] [--collection REPO]
`

func workCommand(ctx context.Context, store *core.Store, args []string) (any, error) {
	if len(args) == 0 || args[0] == "--help" || args[0] == "-h" || args[0] == "help" {
		return commandHelp(workHelp), nil
	}
	if args[0] == "features" {
		return workFeaturesCommand(ctx, store, args[1:])
	}
	return nil, invalid("unknown work subcommand: " + args[0] + "; expected features")
}

func workFeaturesCommand(ctx context.Context, store *core.Store, args []string) (any, error) {
	if len(args) == 0 || args[0] == "--help" || args[0] == "-h" || args[0] == "help" {
		return commandHelp(workHelp), nil
	}
	sub := args[0]
	f := flags("work features " + sub)
	feature := f.String("feature", "", "feature name (e.g. coordination-waits/1)")
	requestID := f.String("request-id", "", "stable retry UUID")
	collection := f.String("collection", "", "collection repository")
	repo := f.String("repo", "", "alias for collection")
	if err := f.Parse(args[1:]); err != nil {
		if err == flag.ErrHelp {
			return commandHelp(workHelp), nil
		}
		return nil, invalid(err.Error())
	}
	scopeRepo := *collection
	if scopeRepo == "" {
		scopeRepo = *repo
	}
	_ = scopeRepo

	targetFeature := *feature
	if targetFeature == "" && f.NArg() > 0 {
		targetFeature = f.Arg(0)
	}

	switch sub {
	case "status":
		if targetFeature != "" {
			return store.GetWorkFeatureStatus(ctx, targetFeature)
		}
		list, err := store.ListWorkFeatures(ctx)
		if err != nil {
			return nil, err
		}
		return struct {
			Features []core.WorkFeatureStatus `json:"features"`
		}{Features: list}, nil

	case "enable", "disable", "retire":
		if targetFeature == "" {
			return nil, invalid("--feature NAME is required")
		}
		reqID := *requestID
		if reqID == "" {
			reqID = uuid.NewString()
		}
		targetStatus := "active"
		if sub == "disable" {
			targetStatus = "disabled"
		} else if sub == "retire" {
			targetStatus = "retired"
		}
		return store.SetWorkFeatureStatus(ctx, reqID, targetFeature, targetStatus)

	default:
		return nil, invalid("unknown features subcommand: " + sub + "; expected status, enable, disable, or retire")
	}
}
