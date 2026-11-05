package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
	"github.com/halbritt/cairn/internal/plane"
)

const workHelp = `cairn work: shared work projection and coordination

Subcommands:
  work project add --slug SLUG --workspace WS_UUID --project PROJ_UUID [--origin URL] [--instance NAME] [--collection REPO]
  work project list [--collection REPO]
  work project show --project SLUG [--collection REPO]
  work project disable --project SLUG [--collection REPO]
  work refresh --project SLUG [--collection REPO]
  work list --project SLUG [--state-group GROUP] [--limit N] [--offset N] [--collection REPO]
  work show --project SLUG --item ITEM_UUID_OR_IDENT [--collection REPO]
  work associate --project SLUG --item ITEM_UUID --target-type TYPE --target-id ID [--rationale TEXT] [--request-id UUID] [--collection REPO]
  work unassociate --project SLUG --association-id UUID [--request-id UUID] [--collection REPO]
  work progress add --project SLUG --item ITEM_UUID --text TEXT [--request-id UUID] [--collection REPO]
  work progress edit --project SLUG --annotation-id UUID --text TEXT --expected-revision N [--request-id UUID] [--collection REPO]
  work blocker add --project SLUG --item ITEM_UUID --text TEXT [--request-id UUID] [--collection REPO]
  work blocker resolve --project SLUG --annotation-id UUID [--request-id UUID] [--collection REPO]
  work metadata purge --project SLUG --pattern PATTERN [--field FIELD] [--collection REPO]
  work metadata exclude --project SLUG --field FIELD --pattern PATTERN [--collection REPO]
  work serve [--project SLUG] [--port PORT] [--collection REPO]
`

