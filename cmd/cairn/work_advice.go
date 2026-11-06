package main

import (
	"context"
	"flag"
	"fmt"
	"strconv"
	"strings"
	"time"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
	"github.com/halbritt/cairn/internal/advice"
)

const workAdviceHelp = `cairn work advice: model-assisted coordination proposals and actions

Subcommands:
  work advice generate --project SLUG --item ITEM [--profile NAME] [--timeout SECONDS] [--request-id UUID] [--candidate-revision REV...] [--source RECORD[:VER]...] [--collection REPO]
  work advice list --project SLUG --item ITEM [--collection REPO]
  work advice show (--proposal UUID | --request-id UUID) [--collection REPO]
  work advice apply --proposal UUID --version N [--action UUID...] [--request-id UUID] [--collection REPO]
  work advice reject --proposal UUID --version N [--collection REPO]
  work advice undo (--receipt UUID | --request-id UUID) [--collection REPO]
  work advice control (status|get|set) [--generation true|false] [--apply true|false]
  work advice profile (register|list|show) [FLAGS]
  work advice policy (get|show|set) [FLAGS]
  work advice reconcile [--collection REPO]
  work advice sweep [--collection REPO]
`

type adviceSliceValue []string

func (s *adviceSliceValue) String() string {
	if s == nil {
		return ""
	}
	return strings.Join(*s, ",")
}

func (s *adviceSliceValue) Set(v string) error {
	*s = append(*s, v)
	return nil
}

