package main

import (
	"context"
	"errors"
	"fmt"
	"io"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
	"github.com/halbritt/cairn/internal/plane"
)

const workHelp = `cairn work: shared work projection and work coordination

Subcommands:
  work project add --slug SLUG --workspace WS_UUID --project PROJ_UUID [--origin URL] [--instance NAME] [--request-id UUID] [--collection REPO]
  work project list [--collection REPO]
  work project show --project SLUG [--collection REPO]
  work project disable --project SLUG [--request-id UUID] [--collection REPO]
  work refresh --project SLUG [--collection REPO]
  work list --project SLUG [--state-group GROUP] [--limit N] [--offset N] [--collection REPO]
  work show --project SLUG --item ITEM_UUID_OR_IDENT [--collection REPO]
  work associate --project SLUG --item ITEM_UUID_OR_IDENT --target-type TYPE --target-id ID [--rationale TEXT] [--request-id UUID] [--collection REPO]
  work unassociate --project SLUG --item ITEM_UUID_OR_IDENT --association-id UUID --expected-revision N [--request-id UUID] [--collection REPO]
  work progress add --project SLUG --item ITEM_UUID_OR_IDENT --text TEXT [--request-id UUID] [--collection REPO]
  work progress edit --project SLUG --item ITEM_UUID_OR_IDENT --annotation-id UUID --text TEXT --expected-revision N [--request-id UUID] [--collection REPO]
  work blocker add --project SLUG --item ITEM_UUID_OR_IDENT --text TEXT [--request-id UUID] [--collection REPO]
  work blocker resolve --project SLUG --item ITEM_UUID_OR_IDENT --annotation-id UUID --expected-revision N [--request-id UUID] [--collection REPO]
  work artifact (register|show|link|unlink|list|impact|purge) [FLAGS] [--collection REPO]
  work metadata purge --project SLUG --pattern PATTERN [--field FIELD] [--exclude] [--request-id UUID] [--collection REPO]
  work metadata exclude --project SLUG --field FIELD --pattern PATTERN [--request-id UUID] [--collection REPO]
  work features (status|enable|disable|retire) [--feature NAME] [--request-id UUID] [--collection REPO]
  work serve [--project SLUG] [--port PORT] [--collection REPO]

Features: work-view/1, work-artifacts/1, coordination-waits/1, next-turn-notices/1
`

const workFeaturesHelp = `cairn work features: manage work coordination features (work-view/1, work-artifacts/1, coordination-waits/1, next-turn-notices/1)

  work features (status|enable|disable|retire) [--feature NAME] [--request-id UUID] [--collection REPO]
`

func hasWorkHelpArg(args []string) bool {
	for _, a := range args {
		if a == "--help" || a == "-h" || a == "help" {
			return true
		}
	}
	return false
}

func resolveWorkItemUUID(ctx context.Context, store *core.Store, collection, slug, raw string) (uuid.UUID, error) {
	if raw == "" {
		return uuid.Nil, nil
	}
	if u, err := uuid.Parse(raw); err == nil {
		if u == uuid.Nil {
			return uuid.Nil, invalid("item must be a valid non-nil UUID")
		}
		return u, nil
	}
	detail, err := store.GetWorkItemDetail(ctx, collection, slug, raw)
	if err != nil {
		return uuid.Nil, err
	}
	return detail.Item.PlaneItemUUID, nil
}

func mapPlaneError(err error) error {
	if err == nil {
		return nil
	}
	var planeErr *plane.Error
	if errors.As(err, &planeErr) {
		return &core.Error{Code: planeErr.Code, Message: planeErr.Message}
	}
	return err
}

func recordPlaneRefreshFailure(ctx context.Context, store *core.Store, refCtx core.RefreshContext, err error) error {
	mapped := mapPlaneError(err)
	authRejected := core.Code(mapped) == "AUTHORITY_DENIED"
	summary := mapped.Error()
	if authRejected {
		summary = "Plane auth rejected"
	}
	if persistErr := store.RecordRefreshFailure(ctx, refCtx, authRejected, summary); persistErr != nil {
		return &core.Error{
			Code:    core.Code(mapped),
			Message: fmt.Sprintf("%s (refresh failure persistence failed: %v)", mapped.Error(), persistErr),
			Cause:   errors.Join(mapped, persistErr),
		}
	}
	return mapped
}

