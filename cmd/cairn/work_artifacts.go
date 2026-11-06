package main

import (
	"context"
	"fmt"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
)

const workArtifactHelp = `cairn work artifact: maintain external deliverable revisions and item links

Subcommands:
  work artifact register --kind (git|report|ci|cairn_result) [KIND_FLAGS] [--request-id UUID] [--collection REPO]
  work artifact show (--artifact UUID | --revision UUID) [--collection REPO]
  work artifact link --project SLUG --item ITEM --revision UUID --role (input|output|evidence) [--rationale TEXT] [--request-id UUID] [--collection REPO]
  work artifact unlink --project SLUG --item ITEM --link-id UUID --expected-revision N [--request-id UUID] [--collection REPO]
  work artifact list --project SLUG --item ITEM [--role ROLE] [--include-removed] [--collection REPO]
  work artifact impact --revision UUID [--limit N] [--cursor CURSOR] [--collection REPO]
  work artifact purge --artifact UUID [--request-id UUID] [--collection REPO]
`

func workArtifactCommand(ctx context.Context, store *core.Store, args []string) (any, error) {
	if len(args) == 0 || hasWorkHelpArg(args) {
		return commandHelp(workArtifactHelp), nil
	}

	sub := args[0]
	switch sub {
	case "register":
		f := flags("work artifact register")
		kind := f.String("kind", "", "artifact kind: git, report, ci, cairn_result")
		displayName := f.String("display-name", "", "optional display name")
		revLabel := f.String("revision-label", "", "revision label")

		// Git
		gitRepo := f.String("repo", "", "git repository URL or path")
		gitCommit := f.String("commit", "", "full hex commit OID")
		gitFormat := f.String("format", "", "git object format (sha1 or sha256)")
		gitBranch := f.String("branch-hint", "", "display branch hint")

		// Report
		repURL := f.String("url", "", "HTTPS report locator")
		repRev := f.String("revision", "", "report revision label")
		repDigest := f.String("digest", "", "optional content digest")
		repProv := f.String("provenance", "unverified", "provenance: unverified, asserted, verified")

		// CI
		ciProvider := f.String("provider", "", "CI provider name")
		ciRepo := f.String("ci-repo", "", "CI repository")
		ciRunID := f.String("run-id", "", "CI run ID")
		ciAttempt := f.Int("attempt", 1, "CI attempt number")
		ciStatus := f.String("status", "", "CI status observation")

		// Cairn Result
		cairnRec := f.String("record", "", "cairn record UUID")
		cairnVer := f.Int("version", 0, "cairn positive version")

		requestID := f.String("request-id", "", "idempotency request ID")
		collection := f.String("collection", "", "collection name")

		if err := f.Parse(args[1:]); err != nil {
			return nil, invalid(err.Error())
		}
		if *kind == "" {
			return nil, invalid("--kind is required (git, report, ci, cairn_result)")
		}
		if *collection == "" {
			*collection = store.Channel().Repo
			if *collection == "" {
				*collection = "default"
			}
		}

		req := core.RegisterWorkArtifactRequest{
			RequestID:        *requestID,
			Collection:       *collection,
			Kind:             *kind,
			DisplayName:      *displayName,
			RevisionLabel:    *revLabel,
			GitRepo:          *gitRepo,
			GitCommitOID:     *gitCommit,
			GitObjectFormat:  *gitFormat,
			GitBranchHint:    *gitBranch,
			ReportLocator:    *repURL,
			ReportDigest:     *repDigest,
			ReportProvenance: *repProv,
			CIProvider:       *ciProvider,
			CIRepo:           *ciRepo,
			CIRunID:          *ciRunID,
			CIAttempt:        *ciAttempt,
			CIStatus:         *ciStatus,
		}
		if *repRev != "" && req.RevisionLabel == "" {
			req.RevisionLabel = *repRev
		}
		if *cairnRec != "" {
			recUUID, err := uuid.Parse(*cairnRec)
			if err != nil {
				return nil, invalid("record must be a valid UUID")
			}
			req.CairnRecordID = &recUUID
		}
		if *cairnVer > 0 {
			ver := *cairnVer
			req.CairnVersion = &ver
		}

		return store.RegisterWorkArtifact(ctx, req)

	case "show":
		f := flags("work artifact show")
		artifactIDStr := f.String("artifact", "", "artifact UUID")
		revisionIDStr := f.String("revision", "", "revision UUID")
		collection := f.String("collection", "", "collection name")
		if err := f.Parse(args[1:]); err != nil {
			return nil, invalid(err.Error())
		}
		if *collection == "" {
			*collection = store.Channel().Repo
			if *collection == "" {
				*collection = "default"
			}
		}
		if *revisionIDStr != "" {
			revUUID, err := uuid.Parse(*revisionIDStr)
			if err != nil {
				return nil, invalid("revision must be a valid UUID")
			}
			return store.GetWorkArtifactRevision(ctx, *collection, revUUID)
		}
		if *artifactIDStr != "" {
			artUUID, err := uuid.Parse(*artifactIDStr)
			if err != nil {
				return nil, invalid("artifact must be a valid UUID")
			}
			return store.GetWorkArtifact(ctx, *collection, artUUID)
		}
		return nil, invalid("--artifact or --revision is required")

	case "link":
		f := flags("work artifact link")
		slug := f.String("project", "", "local project slug")
		item := f.String("item", "", "work item UUID or identifier")
		revisionIDStr := f.String("revision", "", "artifact revision UUID")
		role := f.String("role", "", "link role: input, output, evidence")
		rationale := f.String("rationale", "", "optional rationale")
		requestID := f.String("request-id", "", "idempotency request ID")
		collection := f.String("collection", "", "collection name")

		if err := f.Parse(args[1:]); err != nil {
			return nil, invalid(err.Error())
		}
		if *slug == "" || *item == "" || *revisionIDStr == "" || *role == "" {
			return nil, invalid("--project, --item, --revision, and --role are required")
		}
		if *collection == "" {
			*collection = store.Channel().Repo
			if *collection == "" {
				*collection = "default"
			}
		}
		revUUID, err := uuid.Parse(*revisionIDStr)
		if err != nil {
			return nil, invalid("revision must be a valid UUID")
		}
		itemUUID, err := resolveWorkItemUUID(ctx, store, *collection, *slug, *item)
		if err != nil {
			return nil, err
		}

		return store.LinkWorkArtifact(ctx, core.LinkWorkArtifactRequest{
			RequestID:   *requestID,
			Collection:  *collection,
			ProjectSlug: *slug,
			ItemUUID:    itemUUID,
			RevisionID:  revUUID,
			Role:        *role,
			Rationale:   *rationale,
		})

	case "unlink":
		f := flags("work artifact unlink")
		slug := f.String("project", "", "local project slug")
		item := f.String("item", "", "work item UUID or identifier")
		linkIDStr := f.String("link-id", "", "artifact link UUID")
		expectedRev := f.Int64("expected-revision", 0, "positive expected revision guard")
		requestID := f.String("request-id", "", "idempotency request ID")
		collection := f.String("collection", "", "collection name")

		if err := f.Parse(args[1:]); err != nil {
			return nil, invalid(err.Error())
		}
		if *slug == "" || *item == "" || *linkIDStr == "" || *expectedRev <= 0 {
			return nil, invalid("--project, --item, --link-id, and positive --expected-revision are required")
		}
		if *collection == "" {
			*collection = store.Channel().Repo
			if *collection == "" {
				*collection = "default"
			}
		}
		linkUUID, err := uuid.Parse(*linkIDStr)
		if err != nil {
			return nil, invalid("link-id must be a valid UUID")
		}
		itemUUID, err := resolveWorkItemUUID(ctx, store, *collection, *slug, *item)
		if err != nil {
			return nil, err
		}

		return store.UnlinkWorkArtifact(ctx, core.UnlinkWorkArtifactRequest{
			RequestID:        *requestID,
			Collection:       *collection,
			ProjectSlug:      *slug,
			ItemUUID:         itemUUID,
			LinkID:           linkUUID,
			ExpectedRevision: *expectedRev,
		})

	case "list":
		f := flags("work artifact list")
		slug := f.String("project", "", "local project slug")
		item := f.String("item", "", "work item UUID or identifier")
		role := f.String("role", "", "optional role filter")
		includeRemoved := f.Bool("include-removed", false, "include removed links")
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
		itemUUID, err := resolveWorkItemUUID(ctx, store, *collection, *slug, *item)
		if err != nil {
			return nil, err
		}

		links, err := store.ListWorkArtifactLinks(ctx, core.ListWorkArtifactLinksRequest{
			Collection:     *collection,
			ProjectSlug:    *slug,
			ItemUUID:       itemUUID,
			Role:           *role,
			IncludeRemoved: *includeRemoved,
		})
		if err != nil {
			return nil, err
		}
		return map[string]any{"artifacts": links}, nil

	case "impact":
		f := flags("work artifact impact")
		revIDStr := f.String("revision", "", "artifact revision UUID")
		limit := f.Int("limit", 50, "maximum items per page")
		cursor := f.String("cursor", "", "keyset pagination cursor")
		collection := f.String("collection", "", "collection name")

		if err := f.Parse(args[1:]); err != nil {
			return nil, invalid(err.Error())
		}
		if *revIDStr == "" {
			return nil, invalid("--revision is required")
		}
		if *collection == "" {
			*collection = store.Channel().Repo
			if *collection == "" {
				*collection = "default"
			}
		}
		revUUID, err := uuid.Parse(*revIDStr)
		if err != nil {
			return nil, invalid("revision must be a valid UUID")
		}

		return store.ReverseImpact(ctx, core.ReverseImpactRequest{
			Collection: *collection,
			RevisionID: revUUID,
			Limit:      *limit,
			Cursor:     *cursor,
		})

	case "purge":
		f := flags("work artifact purge")
		artifactIDStr := f.String("artifact", "", "artifact UUID to purge")
		requestID := f.String("request-id", "", "idempotency request ID")
		collection := f.String("collection", "", "collection name")

		if err := f.Parse(args[1:]); err != nil {
			return nil, invalid(err.Error())
		}
		if *artifactIDStr == "" {
			return nil, invalid("--artifact is required")
		}
		if *collection == "" {
			*collection = store.Channel().Repo
			if *collection == "" {
				*collection = "default"
			}
		}
		artUUID, err := uuid.Parse(*artifactIDStr)
		if err != nil {
			return nil, invalid("artifact must be a valid UUID")
		}

		err = store.PurgeWorkArtifact(ctx, core.PurgeWorkArtifactRequest{
			RequestID:  *requestID,
			Collection: *collection,
			ArtifactID: artUUID,
		})
		if err != nil {
			return nil, err
		}
		return map[string]any{"purged": true, "artifact_id": artUUID}, nil

	default:
		return nil, invalid(fmt.Sprintf("unknown work artifact subcommand %q", sub))
	}
}