func workAdviceCommand(ctx context.Context, store *core.Store, args []string) (any, error) {
	if len(args) == 0 || hasWorkHelpArg(args) {
		return commandHelp(workAdviceHelp), nil
	}

	sub := args[0]
	switch sub {
	case "generate":
		f := flags("work advice generate")
		slug := f.String("project", "", "project slug")
		rawItem := f.String("item", "", "item UUID or identifier")
		profileName := f.String("profile", "", "registered advice profile name")
		modelRoute := f.String("model", "", "override model name")
		timeoutSec := f.Int("timeout", 90, "job deadline in seconds (10-180)")
		requestID := f.String("request-id", "", "idempotency request ID")
		collection := f.String("collection", "", "collection name")

		var candidateRevs adviceSliceValue
		var selectedSources adviceSliceValue
		f.Var(&candidateRevs, "candidate-revision", "candidate artifact revision UUID (repeatable)")
		f.Var(&selectedSources, "source", "selected source RECORD[:VERSION] (repeatable)")

		if err := f.Parse(args[1:]); err != nil {
			return nil, invalid(err.Error())
		}
		if *slug == "" {
			return nil, invalid("--project is required")
		}
		if *rawItem == "" {
			return nil, invalid("--item is required")
		}
		if *collection == "" {
			*collection = store.Channel().Repo
			if *collection == "" {
				*collection = "default"
			}
		}

		proj, err := store.GetWorkProject(ctx, *collection, *slug)
		if err != nil {
			return nil, err
		}
		itemUUID, err := store.ResolveWorkItemUUID(ctx, *collection, *slug, *rawItem)
		if err != nil {
			return nil, err
		}

		var candidateUUIDs []uuid.UUID
		for _, raw := range candidateRevs {
			u, err := uuid.Parse(raw)
			if err != nil || u == uuid.Nil {
				return nil, invalid(fmt.Sprintf("invalid candidate revision UUID %q", raw))
			}
			candidateUUIDs = append(candidateUUIDs, u)
		}

		var sources []core.SourceRefSpec
		for _, raw := range selectedSources {
			parts := strings.SplitN(raw, ":", 2)
			recID, err := uuid.Parse(parts[0])
			if err != nil || recID == uuid.Nil {
				return nil, invalid(fmt.Sprintf("invalid source record UUID %q", parts[0]))
			}
			ver := 1
			if len(parts) == 2 {
				v, err := strconv.Atoi(parts[1])
				if err != nil || v <= 0 {
					return nil, invalid(fmt.Sprintf("invalid source record version %q", parts[1]))
				}
				ver = v
			}
			sources = append(sources, core.SourceRefSpec{
				RecordID: recID,
				Version:  ver,
			})
		}

		reqUUID := uuid.New()
		if *requestID != "" {
			u, err := uuid.Parse(*requestID)
			if err != nil || u == uuid.Nil {
				return nil, invalid("request-id must be a valid non-nil UUID")
			}
			reqUUID = u
		}

		job := &core.WorkAdviceJob{
			BindingID:       proj.BindingID,
			PlaneItemUUID:   itemUUID,
			ProfileName:     *profileName,
			ModelRoute:      *modelRoute,
			DeadlineSeconds: *timeoutSec,
			RequestUUID:     reqUUID,
		}

		spec := core.AdviceInputSpec{
			BindingID:            proj.BindingID,
			PlaneItemUUID:        itemUUID,
			CandidateRevisionIDs: candidateUUIDs,
			SelectedSources:      sources,
		}

		prop, actions, err := store.GenerateWorkAdvice(ctx, job, spec, nil)
		if err != nil {
			return nil, err
		}
		return map[string]any{
			"proposal": prop,
			"actions":  actions,
		}, nil

	case "list":
		f := flags("work advice list")
		slug := f.String("project", "", "project slug")
		rawItem := f.String("item", "", "item UUID or identifier")
		collection := f.String("collection", "", "collection name")
		if err := f.Parse(args[1:]); err != nil {
			return nil, invalid(err.Error())
		}
		if *slug == "" {
			return nil, invalid("--project is required")
		}
		if *rawItem == "" {
			return nil, invalid("--item is required")
		}
		if *collection == "" {
			*collection = store.Channel().Repo
			if *collection == "" {
				*collection = "default"
			}
		}

		proj, err := store.GetWorkProject(ctx, *collection, *slug)
		if err != nil {
			return nil, err
		}
		itemUUID, err := store.ResolveWorkItemUUID(ctx, *collection, *slug, *rawItem)
		if err != nil {
			return nil, err
		}

		proposals, err := store.ListAdviceProposals(ctx, proj.BindingID, itemUUID)
		if err != nil {
			return nil, err
		}
		if proposals == nil {
			proposals = make([]*core.WorkAdviceProposal, 0)
		}
		return map[string]any{"proposals": proposals}, nil

	case "show":
		f := flags("work advice show")
		propID := f.String("proposal", "", "proposal UUID")
		reqID := f.String("request-id", "", "request UUID")
		collection := f.String("collection", "", "collection name")
		if err := f.Parse(args[1:]); err != nil {
			return nil, invalid(err.Error())
		}
		if (*propID == "" && *reqID == "") || (*propID != "" && *reqID != "") {
			return nil, invalid("exactly one of --proposal or --request-id is required")
		}
		if *collection == "" {
			*collection = store.Channel().Repo
			if *collection == "" {
				*collection = "default"
			}
		}

		var job *core.WorkAdviceJob
		var prop *core.WorkAdviceProposal
		var actions []*core.WorkAdviceAction

		if *propID != "" {
			pUUID, err := uuid.Parse(*propID)
			if err != nil || pUUID == uuid.Nil {
				return nil, invalid("proposal must be a valid non-nil UUID")
			}
			prop, actions, err = store.GetAdviceProposal(ctx, pUUID)
			if err != nil {
				return nil, err
			}
			job, err = store.GetAdviceJob(ctx, prop.JobUUID)
			if err != nil {
				return nil, err
			}
		} else {
			rUUID, err := uuid.Parse(*reqID)
			if err != nil || rUUID == uuid.Nil {
				return nil, invalid("request-id must be a valid non-nil UUID")
			}
			job, err = store.GetAdviceJobByRequestUUID(ctx, rUUID)
			if err != nil {
				return nil, err
			}
			prop, actions, _ = store.GetAdviceProposalByJobUUID(ctx, job.JobUUID)
		}

		env, err := buildAdviceReviewEnvelope(ctx, store, *collection, job.BindingID, job.PlaneItemUUID, prop, job, actions)
		if err != nil {
			return nil, err
		}
		return env, nil

	case "apply":
		f := flags("work advice apply")
		propID := f.String("proposal", "", "proposal UUID")
		version := f.Int64("version", 0, "proposal review version")
		requestID := f.String("request-id", "", "idempotency request ID")
		_ = f.String("collection", "", "collection name")

		var actionIDs adviceSliceValue
		f.Var(&actionIDs, "action", "action UUID to apply (repeatable, defaults to all pending)")

		if err := f.Parse(args[1:]); err != nil {
			return nil, invalid(err.Error())
		}
		if *propID == "" {
			return nil, invalid("--proposal is required")
		}
		if *version <= 0 {
			return nil, invalid("--version is required and must be positive")
		}
		pUUID, err := uuid.Parse(*propID)
		if err != nil || pUUID == uuid.Nil {
			return nil, invalid("proposal must be a valid non-nil UUID")
		}

		var targetActionUUIDs []uuid.UUID
		if len(actionIDs) > 0 {
			for _, raw := range actionIDs {
				u, err := uuid.Parse(raw)
				if err != nil || u == uuid.Nil {
					return nil, invalid(fmt.Sprintf("invalid action UUID %q", raw))
				}
				targetActionUUIDs = append(targetActionUUIDs, u)
			}
		} else {
			_, actions, err := store.GetAdviceProposal(ctx, pUUID)
			if err != nil {
				return nil, err
			}
			for _, act := range actions {
				if act.Status == "pending" {
					targetActionUUIDs = append(targetActionUUIDs, act.ActionUUID)
				}
			}
		}

		reqUUID := uuid.New()
		if *requestID != "" {
			u, err := uuid.Parse(*requestID)
			if err != nil || u == uuid.Nil {
				return nil, invalid("request-id must be a valid non-nil UUID")
			}
			reqUUID = u
		}

		receipt := &core.WorkAdviceApplicationReceipt{
			RequestUUID:  reqUUID,
			ProposalUUID: pUUID,
		}

		appliedReceipt, err := store.ApplyAdviceProposal(ctx, receipt, *version, targetActionUUIDs)
		if err != nil {
			return nil, err
		}
		return map[string]any{"receipt": appliedReceipt}, nil

	case "reject":
		f := flags("work advice reject")
		propID := f.String("proposal", "", "proposal UUID")
		version := f.Int64("version", 0, "proposal review version")
		_ = f.String("collection", "", "collection name")
		if err := f.Parse(args[1:]); err != nil {
			return nil, invalid(err.Error())
		}
		if *propID == "" {
			return nil, invalid("--proposal is required")
		}
		if *version <= 0 {
			return nil, invalid("--version is required and must be positive")
		}
		pUUID, err := uuid.Parse(*propID)
		if err != nil || pUUID == uuid.Nil {
			return nil, invalid("proposal must be a valid non-nil UUID")
		}

		if err := store.RejectAdviceProposal(ctx, pUUID, *version); err != nil {
			return nil, err
		}
		return map[string]any{"ok": true}, nil

	case "undo":
		f := flags("work advice undo")
		receiptID := f.String("receipt", "", "receipt UUID or request UUID")
		requestID := f.String("request-id", "", "request UUID (alias for receipt)")
		_ = f.String("collection", "", "collection name")
		if err := f.Parse(args[1:]); err != nil {
			return nil, invalid(err.Error())
		}
		target := *receiptID
		if target == "" {
			target = *requestID
		}
		if target == "" {
			return nil, invalid("either --receipt or --request-id is required")
		}
		targetUUID, err := uuid.Parse(target)
		if err != nil || targetUUID == uuid.Nil {
			return nil, invalid("receipt / request-id must be a valid non-nil UUID")
		}

		var r *core.WorkAdviceApplicationReceipt
		r, err = store.GetAdviceReceipt(ctx, targetUUID)
		if err != nil {
			r, err = store.GetAdviceReceiptByUUID(ctx, targetUUID)
		}
		if err != nil {
			return nil, err
		}
		if err := store.UndoAdviceApplication(ctx, r.RequestUUID); err != nil {
			return nil, err
		}
		updated, err := store.GetAdviceReceipt(ctx, r.RequestUUID)
		if err != nil {
			return nil, err
		}
		return map[string]any{"receipt": updated}, nil

	case "profile":
		if len(args) < 2 || hasWorkHelpArg(args[1:]) {
			return commandHelp("cairn work advice profile (register|list|show) [FLAGS]\n"), nil
		}
		switch args[1] {
		case "register":
			f := flags("work advice profile register")
			name := f.String("profile", "", "profile name")
			route := f.String("route", advice.RouteCodexSubscription, "route name")
			executable := f.String("executable", "", "executable path")
			binarySHA := f.String("binary-sha256", "", "binary sha256 hash")
			commit := f.String("expected-upstream-commit", advice.RequiredCodexUpstreamCommit, "expected upstream commit")
			patchSHA := f.String("expected-patch-sha256", "", "expected patch sha256")
			codexHome := f.String("codex-home", "", "codex home directory")
			model := f.String("model", "", "model name")
			timeoutMs := f.Int64("timeout-ms", advice.DefaultCodexTimeoutMs, "timeout in milliseconds")
			maxBytes := f.Int64("max-output-bytes", advice.DefaultCodexMaxBytes, "max output bytes")
			if err := f.Parse(args[2:]); err != nil {
				return nil, invalid(err.Error())
			}
			if *name == "" {
				return nil, invalid("--profile is required")
			}
			if *executable == "" || *binarySHA == "" || *codexHome == "" || *model == "" {
				return nil, invalid("--executable, --binary-sha256, --codex-home, and --model are required")
			}

			prof := &advice.Profile{
				Name:                   *name,
				Route:                  *route,
				ExecutablePath:         *executable,
				BinarySHA256:           *binarySHA,
				ExpectedUpstreamCommit: *commit,
				ExpectedPatchSHA256:    *patchSHA,
				CodexHome:              *codexHome,
				Model:                  *model,
				TimeoutMs:              *timeoutMs,
				MaxOutputBytes:         *maxBytes,
			}
			if err := store.RegisterAdviceProfile(ctx, prof); err != nil {
				return nil, err
			}
			return map[string]any{"profile": prof}, nil

		case "list":
			profiles, err := store.ListAdviceProfiles(ctx)
			if err != nil {
				return nil, err
			}
			if profiles == nil {
				profiles = make([]*advice.Profile, 0)
			}
			return map[string]any{"profiles": profiles}, nil

		case "show":
			f := flags("work advice profile show")
			name := f.String("profile", "", "profile name")
			if err := f.Parse(args[2:]); err != nil {
				return nil, invalid(err.Error())
			}
			if *name == "" {
				return nil, invalid("--profile is required")
			}
			prof, err := store.GetAdviceProfile(ctx, *name)
			if err != nil {
				return nil, err
			}
			return map[string]any{"profile": prof}, nil

		default:
			return nil, invalid(fmt.Sprintf("unknown work advice profile subcommand %q", args[1]))
		}

	case "policy":
		if len(args) < 2 || hasWorkHelpArg(args[1:]) {
			return commandHelp("cairn work advice policy (get|show|set) [FLAGS]\n"), nil
		}
		switch args[1] {
		case "get", "show":
			f := flags("work advice policy " + args[1])
			slug := f.String("project", "", "project slug")
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

			proj, err := store.GetWorkProject(ctx, *collection, *slug)
			if err != nil {
				return nil, err
			}
			pol, err := store.GetWorkAdvicePolicyDetail(ctx, proj.BindingID)
			if err != nil {
				return nil, err
			}
			return map[string]any{
				"binding_id":      proj.BindingID,
				"policy_revision": pol.PolicyRevision,
				"allowed_fields":  pol.AllowedFields,
			}, nil

		case "set":
			f := flags("work advice policy set")
			slug := f.String("project", "", "project slug")
			rawFields := f.String("fields", "", "comma-separated allowed fields (identifier,title,state,blockers,artifacts,sources)")
			collection := f.String("collection", "", "collection name")
			if err := f.Parse(args[2:]); err != nil {
				return nil, invalid(err.Error())
			}
			if *slug == "" {
				return nil, invalid("--project is required")
			}
			if *rawFields == "" {
				return nil, invalid("--fields is required")
			}
			if *collection == "" {
				*collection = store.Channel().Repo
				if *collection == "" {
					*collection = "default"
				}
			}

			proj, err := store.GetWorkProject(ctx, *collection, *slug)
			if err != nil {
				return nil, err
			}
			var fieldsList []string
			for _, s := range strings.Split(*rawFields, ",") {
				trimmed := strings.TrimSpace(s)
				if trimmed != "" {
					fieldsList = append(fieldsList, trimmed)
				}
			}
			if err := store.SetWorkAdvicePolicy(ctx, proj.BindingID, fieldsList); err != nil {
				return nil, err
			}
			pol, _ := store.GetWorkAdvicePolicyDetail(ctx, proj.BindingID)
			var rev int64
			var currentFields []string
			if pol != nil {
				rev = pol.PolicyRevision
				currentFields = pol.AllowedFields
			}
			return map[string]any{
				"ok":              true,
				"binding_id":      proj.BindingID,
				"policy_revision": rev,
				"allowed_fields":  currentFields,
			}, nil

		default:
			return nil, invalid(fmt.Sprintf("unknown work advice policy subcommand %q", args[1]))
		}

	case "control":
		if len(args) < 2 || hasWorkHelpArg(args[1:]) {
			return commandHelp("cairn work advice control (status|get|set) [--generation true|false] [--apply true|false]\n"), nil
		}
		switch args[1] {
		case "status", "get":
			ctrl, err := store.GetAdviceControl(ctx)
			if err != nil {
				return nil, err
			}
			st, err := store.GetWorkFeatureStatus(ctx, "work-advice/1")
			if err != nil {
				return nil, err
			}
			return map[string]any{
				"feature_status":     st,
				"generation_enabled": ctrl.GenerationEnabled,
				"apply_enabled":      ctrl.ApplyEnabled,
				"updated_at":         ctrl.UpdatedAt.UTC().Format(time.RFC3339),
			}, nil
		case "set":
			ctrl, err := store.GetAdviceControl(ctx)
			if err != nil {
				return nil, err
			}
			f := flags("work advice control set")
			gen := f.Bool("generation", ctrl.GenerationEnabled, "enable or disable advice generation")
			app := f.Bool("apply", ctrl.ApplyEnabled, "enable or disable advice apply")
			if err := f.Parse(args[2:]); err != nil {
				return nil, invalid(err.Error())
			}
			var hasGen, hasApp bool
			f.Visit(func(fl *flag.Flag) {
				if fl.Name == "generation" {
					hasGen = true
				}
				if fl.Name == "apply" {
					hasApp = true
				}
			})
			if !hasGen && !hasApp {
				return nil, invalid("at least one of --generation or --apply is required")
			}
			finalGen := ctrl.GenerationEnabled
			if hasGen {
				finalGen = *gen
			}
			finalApp := ctrl.ApplyEnabled
			if hasApp {
				finalApp = *app
			}
			updated, err := store.SetAdviceControl(ctx, finalGen, finalApp)
			if err != nil {
				return nil, err
			}
			st, _ := store.GetWorkFeatureStatus(ctx, "work-advice/1")
			return map[string]any{
				"feature_status":     st,
				"generation_enabled": updated.GenerationEnabled,
				"apply_enabled":      updated.ApplyEnabled,
				"updated_at":         updated.UpdatedAt.UTC().Format(time.RFC3339),
			}, nil
		default:
			return nil, invalid(fmt.Sprintf("unknown work advice control subcommand %q", args[1]))
		}

	case "reconcile":
		reconciled, err := store.ReconcileAdviceJobs(ctx)
		if err != nil {
			return nil, err
		}
		if reconciled == nil {
			reconciled = make([]core.ReconciledAdviceJob, 0)
		}
		swept, err := store.SweepExpiredAdvice(ctx)
		if err != nil {
			return nil, err
		}
		return map[string]any{
			"reconciled": reconciled,
			"swept":      swept,
		}, nil

	case "sweep":
		swept, err := store.SweepExpiredAdvice(ctx)
		if err != nil {
			return nil, err
		}
		return map[string]any{"swept": swept}, nil

	default:
		return nil, invalid(fmt.Sprintf("unknown work advice subcommand %q", sub))
	}
}