func refreshWorkProject(ctx context.Context, store *core.Store, collection, slug string) (*core.WorkSnapshot, error) {
	refCtx, err := store.PrepareRefresh(ctx, collection, slug)
	if err != nil {
		return nil, err
	}

	conn := plane.NewConnector()
	verified, verifyErr := conn.VerifyRefreshBinding(
		ctx,
		refCtx.PlaneInstance,
		refCtx.PlaneOrigin,
		refCtx.WorkspaceUUID,
		refCtx.ProjectUUID,
		refCtx.ConfigRevision,
	)
	if verifyErr != nil {
		return nil, recordPlaneRefreshFailure(ctx, store, refCtx, verifyErr)
	}

	items, refreshErr := conn.RefreshIssues(
		ctx,
		verified.PlaneInstance,
		verified.ProjectUUID,
		verified.PlaneOrigin,
		verified.WorkspaceSlug,
		verified.ProjectIdentifier,
	)
	if refreshErr != nil {
		return nil, recordPlaneRefreshFailure(ctx, store, refCtx, refreshErr)
	}

	effAfter, effErr := conn.ResolveEffectiveConfig(plane.ConfigSelector{
		Instance: refCtx.PlaneInstance,
		Project:  refCtx.ProjectUUID.String(),
	})
	if effErr != nil {
		return nil, recordPlaneRefreshFailure(ctx, store, refCtx, effErr)
	}
	postRev := plane.ComputeConfigRevision(
		conn.ConnectorIdentity(),
		effAfter.PlaneOrigin,
		effAfter.WorkspaceSlug,
		refCtx.WorkspaceUUID,
		refCtx.ProjectUUID,
	)
	if effAfter.PlaneOrigin != refCtx.PlaneOrigin || postRev != refCtx.ConfigRevision {
		mismatchErr := &core.Error{
			Code:    "CONFIG_MISMATCH",
			Message: "Plane effective routing configuration changed during refresh",
		}
		if persistErr := store.RecordRefreshFailure(ctx, refCtx, false, mismatchErr.Error()); persistErr != nil {
			return nil, &core.Error{
				Code:    mismatchErr.Code,
				Message: fmt.Sprintf("%s (refresh failure persistence failed: %v)", mismatchErr.Error(), persistErr),
				Cause:   errors.Join(mismatchErr, persistErr),
			}
		}
		return nil, mismatchErr
	}

	return store.CommitRefreshSnapshot(ctx, refCtx, items, false, nil)
}