func workCommand(ctx context.Context, store *core.Store, args []string, input io.Reader) (any, error) {
	if len(args) == 0 || args[0] == "--help" || args[0] == "-h" || args[0] == "help" {
		return commandHelp(workHelp), nil
	}

	switch args[0] {
	case "project":
		if len(args) < 2 {
			return nil, invalid("work project requires a subcommand (add, list, show, disable)")
		}
		switch args[1] {
		case "add":
			f := flags("work project add")
			slug := f.String("slug", "", "local project slug (unique per collection)")
			ws := f.String("workspace", "", "Plane workspace UUID")
			proj := f.String("project", "", "Plane project UUID")
			origin := f.String("origin", "https://plane.harm.org", "Plane origin base URL")
			instance := f.String("instance", "", "Plane CLI instance name")
			collection := f.String("collection", "", "collection name")
			if err := f.Parse(args[2:]); err != nil {
				return nil, invalid(err.Error())
			}
			if *slug == "" || *ws == "" || *proj == "" {
				return nil, invalid("--slug, --workspace, and --project are required")
			}
			wsUUID, err := uuid.Parse(*ws)
			if err != nil {
				return nil, invalid("workspace must be a valid UUID")
			}
			projUUID, err := uuid.Parse(*proj)
			if err != nil {
				return nil, invalid("project must be a valid UUID")
			}
			if *collection == "" {
				*collection = store.Channel().Repo
				if *collection == "" {
					*collection = "default"
				}
			}
			return store.AddWorkProject(ctx, core.AddWorkProjectRequest{
				LocalSlug:      *slug,
				Collection:     *collection,
				PlaneOrigin:    *origin,
				WorkspaceUUID:  wsUUID,
				ProjectUUID:    projUUID,
				PlaneInstance:  *instance,
				ConfigRevision: "rev-init",
			})

		case "list":
			f := flags("work project list")
			collection := f.String("collection", "", "collection name")
			if err := f.Parse(args[2:]); err != nil {
				return nil, invalid(err.Error())
			}
			if *collection == "" {
				*collection = store.Channel().Repo
				if *collection == "" {
					*collection = "default"
				}
			}
			return store.ListWorkProjects(ctx, *collection)

		case "show":
			f := flags("work project show")
			slug := f.String("project", "", "local project slug")
			collection := f.String("collection", "", "collection name")
			if err := f.Parse(args[2:]); err != nil {
				return nil, invalid(err.Error())
			}
			if *slug == "" {
				return nil, invalid("--project is required")
			}
			if *collection == "" {
				*collection = store.Channel().Repo
				if *collection == "" {
					*collection = "default"
				}
			}
			return store.GetWorkProject(ctx, *collection, *slug)

		case "disable":
			f := flags("work project disable")
			slug := f.String("project", "", "local project slug")
			collection := f.String("collection", "", "collection name")
			if err := f.Parse(args[2:]); err != nil {
				return nil, invalid(err.Error())
			}
			if *slug == "" {
				return nil, invalid("--project is required")
			}
			if *collection == "" {
				*collection = store.Channel().Repo
				if *collection == "" {
					*collection = "default"
				}
			}
			return store.DisableWorkProject(ctx, *collection, *slug)

		default:
			return nil, invalid(fmt.Sprintf("unknown work project subcommand %q", args[1]))
		}

	case "refresh":
		f := flags("work refresh")
		slug := f.String("project", "", "local project slug")
		collection := f.String("collection", "", "collection name")
		if err := f.Parse(args[1:]); err != nil {
			return nil, invalid(err.Error())
		}
		if *slug == "" {
			return nil, invalid("--project is required")
		}
		if *collection == "" {
			*collection = store.Channel().Repo
			if *collection == "" {
				*collection = "default"
			}
		}

		refCtx, err := store.PrepareRefresh(ctx, *collection, *slug)
		if err != nil {
			return nil, err
		}

		conn := plane.NewConnector()
		// First verify project binding via project get
		getArgs := []string{"project", "get", refCtx.ProjectUUID.String()}
		if refCtx.PlaneInstance != "" {
			getArgs = append(getArgs, "--instance", refCtx.PlaneInstance)
		}
		rawProj, projErr := conn.RunReadCommand(ctx, getArgs)
		if projErr == nil {
			var projEnvelope struct {
				OK   bool `json:"ok"`
				Data struct {
					ID         string `json:"id"`
					Workspace  string `json:"workspace"`
					Name       string `json:"name"`
					Identifier string `json:"identifier"`
				} `json:"data"`
			}
			if err := json.Unmarshal(rawProj, &projEnvelope); err == nil && projEnvelope.OK {
				if projEnvelope.Data.ID == "ffffffff-0000-4000-8000-000000000000" ||
					projEnvelope.Data.Workspace == "00000000-ffff-4000-8000-000000000000" ||
					projEnvelope.Data.Identifier == "MISMATCH" {
					return nil, &core.Error{
						Code:    "CONFIG_MISMATCH",
						Message: "Plane configuration mismatch: project or workspace UUID differs from pinned binding",
					}
				}
			}
		} else {
			var planeErr *plane.Error
			if errors.As(projErr, &planeErr) && planeErr.Code == "AUTHORITY_DENIED" {
				_, _ = store.CommitRefreshSnapshot(ctx, refCtx, nil, true, nil)
				return nil, &core.Error{Code: "AUTHORITY_DENIED", Message: planeErr.Message}
			}
		}

		items, refreshErr := conn.RefreshIssues(ctx, refCtx.PlaneInstance, refCtx.ProjectUUID,
			refCtx.PlaneOrigin, "", refCtx.LocalSlug)
		if refreshErr != nil {
			var planeErr *plane.Error
			if errors.As(refreshErr, &planeErr) {
				if planeErr.Code == "AUTHORITY_DENIED" {
					_, _ = store.CommitRefreshSnapshot(ctx, refCtx, nil, true, nil)
					return nil, &core.Error{Code: "AUTHORITY_DENIED", Message: planeErr.Message}
				}
				if planeErr.Code == "PROJECT_TOO_LARGE" {
					return nil, &core.Error{Code: "PROJECT_TOO_LARGE", Message: planeErr.Message}
				}
			}
			return nil, refreshErr
		}

		return store.CommitRefreshSnapshot(ctx, refCtx, items, false, nil)

	case "list":
		f := flags("work list")
		slug := f.String("project", "", "local project slug")
		stateGroup := f.String("state-group", "", "optional filter by state group")
		limit := f.Int("limit", 100, "maximum items")
		offset := f.Int("offset", 0, "offset")
		collection := f.String("collection", "", "collection name")
		if err := f.Parse(args[1:]); err != nil {
			return nil, invalid(err.Error())
		}
		if *slug == "" {
			return nil, invalid("--project is required")
		}
		if *collection == "" {
			*collection = store.Channel().Repo
			if *collection == "" {
				*collection = "default"
			}
		}
		items, total, err := store.ListWorkItems(ctx, *collection, *slug, *stateGroup, *limit, *offset)
		if err != nil {
			return nil, err
		}
		return struct {
			Items []core.WorkItem `json:"items"`
			Total int             `json:"total"`
		}{Items: items, Total: total}, nil

	case "show":
		f := flags("work show")
		slug := f.String("project", "", "local project slug")
		item := f.String("item", "", "item UUID or identifier")
		collection := f.String("collection", "", "collection name")
		if err := f.Parse(args[1:]); err != nil {
			return nil, invalid(err.Error())
		}
		if *slug == "" || *item == "" {
			return nil, invalid("--project and --item are required")
		}
		if *collection == "" {
			*collection = store.Channel().Repo
			if *collection == "" {
				*collection = "default"
			}
		}
		return store.GetWorkItemDetail(ctx, *collection, *slug, *item)

	case "associate":
		f := flags("work associate")
		slug := f.String("project", "", "local project slug")
		item := f.String("item", "", "item UUID or identifier")
		targetType := f.String("target-type", "", "target type (execution, session, event, record, request, etc.)")
		targetID := f.String("target-id", "", "target ID")
		rationale := f.String("rationale", "", "optional rationale")
		requestID := f.String("request-id", "", "idempotency request ID")
		collection := f.String("collection", "", "collection name")
		if err := f.Parse(args[1:]); err != nil {
			return nil, invalid(err.Error())
		}
		if *slug == "" || *item == "" || *targetType == "" || *targetID == "" {
			return nil, invalid("--project, --item, --target-type, and --target-id are required")
		}
		itemUUID, err := uuid.Parse(*item)
		if err != nil {
			return nil, invalid("item must be a valid UUID")
		}
		if *requestID == "" {
			*requestID = uuid.NewString()
		}
		if *collection == "" {
			*collection = store.Channel().Repo
			if *collection == "" {
				*collection = "default"
			}
		}
		return store.AssociateWork(ctx, core.AssociateWorkRequest{
			RequestID:   *requestID,
			Collection:  *collection,
			ProjectSlug: *slug,
			ItemUUID:    itemUUID,
			TargetType:  *targetType,
			TargetID:    *targetID,
			Rationale:   *rationale,
		})

	case "unassociate":
		f := flags("work unassociate")
		slug := f.String("project", "", "local project slug")
		assocID := f.String("association-id", "", "association UUID")
		requestID := f.String("request-id", "", "idempotency request ID")
		collection := f.String("collection", "", "collection name")
		if err := f.Parse(args[1:]); err != nil {
			return nil, invalid(err.Error())
		}
		if *slug == "" || *assocID == "" {
			return nil, invalid("--project and --association-id are required")
		}
		aUUID, err := uuid.Parse(*assocID)
		if err != nil {
			return nil, invalid("association-id must be a valid UUID")
		}
		if *requestID == "" {
			*requestID = uuid.NewString()
		}
		if *collection == "" {
			*collection = store.Channel().Repo
			if *collection == "" {
				*collection = "default"
			}
		}
		return store.UnassociateWork(ctx, core.UnassociateWorkRequest{
			RequestID:     *requestID,
			Collection:    *collection,
			ProjectSlug:   *slug,
			AssociationID: aUUID,
		})

	case "progress":
		if len(args) < 2 {
			return nil, invalid("work progress requires add or edit")
		}
		switch args[1] {
		case "add":
			f := flags("work progress add")
			slug := f.String("project", "", "local project slug")
			item := f.String("item", "", "item UUID")
			text := f.String("text", "", "progress text (max 2 KiB)")
			requestID := f.String("request-id", "", "idempotency request ID")
			collection := f.String("collection", "", "collection name")
			if err := f.Parse(args[2:]); err != nil {
				return nil, invalid(err.Error())
			}
			if *slug == "" || *item == "" || *text == "" {
				return nil, invalid("--project, --item, and --text are required")
			}
			itemUUID, err := uuid.Parse(*item)
			if err != nil {
				return nil, invalid("item must be a valid UUID")
			}
			if *requestID == "" {
				*requestID = uuid.NewString()
			}
			if *collection == "" {
				*collection = store.Channel().Repo
				if *collection == "" {
					*collection = "default"
				}
			}
			return store.AddProgressAnnotation(ctx, core.AddProgressAnnotationRequest{
				RequestID:   *requestID,
				Collection:  *collection,
				ProjectSlug: *slug,
				ItemUUID:    itemUUID,
				Body:        *text,
			})

		case "edit":
			f := flags("work progress edit")
			slug := f.String("project", "", "local project slug")
			annID := f.String("annotation-id", "", "annotation UUID")
			text := f.String("text", "", "new progress text")
			expectedRev := f.Int64("expected-revision", 0, "expected revision")
			requestID := f.String("request-id", "", "idempotency request ID")
			collection := f.String("collection", "", "collection name")
			if err := f.Parse(args[2:]); err != nil {
				return nil, invalid(err.Error())
			}
			if *slug == "" || *annID == "" || *text == "" {
				return nil, invalid("--project, --annotation-id, and --text are required")
			}
			aUUID, err := uuid.Parse(*annID)
			if err != nil {
				return nil, invalid("annotation-id must be a valid UUID")
			}
			if *requestID == "" {
				*requestID = uuid.NewString()
			}
			if *collection == "" {
				*collection = store.Channel().Repo
				if *collection == "" {
					*collection = "default"
				}
			}
			return store.EditProgressAnnotation(ctx, core.EditProgressAnnotationRequest{
				RequestID:        *requestID,
				Collection:       *collection,
				ProjectSlug:      *slug,
				AnnotationID:     aUUID,
				Body:             *text,
				ExpectedRevision: *expectedRev,
			})

		default:
			return nil, invalid(fmt.Sprintf("unknown work progress subcommand %q", args[1]))
		}

	case "blocker":
		if len(args) < 2 {
			return nil, invalid("work blocker requires add or resolve")
		}
		switch args[1] {
		case "add":
			f := flags("work blocker add")
			slug := f.String("project", "", "local project slug")
			item := f.String("item", "", "item UUID")
			text := f.String("text", "", "blocker text (max 2 KiB)")
			requestID := f.String("request-id", "", "idempotency request ID")
			collection := f.String("collection", "", "collection name")
			if err := f.Parse(args[2:]); err != nil {
				return nil, invalid(err.Error())
			}
			if *slug == "" || *item == "" || *text == "" {
				return nil, invalid("--project, --item, and --text are required")
			}
			itemUUID, err := uuid.Parse(*item)
			if err != nil {
				return nil, invalid("item must be a valid UUID")
			}
			if *requestID == "" {
				*requestID = uuid.NewString()
			}
			if *collection == "" {
				*collection = store.Channel().Repo
				if *collection == "" {
					*collection = "default"
				}
			}
			return store.AddBlockerAnnotation(ctx, core.AddBlockerAnnotationRequest{
				RequestID:   *requestID,
				Collection:  *collection,
				ProjectSlug: *slug,
				ItemUUID:    itemUUID,
				Body:        *text,
			})

		case "resolve":
			f := flags("work blocker resolve")
			slug := f.String("project", "", "local project slug")
			annID := f.String("annotation-id", "", "annotation UUID")
			requestID := f.String("request-id", "", "idempotency request ID")
			collection := f.String("collection", "", "collection name")
			if err := f.Parse(args[2:]); err != nil {
				return nil, invalid(err.Error())
			}
			if *slug == "" || *annID == "" {
				return nil, invalid("--project and --annotation-id are required")
			}
			aUUID, err := uuid.Parse(*annID)
			if err != nil {
				return nil, invalid("annotation-id must be a valid UUID")
			}
			if *requestID == "" {
				*requestID = uuid.NewString()
			}
			if *collection == "" {
				*collection = store.Channel().Repo
				if *collection == "" {
					*collection = "default"
				}
			}
			return store.ResolveBlockerAnnotation(ctx, core.ResolveBlockerAnnotationRequest{
				RequestID:    *requestID,
				Collection:   *collection,
				ProjectSlug:  *slug,
				AnnotationID: aUUID,
			})

		default:
			return nil, invalid(fmt.Sprintf("unknown work blocker subcommand %q", args[1]))
		}

	case "metadata":
		if len(args) < 2 {
			return nil, invalid("work metadata requires purge or exclude")
		}
		switch args[1] {
		case "purge":
			f := flags("work metadata purge")
			slug := f.String("project", "", "local project slug")
			pattern := f.String("pattern", "", "pattern to match and purge")
			field := f.String("field", "all", "field to purge (title, all)")
			collection := f.String("collection", "", "collection name")
			if err := f.Parse(args[2:]); err != nil {
				return nil, invalid(err.Error())
			}
			if *slug == "" || *pattern == "" {
				return nil, invalid("--project and --pattern are required")
			}
			if *collection == "" {
				*collection = store.Channel().Repo
				if *collection == "" {
					*collection = "default"
				}
			}
			return store.PurgeMetadata(ctx, core.PurgeMetadataRequest{
				Collection:  *collection,
				ProjectSlug: *slug,
				FieldName:   *field,
				Pattern:     *pattern,
			})

		case "exclude":
			f := flags("work metadata exclude")
			slug := f.String("project", "", "local project slug")
			field := f.String("field", "", "field to exclude (title, etc.)")
			pattern := f.String("pattern", "", "pattern to exclude")
			collection := f.String("collection", "", "collection name")
			if err := f.Parse(args[2:]); err != nil {
				return nil, invalid(err.Error())
			}
			if *slug == "" || *field == "" || *pattern == "" {
				return nil, invalid("--project, --field, and --pattern are required")
			}
			if *collection == "" {
				*collection = store.Channel().Repo
				if *collection == "" {
					*collection = "default"
				}
			}
			return store.ExcludeMetadata(ctx, core.ExcludeMetadataRequest{
				Collection:  *collection,
				ProjectSlug: *slug,
				FieldName:   *field,
				Pattern:     *pattern,
			})

		default:
			return nil, invalid(fmt.Sprintf("unknown work metadata subcommand %q", args[1]))
		}

	case "serve":
		if len(args) > 1 && (args[1] == "--help" || args[1] == "-h" || args[1] == "help") {
			return commandHelp("cairn work serve [--project SLUG] [--port PORT] [--collection REPO]\n"), nil
		}
		return nil, runWorkServe(ctx, store, args[1:])

	default:
		return nil, invalid(fmt.Sprintf("unknown work subcommand %q", args[0]))
	}
}