func workCommand(ctx context.Context, store *core.Store, args []string, input io.Reader) (any, error) {
	if len(args) == 0 || args[0] == "--help" || args[0] == "-h" || args[0] == "help" {
		return commandHelp(workHelp), nil
	}
	if !hasWorkHelpArg(args) && store != nil {
		if err := store.CheckCompatibilityFloor(ctx, 75); err != nil {
			return nil, err
		}
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
			origin := f.String("origin", "", "optional expected Plane origin URL")
			instance := f.String("instance", "", "Plane CLI instance name")
			requestID := f.String("request-id", "", "idempotency request ID")
			collection := f.String("collection", "", "collection name")
			if err := f.Parse(args[2:]); err != nil {
				return nil, invalid(err.Error())
			}
			if *slug == "" || *ws == "" || *proj == "" {
				return nil, invalid("--slug, --workspace, and --project are required")
			}
			wsUUID, err := uuid.Parse(*ws)
			if err != nil || wsUUID == uuid.Nil {
				return nil, invalid("workspace must be a valid UUID")
			}
			projUUID, err := uuid.Parse(*proj)
			if err != nil || projUUID == uuid.Nil {
				return nil, invalid("project must be a valid UUID")
			}
			if *requestID == "" {
				*requestID = uuid.NewString()
			} else if _, err := uuid.Parse(*requestID); err != nil {
				return nil, invalid("request-id must be a valid UUID")
			}
			if *collection == "" {
				*collection = store.Channel().Repo
				if *collection == "" {
					*collection = "default"
				}
			}
			conn := plane.NewConnector()
			bindingCfg, err := conn.ResolveBinding(ctx, plane.ConfigSelector{
				Instance: *instance,
				Project:  projUUID.String(),
			}, wsUUID, projUUID)
			if err != nil {
				return nil, mapPlaneError(err)
			}
			if *origin != "" {
				expectedOrigin, oErr := plane.NormalizeOrigin(*origin)
				if oErr != nil {
					return nil, mapPlaneError(oErr)
				}
				if bindingCfg.PlaneOrigin != expectedOrigin {
					return nil, &core.Error{
						Code:    "CONFIG_MISMATCH",
						Message: fmt.Sprintf("Plane CLI configured origin %q does not match expected --origin %q", bindingCfg.PlaneOrigin, expectedOrigin),
					}
				}
			}
			return store.AddWorkProject(ctx, core.AddWorkProjectRequest{
				RequestID:      *requestID,
				LocalSlug:      *slug,
				Collection:     *collection,
				PlaneOrigin:    bindingCfg.PlaneOrigin,
				WorkspaceUUID:  bindingCfg.WorkspaceUUID,
				ProjectUUID:    bindingCfg.ProjectUUID,
				PlaneInstance:  *instance,
				ConfigRevision: bindingCfg.ConfigRevision,
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
			requestID := f.String("request-id", "", "idempotency request ID")
			collection := f.String("collection", "", "collection name")
			if err := f.Parse(args[2:]); err != nil {
				return nil, invalid(err.Error())
			}
			if *slug == "" {
				return nil, invalid("--project is required")
			}
			if *requestID == "" {
				*requestID = uuid.NewString()
			} else if _, err := uuid.Parse(*requestID); err != nil {
				return nil, invalid("request-id must be a valid UUID")
			}
			if *collection == "" {
				*collection = store.Channel().Repo
				if *collection == "" {
					*collection = "default"
				}
			}
			return store.DisableWorkProject(ctx, *collection, *slug, *requestID)

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
		return refreshWorkProject(ctx, store, *collection, *slug)

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
		if *requestID == "" {
			*requestID = uuid.NewString()
		}
		if *collection == "" {
			*collection = store.Channel().Repo
			if *collection == "" {
				*collection = "default"
			}
		}
		itemUUID, err := resolveWorkItemUUID(ctx, store, *collection, *slug, *item)
		if err != nil {
			return nil, err
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
		item := f.String("item", "", "item UUID or identifier guard")
		assocID := f.String("association-id", "", "association UUID")
		expectedRev := f.Int64("expected-revision", 0, "positive expected revision guard")
		requestID := f.String("request-id", "", "idempotency request ID")
		collection := f.String("collection", "", "collection name")
		if err := f.Parse(args[1:]); err != nil {
			return nil, invalid(err.Error())
		}
		if *slug == "" || *item == "" || *assocID == "" || *expectedRev <= 0 {
			return nil, invalid("--project, --item, --association-id, and positive --expected-revision are required")
		}
		aUUID, err := uuid.Parse(*assocID)
		if err != nil || aUUID == uuid.Nil {
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
		itemUUID, err := resolveWorkItemUUID(ctx, store, *collection, *slug, *item)
		if err != nil {
			return nil, err
		}
		v := *expectedRev
		return store.UnassociateWork(ctx, core.UnassociateWorkRequest{
			RequestID:        *requestID,
			Collection:       *collection,
			ProjectSlug:      *slug,
			AssociationID:    aUUID,
			ItemUUID:         itemUUID,
			ExpectedRevision: &v,
		})

	case "progress":
		if len(args) < 2 {
			return nil, invalid("work progress requires add or edit")
		}
		switch args[1] {
		case "add":
			f := flags("work progress add")
			slug := f.String("project", "", "local project slug")
			item := f.String("item", "", "item UUID or identifier")
			text := f.String("text", "", "progress text (max 2 KiB)")
			requestID := f.String("request-id", "", "idempotency request ID")
			collection := f.String("collection", "", "collection name")
			if err := f.Parse(args[2:]); err != nil {
				return nil, invalid(err.Error())
			}
			if *slug == "" || *item == "" || *text == "" {
				return nil, invalid("--project, --item, and --text are required")
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
			itemUUID, err := resolveWorkItemUUID(ctx, store, *collection, *slug, *item)
			if err != nil {
				return nil, err
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
			item := f.String("item", "", "item UUID or identifier guard")
			annID := f.String("annotation-id", "", "annotation UUID")
			text := f.String("text", "", "new progress text")
			expectedRev := f.Int64("expected-revision", 0, "positive expected revision guard")
			requestID := f.String("request-id", "", "idempotency request ID")
			collection := f.String("collection", "", "collection name")
			if err := f.Parse(args[2:]); err != nil {
				return nil, invalid(err.Error())
			}
			if *slug == "" || *item == "" || *annID == "" || *text == "" || *expectedRev <= 0 {
				return nil, invalid("--project, --item, --annotation-id, --text, and positive --expected-revision are required")
			}
			aUUID, err := uuid.Parse(*annID)
			if err != nil || aUUID == uuid.Nil {
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
			itemUUID, err := resolveWorkItemUUID(ctx, store, *collection, *slug, *item)
			if err != nil {
				return nil, err
			}
			return store.EditProgressAnnotation(ctx, core.EditProgressAnnotationRequest{
				RequestID:        *requestID,
				Collection:       *collection,
				ProjectSlug:      *slug,
				ItemUUID:         itemUUID,
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
			item := f.String("item", "", "item UUID or identifier")
			text := f.String("text", "", "blocker text (max 2 KiB)")
			requestID := f.String("request-id", "", "idempotency request ID")
			collection := f.String("collection", "", "collection name")
			if err := f.Parse(args[2:]); err != nil {
				return nil, invalid(err.Error())
			}
			if *slug == "" || *item == "" || *text == "" {
				return nil, invalid("--project, --item, and --text are required")
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
			itemUUID, err := resolveWorkItemUUID(ctx, store, *collection, *slug, *item)
			if err != nil {
				return nil, err
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
			item := f.String("item", "", "item UUID or identifier guard")
			annID := f.String("annotation-id", "", "annotation UUID")
			expectedRev := f.Int64("expected-revision", 0, "positive expected revision guard")
			requestID := f.String("request-id", "", "idempotency request ID")
			collection := f.String("collection", "", "collection name")
			if err := f.Parse(args[2:]); err != nil {
				return nil, invalid(err.Error())
			}
			if *slug == "" || *item == "" || *annID == "" || *expectedRev <= 0 {
				return nil, invalid("--project, --item, --annotation-id, and positive --expected-revision are required")
			}
			aUUID, err := uuid.Parse(*annID)
			if err != nil || aUUID == uuid.Nil {
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
			itemUUID, err := resolveWorkItemUUID(ctx, store, *collection, *slug, *item)
			if err != nil {
				return nil, err
			}
			return store.ResolveBlockerAnnotation(ctx, core.ResolveBlockerAnnotationRequest{
				RequestID:        *requestID,
				Collection:       *collection,
				ProjectSlug:      *slug,
				ItemUUID:         itemUUID,
				AnnotationID:     aUUID,
				ExpectedRevision: *expectedRev,
			})

		default:
			return nil, invalid(fmt.Sprintf("unknown work blocker subcommand %q", args[1]))
		}

	case "artifact":
		return workArtifactCommand(ctx, store, args[1:])

	case "metadata":
		if len(args) < 2 {
			return nil, invalid("work metadata requires purge or exclude")
		}
		switch args[1] {
		case "purge":
			f := flags("work metadata purge")
			slug := f.String("project", "", "local project slug")
			pattern := f.String("pattern", "", "pattern to match and purge")
			field := f.String("field", "all", "field to purge (title, annotation, all)")
			exclude := f.Bool("exclude", false, "also record persistent exclusion")
			requestID := f.String("request-id", "", "idempotency request ID")
			collection := f.String("collection", "", "collection name")
			if err := f.Parse(args[2:]); err != nil {
				return nil, invalid(err.Error())
			}
			if *slug == "" || *pattern == "" {
				return nil, invalid("--project and --pattern are required")
			}
			if *requestID == "" {
				*requestID = uuid.NewString()
			} else if _, err := uuid.Parse(*requestID); err != nil {
				return nil, invalid("request-id must be a valid UUID")
			}
			if *collection == "" {
				*collection = store.Channel().Repo
				if *collection == "" {
					*collection = "default"
				}
			}
			return store.PurgeMetadata(ctx, core.PurgeMetadataRequest{
				RequestID:   *requestID,
				Collection:  *collection,
				ProjectSlug: *slug,
				FieldName:   *field,
				Pattern:     *pattern,
				Exclude:     *exclude,
			})

		case "exclude":
			f := flags("work metadata exclude")
			slug := f.String("project", "", "local project slug")
			field := f.String("field", "", "field to exclude (title, annotation, all)")
			pattern := f.String("pattern", "", "pattern to exclude")
			requestID := f.String("request-id", "", "idempotency request ID")
			collection := f.String("collection", "", "collection name")
			if err := f.Parse(args[2:]); err != nil {
				return nil, invalid(err.Error())
			}
			if *slug == "" || *field == "" || *pattern == "" {
				return nil, invalid("--project, --field, and --pattern are required")
			}
			if *requestID == "" {
				*requestID = uuid.NewString()
			} else if _, err := uuid.Parse(*requestID); err != nil {
				return nil, invalid("request-id must be a valid UUID")
			}
			if *collection == "" {
				*collection = store.Channel().Repo
				if *collection == "" {
					*collection = "default"
				}
			}
			return store.ExcludeMetadata(ctx, core.ExcludeMetadataRequest{
				RequestID:   *requestID,
				Collection:  *collection,
				ProjectSlug: *slug,
				FieldName:   *field,
				Pattern:     *pattern,
			})

		default:
			return nil, invalid(fmt.Sprintf("unknown work metadata subcommand %q", args[1]))
		}

	case "features":
		return workFeaturesCommand(ctx, store, args[1:])

	case "serve":
		if len(args) > 1 && (args[1] == "--help" || args[1] == "-h" || args[1] == "help") {
			return commandHelp("cairn work serve [--project SLUG] [--port PORT] [--collection REPO]\n"), nil
		}
		return nil, runWorkServe(ctx, store, args[1:])

	default:
		return nil, invalid(fmt.Sprintf("unknown work subcommand %q", args[0]))
	}
}

type workFeatureView struct {
	core.WorkFeature
	Enabled bool `json:"enabled"`
	Retired bool `json:"retired"`
}

type workFeatureSingleView struct {
	workFeatureView
	Features []workFeatureView `json:"features"`
}

func isValidWorkFeatureName(name string) bool {
	switch name {
	case "work-view/1", "work-artifacts/1", "coordination-waits/1", "next-turn-notices/1", "work-advice/1":
		return true
	default:
		return false
	}
}

func workFeaturesCommand(ctx context.Context, store *core.Store, args []string) (any, error) {
	if len(args) == 0 || args[0] == "--help" || args[0] == "-h" || args[0] == "help" {
		return commandHelp(workFeaturesHelp), nil
	}
	sub := args[0]
	f := flags("work features " + sub)
	feature := f.String("feature", "", "versioned feature name (default work-view/1 on mutate)")
	requestID := f.String("request-id", "", "idempotency request ID")
	_ = f.String("collection", "", "collection name")
	_ = f.String("repo", "", "alias for collection")
	if err := f.Parse(args[1:]); err != nil {
		return nil, invalid(err.Error())
	}
	targetArg := *feature
	if targetArg == "" && f.NArg() > 0 {
		targetArg = f.Arg(0)
	}
	switch sub {
	case "status":
		if targetArg != "" && !isValidWorkFeatureName(targetArg) {
			return nil, invalid(fmt.Sprintf("unknown work feature %q", targetArg))
		}
		rows, err := store.ListWorkFeatures(ctx)
		if err != nil {
			return nil, err
		}
		out := make([]workFeatureView, 0, len(rows))
		for _, r := range rows {
			if targetArg != "" && r.FeatureName != targetArg {
				continue
			}
			out = append(out, workFeatureView{WorkFeature: r, Enabled: r.Status == "active", Retired: r.Status == "retired"})
		}
		if targetArg != "" && len(out) == 0 {
			st, err := store.GetWorkFeatureStatus(ctx, targetArg)
			if err != nil {
				return nil, err
			}
			out = append(out, workFeatureView{
				WorkFeature: core.WorkFeature{FeatureName: targetArg, Status: st},
				Enabled:     st == "active",
				Retired:     st == "retired",
			})
		}
		if targetArg != "" && len(out) == 1 {
			return workFeatureSingleView{workFeatureView: out[0], Features: out}, nil
		}
		return map[string]any{"features": out}, nil
	case "enable", "disable", "retire":
		target := targetArg
		if target == "" {
			target = "work-view/1"
		}
		if !isValidWorkFeatureName(target) {
			return nil, invalid(fmt.Sprintf("unknown work feature %q", target))
		}
		if *requestID == "" {
			*requestID = uuid.NewString()
		} else if _, err := uuid.Parse(*requestID); err != nil {
			return nil, invalid("request-id must be a valid UUID")
		}
		statusMap := map[string]string{"enable": "active", "disable": "disabled", "retire": "retired"}
		if err := store.SetWorkFeatureStatus(ctx, core.SetWorkFeatureStatusRequest{
			RequestID:   *requestID,
			FeatureName: target,
			Status:      statusMap[sub],
		}); err != nil {
			return nil, err
		}
		rows, err := store.ListWorkFeatures(ctx)
		if err != nil {
			return nil, err
		}
		for _, r := range rows {
			if r.FeatureName == target {
				return workFeatureView{WorkFeature: r, Enabled: r.Status == "active", Retired: r.Status == "retired"}, nil
			}
		}
		return workFeatureView{
			WorkFeature: core.WorkFeature{FeatureName: target, Status: statusMap[sub]},
			Enabled:     statusMap[sub] == "active",
			Retired:     statusMap[sub] == "retired",
		}, nil
	default:
		return nil, invalid(fmt.Sprintf("unknown work features subcommand %q", sub))
	}
}
