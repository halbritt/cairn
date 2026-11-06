package main

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"time"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
	"github.com/halbritt/cairn/internal/advice"
	"github.com/halbritt/cairn/internal/refusal"
)

func writeWorkAdviceHTTPError(w http.ResponseWriter, err error) {
	code := core.Code(err)
	status := refusal.Classify(code).HTTPStatus
	if status == 0 {
		status = http.StatusInternalServerError
	}
	w.Header().Set("Cache-Control", "no-store")
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(map[string]any{
		"error": err.Error(),
		"code":  code,
	})
}

func decodeStrictAdviceJSON[T any](r *http.Request, target *T) error {
	limited := http.MaxBytesReader(nil, r.Body, 64*1024)
	dec := json.NewDecoder(limited)
	dec.DisallowUnknownFields()
	if err := dec.Decode(target); err != nil {
		return &core.Error{Code: "INVALID_REQUEST", Message: fmt.Sprintf("invalid json payload: %v", err)}
	}
	var extra any
	if err := dec.Decode(&extra); !errors.Is(err, io.EOF) {
		return &core.Error{Code: "INVALID_REQUEST", Message: "unexpected trailing tokens in json body"}
	}
	return nil
}

type adviceGenerateBody struct {
	Project              string               `json:"project"`
	Item                 string               `json:"item"`
	Profile              string               `json:"profile,omitempty"`
	Model                string               `json:"model,omitempty"`
	Timeout              int                  `json:"timeout,omitempty"`
	RequestID            string               `json:"request_id,omitempty"`
	CandidateRevisionIDs []uuid.UUID          `json:"candidate_revision_ids,omitempty"`
	Sources              []core.SourceRefSpec `json:"sources,omitempty"`
}

type adviceApplyBody struct {
	ProposalUUID uuid.UUID   `json:"proposal_uuid"`
	Version      int64       `json:"version"`
	Actions      []uuid.UUID `json:"actions,omitempty"`
	RequestID    string      `json:"request_id,omitempty"`
}

type adviceRejectBody struct {
	ProposalUUID uuid.UUID `json:"proposal_uuid"`
	Version      int64     `json:"version"`
	RequestID    string    `json:"request_id,omitempty"`
}

type adviceUndoBody struct {
	RequestUUID uuid.UUID `json:"request_uuid"`
	RequestID   string    `json:"request_id,omitempty"`
}

type adviceProfileRegisterBody struct {
	Profile                string `json:"profile"`
	Route                  string `json:"route,omitempty"`
	Executable             string `json:"executable"`
	BinarySHA256           string `json:"binary_sha256"`
	ExpectedUpstreamCommit string `json:"expected_upstream_commit,omitempty"`
	ExpectedPatchSHA256    string `json:"expected_patch_sha256,omitempty"`
	CodexHome              string `json:"codex_home"`
	Model                  string `json:"model"`
	TimeoutMs              int64  `json:"timeout_ms,omitempty"`
	MaxOutputBytes         int64  `json:"max_output_bytes,omitempty"`
	RequestID              string `json:"request_id,omitempty"`
}

type advicePolicySetBody struct {
	Project   string   `json:"project"`
	Fields    []string `json:"fields"`
	RequestID string   `json:"request_id,omitempty"`
}

type adviceControlBody struct {
	GenerationEnabled *bool  `json:"generation_enabled"`
	ApplyEnabled      *bool  `json:"apply_enabled"`
	RequestID         string `json:"request_id,omitempty"`
}

type adviceReconcileBody struct {
	RequestID string `json:"request_id,omitempty"`
}

type WorkAdviceItemContext struct {
	Collection    string    `json:"collection"`
	BindingID     uuid.UUID `json:"binding_id"`
	PlaneItemUUID uuid.UUID `json:"plane_item_uuid"`
	ObservedAt    string    `json:"observed_at"`
}

type WorkAdviceAvailability struct {
	GenerationAllowed bool     `json:"generation_allowed"`
	ApplyAllowed      bool     `json:"apply_allowed"`
	PolicyEditAllowed bool     `json:"policy_edit_allowed"`
	RejectAllowed     bool     `json:"reject_allowed"`
	ReasonCodes       []string `json:"reason_codes"`
}

type ReviewedJob struct {
	core.WorkAdviceJob
	Route                      string `json:"route"`
	Model                      string `json:"model"`
	ProfileConfigurationDigest string `json:"profile_configuration_digest"`
	CapturedAt                 string `json:"captured_at"`
}

type ReviewedManifestSource struct {
	RecordID   uuid.UUID `json:"record_id"`
	Version    int       `json:"version"`
	BodySHA256 string    `json:"body_sha256"`
	ObservedAt string    `json:"observed_at"`
	Eligible   bool      `json:"eligible"`
}

type ReviewedCandidateRevision struct {
	RevisionID       uuid.UUID `json:"revision_id"`
	ArtifactID       uuid.UUID `json:"artifact_id"`
	Kind             string    `json:"kind"`
	DisplayName      string    `json:"display_name"`
	PayloadAvailable bool      `json:"payload_available"`
}

type ReviewedManifest struct {
	PolicyRevision     int64                       `json:"policy_revision"`
	AllowedFields      []string                    `json:"allowed_fields"`
	LastRefreshedAt    string                      `json:"last_refreshed_at"`
	RestoreGeneration  int64                       `json:"restore_generation"`
	Sources            []ReviewedManifestSource    `json:"sources"`
	CandidateRevisions []ReviewedCandidateRevision `json:"candidate_revisions"`
}

type ReviewedInput struct {
	PayloadAvailable bool             `json:"payload_available"`
	ExpiresAt        string           `json:"expires_at"`
	Snapshot         *advice.Snapshot `json:"snapshot"`
	ReasonCode       *string          `json:"reason_code,omitempty"`
}

type CurrentnessReason struct {
	Code    string `json:"code"`
	Message string `json:"message"`
}

type ReviewedCurrentness struct {
	State     string              `json:"state"`
	CheckedAt string              `json:"checked_at"`
	Reasons   []CurrentnessReason `json:"reasons"`
}

type ActionAttribution struct {
	Requester      string `json:"requester"`
	ModelAssisted  bool   `json:"model_assisted"`
	AcceptingActor string `json:"accepting_actor"`
}

type ActionSourceRef struct {
	RecordID   uuid.UUID `json:"record_id"`
	Version    int       `json:"version"`
	BodySHA256 string    `json:"body_sha256"`
	ObservedAt string    `json:"observed_at"`
}

type ActionReview struct {
	Before           any               `json:"before"`
	After            any               `json:"after"`
	SourceReferences []ActionSourceRef `json:"source_references"`
	Attribution      ActionAttribution `json:"attribution"`
}

type ReviewedAction struct {
	core.WorkAdviceAction
	Review ActionReview `json:"review"`
}

type ApplicationUndoStatus struct {
	Allowed    bool   `json:"allowed"`
	CheckedAt  string `json:"checked_at"`
	ReasonCode string `json:"reason_code"`
}

type ReviewedApplication struct {
	core.WorkAdviceApplicationReceipt
	Undo ApplicationUndoStatus `json:"undo"`
}

type WorkAdviceShowEnvelope struct {
	Context           WorkAdviceItemContext    `json:"context"`
	Availability      WorkAdviceAvailability   `json:"availability"`
	FeatureStatus     string                   `json:"feature_status"`
	GenerationEnabled bool                     `json:"generation_enabled"`
	ApplyEnabled      bool                     `json:"apply_enabled"`
	Proposal          *core.WorkAdviceProposal `json:"proposal"`
	Job               *ReviewedJob             `json:"job"`
	Manifest          *ReviewedManifest        `json:"manifest"`
	Input             ReviewedInput            `json:"input"`
	Currentness       ReviewedCurrentness      `json:"currentness"`
	Actions           []ReviewedAction         `json:"actions"`
	Applications      []ReviewedApplication    `json:"applications"`
}

func computeAvailability(st string, ctrl *core.WorkAdviceControl, prop *core.WorkAdviceProposal) WorkAdviceAvailability {
	genEnabled := true
	appEnabled := true
	if ctrl != nil {
		genEnabled = ctrl.GenerationEnabled
		appEnabled = ctrl.ApplyEnabled
	}

	var reasonCodes []string
	genAllowed := (st == "active") && genEnabled
	if !genAllowed {
		if st != "active" {
			reasonCodes = append(reasonCodes, "FEATURE_DISABLED")
		} else {
			reasonCodes = append(reasonCodes, "GENERATION_DISABLED")
		}
	}
	appAllowed := (st == "active") && appEnabled
	if !appAllowed && st == "active" && !appEnabled {
		reasonCodes = append(reasonCodes, "APPLY_DISABLED")
	}
	rejectAllowed := (st == "active") && (prop != nil && (prop.Status == "pending" || prop.Status == "partially_applied"))
	if reasonCodes == nil {
		reasonCodes = make([]string, 0)
	}

	return WorkAdviceAvailability{
		GenerationAllowed: genAllowed,
		ApplyAllowed:      appAllowed,
		PolicyEditAllowed: true,
		RejectAllowed:     rejectAllowed,
		ReasonCodes:       reasonCodes,
	}
}

func buildReviewedJob(ctx context.Context, store *core.Store, job *core.WorkAdviceJob) *ReviewedJob {
	jobCopy := *job
	jobCopy.InputSnapshot = nil
	jobCopy.ProcessIdentity = nil
	jobReview := &ReviewedJob{
		WorkAdviceJob: jobCopy,
		Route:         job.ModelRoute,
		Model:         job.ModelRoute,
		CapturedAt:    job.CreatedAt.UTC().Format(time.RFC3339),
	}
	prof, _ := store.GetAdviceProfile(ctx, job.ProfileName)
	if prof != nil {
		jobReview.Route = prof.Route
		jobReview.Model = prof.Model
		raw := fmt.Sprintf("%s:%s:%s:%s:%s", prof.Route, prof.ExecutablePath, prof.BinarySHA256, prof.Model, prof.CodexHome)
		h := sha256.Sum256([]byte(raw))
		jobReview.ProfileConfigurationDigest = hex.EncodeToString(h[:])
	}
	return jobReview
}

func buildReviewedManifest(ctx context.Context, store *core.Store, job *core.WorkAdviceJob, nowRFC string) *ReviewedManifest {
	if len(job.InputManifest) == 0 {
		return nil
	}
	var rawMan core.WorkAdviceInputManifest
	if err := json.Unmarshal(job.InputManifest, &rawMan); err != nil {
		return nil
	}
	manifest := &ReviewedManifest{
		PolicyRevision:     rawMan.PolicyRevision,
		AllowedFields:      rawMan.AllowedFields,
		LastRefreshedAt:    rawMan.LastRefreshedAt.UTC().Format(time.RFC3339),
		RestoreGeneration:  rawMan.RestoreGeneration,
		Sources:            make([]ReviewedManifestSource, 0, len(rawMan.Sources)),
		CandidateRevisions: make([]ReviewedCandidateRevision, 0, len(rawMan.CandidateRevisionIDs)),
	}
	for _, s := range rawMan.Sources {
		eligible := true
		if store != nil {
			eligible = store.CheckSourceEligibleLive(ctx, s.RecordID, s.Version)
		}
		manifest.Sources = append(manifest.Sources, ReviewedManifestSource{
			RecordID:   s.RecordID,
			Version:    s.Version,
			BodySHA256: s.BodySHA256,
			ObservedAt: nowRFC,
			Eligible:   eligible,
		})
	}
	for _, c := range rawMan.CandidateRevisionIDs {
		artID := uuid.Nil
		kind := "artifact"
		displayName := c.String()[:8]
		payloadAvailable := true
		if store != nil {
			if aID, k, dn, pa, err := store.GetArtifactRevisionDetail(ctx, c); err == nil {
				artID = aID
				kind = k
				displayName = dn
				payloadAvailable = pa
			}
		}
		manifest.CandidateRevisions = append(manifest.CandidateRevisions, ReviewedCandidateRevision{
			RevisionID:       c,
			ArtifactID:       artID,
			Kind:             kind,
			DisplayName:      displayName,
			PayloadAvailable: payloadAvailable,
		})
	}
	return manifest
}

func buildReviewedInput(job *core.WorkAdviceJob, now time.Time) ReviewedInput {
	input := ReviewedInput{
		PayloadAvailable: false,
		ExpiresAt:        job.InputExpiresAt.UTC().Format(time.RFC3339),
		Snapshot:         nil,
	}
	if job.InputUnavailableReason != nil && *job.InputUnavailableReason != "" {
		input.ReasonCode = job.InputUnavailableReason
	} else if now.After(job.InputExpiresAt) {
		r := "EXPIRED"
		input.ReasonCode = &r
	} else if len(job.InputSnapshot) == 0 {
		r := "UNAVAILABLE"
		input.ReasonCode = &r
	} else {
		var snap advice.Snapshot
		if err := json.Unmarshal(job.InputSnapshot, &snap); err == nil {
			input.PayloadAvailable = true
			input.Snapshot = &snap
		} else {
			r := "UNAVAILABLE"
			input.ReasonCode = &r
		}
	}
	return input
}

func buildReviewedCurrentness(ctx context.Context, store *core.Store, prop *core.WorkAdviceProposal, job *core.WorkAdviceJob, input ReviewedInput, manifest *ReviewedManifest, now time.Time, nowRFC string) ReviewedCurrentness {
	var reasons []CurrentnessReason
	state := ""

	// 1. Purged precedence
	if (input.ReasonCode != nil && *input.ReasonCode == "PURGED") || (job != nil && job.InputUnavailableReason != nil && *job.InputUnavailableReason == "PURGED") {
		state = "purged"
		reasons = append(reasons, CurrentnessReason{Code: "INPUT_PURGED", Message: "input payload purged"})
	} else if manifest != nil {
		for _, s := range manifest.Sources {
			if !s.Eligible {
				state = "purged"
				reasons = append(reasons, CurrentnessReason{Code: "SOURCE_PURGED", Message: fmt.Sprintf("source record %s was purged or became ineligible", s.RecordID)})
				break
			}
		}
	}

	// 2. Unavailable
	if state == "" && prop == nil {
		state = "unavailable"
		reasons = append(reasons, CurrentnessReason{Code: "NO_PROPOSAL", Message: "no proposal produced"})
	}

	// 3. Expired
	if state == "" && prop != nil && now.After(prop.ExpiresAt) {
		state = "expired"
		reasons = append(reasons, CurrentnessReason{Code: "PROPOSAL_EXPIRED", Message: "proposal expired"})
	}

	// 4. Substantive live check (VerifyAdviceCurrentness)
	if state == "" && prop != nil && store != nil {
		if err := store.VerifyAdviceCurrentness(ctx, prop.ProposalUUID); err != nil {
			state = "stale"
			reasons = append(reasons, CurrentnessReason{Code: "SUBSTANTIVE_CHANGE", Message: err.Error()})
		}
	}

	// 5. Settled / Rejected check (do NOT mark partially_applied as stale!)
	if state == "" && prop != nil {
		if prop.Status == "rejected" {
			state = "stale"
			reasons = append(reasons, CurrentnessReason{Code: "PROPOSAL_REJECTED", Message: "proposal was rejected"})
		} else {
			state = "current"
		}
	}

	if state == "" {
		state = "current"
	}
	if reasons == nil {
		reasons = make([]CurrentnessReason, 0)
	}

	return ReviewedCurrentness{
		State:     state,
		CheckedAt: nowRFC,
		Reasons:   reasons,
	}
}

func buildReviewedActions(job *core.WorkAdviceJob, actions []*core.WorkAdviceAction, snapshot *advice.Snapshot, channelPrincipal string) []ReviewedAction {
	reviewedActions := make([]ReviewedAction, 0, len(actions))
	requester := "operator"
	if job != nil && job.Caller != "" {
		requester = job.Caller
	}
	acceptingActor := channelPrincipal
	if acceptingActor == "" {
		acceptingActor = "operator"
	}

	for _, act := range actions {
		var before any
		var after any
		srcRefs := make([]ActionSourceRef, 0)

		switch act.ActionType {
		case "annotate_progress":
			var p struct {
				Text             string   `json:"text"`
				SourceReferences []string `json:"source_references"`
			}
			_ = json.Unmarshal(act.TargetPayload, &p)
			after = map[string]any{
				"kind":  "progress",
				"body":  p.Text,
				"state": "active",
			}
			for _, ref := range p.SourceReferences {
				if u, err := uuid.Parse(ref); err == nil {
					srcRefs = append(srcRefs, ActionSourceRef{RecordID: u, Version: 1})
				}
			}
		case "annotate_blocker":
			var b struct {
				BlockerAction     string   `json:"blocker_action"`
				Text              string   `json:"text"`
				ExistingBlockerID string   `json:"existing_blocker_id"`
				ExpectedRevision  int64    `json:"expected_revision"`
				SourceReferences  []string `json:"source_references"`
			}
			_ = json.Unmarshal(act.TargetPayload, &b)
			if b.BlockerAction == "resolve" {
				priorText := b.Text
				if snapshot != nil {
					for _, eb := range snapshot.ExistingBlockers {
						if eb.BlockerID == b.ExistingBlockerID {
							priorText = eb.Text
							break
						}
					}
				}
				before = map[string]any{
					"annotation_id": b.ExistingBlockerID,
					"revision":      b.ExpectedRevision,
					"kind":          "blocker",
					"body":          priorText,
					"state":         "open",
				}
				afterBody := b.Text
				if afterBody == "" {
					afterBody = priorText
				}
				after = map[string]any{
					"annotation_id": b.ExistingBlockerID,
					"revision":      b.ExpectedRevision + 1,
					"kind":          "blocker",
					"body":          afterBody,
					"state":         "resolved",
				}
			} else {
				after = map[string]any{
					"kind":  "blocker",
					"body":  b.Text,
					"state": "open",
				}
			}
			for _, ref := range b.SourceReferences {
				if u, err := uuid.Parse(ref); err == nil {
					srcRefs = append(srcRefs, ActionSourceRef{RecordID: u, Version: 1})
				}
			}
		case "link_artifact":
			var l struct {
				ArtifactRevisionID string `json:"artifact_revision_id"`
				Role               string `json:"role"`
			}
			_ = json.Unmarshal(act.TargetPayload, &l)
			after = map[string]any{
				"revision_id": l.ArtifactRevisionID,
				"role":        l.Role,
				"state":       "active",
			}
		}

		reviewedActions = append(reviewedActions, ReviewedAction{
			WorkAdviceAction: *act,
			Review: ActionReview{
				Before:           before,
				After:            after,
				SourceReferences: srcRefs,
				Attribution: ActionAttribution{
					Requester:      requester,
					ModelAssisted:  true,
					AcceptingActor: acceptingActor,
				},
			},
		})
	}
	return reviewedActions
}

func buildReviewedApplications(ctx context.Context, store *core.Store, prop *core.WorkAdviceProposal, nowRFC string) []ReviewedApplication {
	reviewedApps := make([]ReviewedApplication, 0)
	if prop != nil && store != nil {
		apps, _ := store.ListAdviceApplications(ctx, prop.ProposalUUID)
		for _, app := range apps {
			allowed := !app.Undone
			reason := ""
			if app.Undone {
				reason = "ALREADY_UNDONE"
			} else {
				if err := store.CheckReceiptUndoable(ctx, app.RequestUUID); err != nil {
					allowed = false
					reason = "UNDO_CONFLICT"
				}
			}
			reviewedApps = append(reviewedApps, ReviewedApplication{
				WorkAdviceApplicationReceipt: *app,
				Undo: ApplicationUndoStatus{
					Allowed:    allowed,
					CheckedAt:  nowRFC,
					ReasonCode: reason,
				},
			})
		}
	}
	return reviewedApps
}

func buildAdviceReviewEnvelope(
	ctx context.Context,
	store *core.Store,
	collection string,
	bindingID uuid.UUID,
	itemUUID uuid.UUID,
	prop *core.WorkAdviceProposal,
	job *core.WorkAdviceJob,
	actions []*core.WorkAdviceAction,
) (*WorkAdviceShowEnvelope, error) {
	now := time.Now().UTC()
	nowRFC := now.Format(time.RFC3339)

	itemContext := WorkAdviceItemContext{
		Collection:    collection,
		BindingID:     bindingID,
		PlaneItemUUID: itemUUID,
		ObservedAt:    nowRFC,
	}

	st, _ := store.GetWorkFeatureStatus(ctx, "work-advice/1")
	ctrl, _ := store.GetAdviceControl(ctx)
	genEnabled := true
	appEnabled := true
	if ctrl != nil {
		genEnabled = ctrl.GenerationEnabled
		appEnabled = ctrl.ApplyEnabled
	}

	availability := computeAvailability(st, ctrl, prop)
	jobReview := buildReviewedJob(ctx, store, job)
	manifest := buildReviewedManifest(ctx, store, job, nowRFC)
	input := buildReviewedInput(job, now)
	currentness := buildReviewedCurrentness(ctx, store, prop, job, input, manifest, now, nowRFC)
	reviewedActions := buildReviewedActions(job, actions, input.Snapshot, store.Channel().Principal)
	reviewedApps := buildReviewedApplications(ctx, store, prop, nowRFC)

	return &WorkAdviceShowEnvelope{
		Context:           itemContext,
		Availability:      availability,
		FeatureStatus:     st,
		GenerationEnabled: genEnabled,
		ApplyEnabled:      appEnabled,
		Proposal:          prop,
		Job:               jobReview,
		Manifest:          manifest,
		Input:             input,
		Currentness:       currentness,
		Actions:           reviewedActions,
		Applications:      reviewedApps,
	}, nil
}

// HandleWorkAdviceAPI dispatches /api/work/advice/* routes.
func HandleWorkAdviceAPI(store *core.Store, collection string, w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	if store == nil {
		http.Error(w, `{"error":"store unavailable"}`, http.StatusServiceUnavailable)
		return
	}

	st, err := store.GetWorkFeatureStatus(ctx, "work-advice/1")
	if err != nil {
		writeWorkAdviceHTTPError(w, err)
		return
	}
	w.Header().Set("Cache-Control", "no-store")
	w.Header().Set("Content-Type", "application/json; charset=utf-8")

	switch {
	case r.URL.Path == "/api/work/advice/control" && r.Method == http.MethodGet:
		ctrl, err := store.GetAdviceControl(ctx)
		if err != nil {
			writeWorkAdviceHTTPError(w, err)
			return
		}
		_ = json.NewEncoder(w).Encode(map[string]any{
			"feature_status":     st,
			"generation_enabled": ctrl.GenerationEnabled,
			"apply_enabled":      ctrl.ApplyEnabled,
			"updated_at":         ctrl.UpdatedAt.UTC().Format(time.RFC3339),
		})

	case r.URL.Path == "/api/work/advice/control" && r.Method == http.MethodPost:
		var body adviceControlBody
		if err := decodeStrictAdviceJSON(r, &body); err != nil {
			writeWorkAdviceHTTPError(w, err)
			return
		}
		ctrl, err := store.GetAdviceControl(ctx)
		if err != nil {
			writeWorkAdviceHTTPError(w, err)
			return
		}
		gen := ctrl.GenerationEnabled
		app := ctrl.ApplyEnabled
		if body.GenerationEnabled != nil {
			gen = *body.GenerationEnabled
		}
		if body.ApplyEnabled != nil {
			app = *body.ApplyEnabled
		}
		updated, err := store.SetAdviceControl(ctx, gen, app)
		if err != nil {
			writeWorkAdviceHTTPError(w, err)
			return
		}
		_ = json.NewEncoder(w).Encode(map[string]any{
			"feature_status":     st,
			"generation_enabled": updated.GenerationEnabled,
			"apply_enabled":      updated.ApplyEnabled,
			"updated_at":         updated.UpdatedAt.UTC().Format(time.RFC3339),
		})

	case r.URL.Path == "/api/work/advice/generate" && r.Method == http.MethodPost:
		var body adviceGenerateBody
		if err := decodeStrictAdviceJSON(r, &body); err != nil {
			writeWorkAdviceHTTPError(w, err)
			return
		}
		if body.Project == "" || body.Item == "" {
			writeWorkAdviceHTTPError(w, &core.Error{Code: "INVALID_REQUEST", Message: "project and item are required"})
			return
		}
		if body.Profile == "" {
			writeWorkAdviceHTTPError(w, &core.Error{Code: "INVALID_REQUEST", Message: "profile is required"})
			return
		}
		if st != "active" {
			writeWorkAdviceHTTPError(w, &core.Error{Code: "FEATURE_DISABLED", Message: "work-advice/1 feature is disabled"})
			return
		}
		ctrl, err := store.GetAdviceControl(ctx)
		if err != nil {
			writeWorkAdviceHTTPError(w, err)
			return
		}
		if !ctrl.GenerationEnabled {
			writeWorkAdviceHTTPError(w, &core.Error{Code: "FEATURE_DISABLED", Message: "work advice generation is disabled"})
			return
		}
		proj, err := store.GetWorkProject(ctx, collection, body.Project)
		if err != nil {
			writeWorkAdviceHTTPError(w, err)
			return
		}
		itemUUID, err := store.ResolveWorkItemUUID(ctx, collection, body.Project, body.Item)
		if err != nil {
			writeWorkAdviceHTTPError(w, err)
			return
		}

		reqUUID := uuid.New()
		if body.RequestID != "" {
			u, err := uuid.Parse(body.RequestID)
			if err != nil || u == uuid.Nil {
				writeWorkAdviceHTTPError(w, &core.Error{Code: "INVALID_REQUEST", Message: "request_id must be a valid non-nil UUID"})
				return
			}
			reqUUID = u
		}

		timeout := body.Timeout
		if timeout <= 0 {
			timeout = 90
		}

		job := &core.WorkAdviceJob{
			BindingID:       proj.BindingID,
			PlaneItemUUID:   itemUUID,
			ProfileName:     body.Profile,
			ModelRoute:      body.Model,
			DeadlineSeconds: timeout,
			RequestUUID:     reqUUID,
		}

		spec := core.AdviceInputSpec{
			BindingID:            proj.BindingID,
			PlaneItemUUID:        itemUUID,
			CandidateRevisionIDs: body.CandidateRevisionIDs,
			SelectedSources:      body.Sources,
		}

		prop, actions, err := store.GenerateWorkAdvice(ctx, job, spec, nil)
		if err != nil {
			writeWorkAdviceHTTPError(w, err)
			return
		}
		loadedJob, _ := store.GetAdviceJob(ctx, job.JobUUID)
		if loadedJob == nil {
			loadedJob = job
		}
		env, err := buildAdviceReviewEnvelope(ctx, store, collection, proj.BindingID, itemUUID, prop, loadedJob, actions)
		if err != nil {
			writeWorkAdviceHTTPError(w, err)
			return
		}
		_ = json.NewEncoder(w).Encode(env)

	case r.URL.Path == "/api/work/advice/list" && r.Method == http.MethodGet:
		slug := r.URL.Query().Get("project")
		rawItem := r.URL.Query().Get("item")
		if slug == "" || rawItem == "" {
			writeWorkAdviceHTTPError(w, &core.Error{Code: "INVALID_REQUEST", Message: "project and item query params required"})
			return
		}
		proj, err := store.GetWorkProject(ctx, collection, slug)
		if err != nil {
			writeWorkAdviceHTTPError(w, err)
			return
		}
		itemUUID, err := store.ResolveWorkItemUUID(ctx, collection, slug, rawItem)
		if err != nil {
			writeWorkAdviceHTTPError(w, err)
			return
		}

		proposals, err := store.ListAdviceProposals(ctx, proj.BindingID, itemUUID)
		if err != nil {
			writeWorkAdviceHTTPError(w, err)
			return
		}
		if proposals == nil {
			proposals = make([]*core.WorkAdviceProposal, 0)
		}
		nowRFC := time.Now().UTC().Format(time.RFC3339)
		itemContext := WorkAdviceItemContext{
			Collection:    collection,
			BindingID:     proj.BindingID,
			PlaneItemUUID: itemUUID,
			ObservedAt:    nowRFC,
		}
		ctrl, _ := store.GetAdviceControl(ctx)
		availability := computeAvailability(st, ctrl, nil)
		_ = json.NewEncoder(w).Encode(map[string]any{
			"context":      itemContext,
			"availability": availability,
			"proposals":    proposals,
		})

	case r.URL.Path == "/api/work/advice/show" && r.Method == http.MethodGet:
		rawProp := r.URL.Query().Get("proposal")
		rawReqID := r.URL.Query().Get("request_id")
		if (rawProp == "" && rawReqID == "") || (rawProp != "" && rawReqID != "") {
			writeWorkAdviceHTTPError(w, &core.Error{Code: "INVALID_REQUEST", Message: "exactly one of proposal or request_id query param required"})
			return
		}
		var job *core.WorkAdviceJob
		var prop *core.WorkAdviceProposal
		var actions []*core.WorkAdviceAction

		if rawProp != "" {
			pUUID, err := uuid.Parse(rawProp)
			if err != nil || pUUID == uuid.Nil {
				writeWorkAdviceHTTPError(w, &core.Error{Code: "INVALID_REQUEST", Message: "invalid proposal UUID"})
				return
			}
			prop, actions, err = store.GetAdviceProposal(ctx, pUUID)
			if err != nil {
				writeWorkAdviceHTTPError(w, err)
				return
			}
			job, err = store.GetAdviceJob(ctx, prop.JobUUID)
			if err != nil {
				writeWorkAdviceHTTPError(w, err)
				return
			}
		} else {
			reqUUID, err := uuid.Parse(rawReqID)
			if err != nil || reqUUID == uuid.Nil {
				writeWorkAdviceHTTPError(w, &core.Error{Code: "INVALID_REQUEST", Message: "invalid request_id UUID"})
				return
			}
			job, err = store.GetAdviceJobByRequestUUID(ctx, reqUUID)
			if err != nil {
				writeWorkAdviceHTTPError(w, err)
				return
			}
			prop, actions, _ = store.GetAdviceProposalByJobUUID(ctx, job.JobUUID)
		}

		env, err := buildAdviceReviewEnvelope(ctx, store, collection, job.BindingID, job.PlaneItemUUID, prop, job, actions)
		if err != nil {
			writeWorkAdviceHTTPError(w, err)
			return
		}
		_ = json.NewEncoder(w).Encode(env)

	case r.URL.Path == "/api/work/advice/apply" && r.Method == http.MethodPost:
		var body adviceApplyBody
		if err := decodeStrictAdviceJSON(r, &body); err != nil {
			writeWorkAdviceHTTPError(w, err)
			return
		}
		if body.ProposalUUID == uuid.Nil || body.Version <= 0 {
			writeWorkAdviceHTTPError(w, &core.Error{Code: "INVALID_REQUEST", Message: "proposal_uuid and positive version required"})
			return
		}
		reqUUID := uuid.New()
		if body.RequestID != "" {
			u, err := uuid.Parse(body.RequestID)
			if err != nil || u == uuid.Nil {
				writeWorkAdviceHTTPError(w, &core.Error{Code: "INVALID_REQUEST", Message: "invalid request_id"})
				return
			}
			reqUUID = u
		}
		receipt := &core.WorkAdviceApplicationReceipt{
			RequestUUID:  reqUUID,
			ProposalUUID: body.ProposalUUID,
		}
		applied, err := store.ApplyAdviceProposal(ctx, receipt, body.Version, body.Actions)
		if err != nil {
			writeWorkAdviceHTTPError(w, err)
			return
		}
		_ = json.NewEncoder(w).Encode(map[string]any{"receipt": applied})

	case r.URL.Path == "/api/work/advice/reject" && r.Method == http.MethodPost:
		var body adviceRejectBody
		if err := decodeStrictAdviceJSON(r, &body); err != nil {
			writeWorkAdviceHTTPError(w, err)
			return
		}
		if body.ProposalUUID == uuid.Nil || body.Version <= 0 {
			writeWorkAdviceHTTPError(w, &core.Error{Code: "INVALID_REQUEST", Message: "proposal_uuid and positive version required"})
			return
		}
		if err := store.RejectAdviceProposal(ctx, body.ProposalUUID, body.Version); err != nil {
			writeWorkAdviceHTTPError(w, err)
			return
		}
		_ = json.NewEncoder(w).Encode(map[string]any{"ok": true})

	case r.URL.Path == "/api/work/advice/undo" && r.Method == http.MethodPost:
		var body adviceUndoBody
		if err := decodeStrictAdviceJSON(r, &body); err != nil {
			writeWorkAdviceHTTPError(w, err)
			return
		}
		if body.RequestUUID == uuid.Nil {
			writeWorkAdviceHTTPError(w, &core.Error{Code: "INVALID_REQUEST", Message: "request_uuid required"})
			return
		}
		if err := store.UndoAdviceApplication(ctx, body.RequestUUID); err != nil {
			writeWorkAdviceHTTPError(w, err)
			return
		}
		receipt, err := store.GetAdviceReceipt(ctx, body.RequestUUID)
		if err != nil {
			writeWorkAdviceHTTPError(w, err)
			return
		}
		_ = json.NewEncoder(w).Encode(map[string]any{"receipt": receipt})

	case r.URL.Path == "/api/work/advice/profile/register" && r.Method == http.MethodPost:
		var body adviceProfileRegisterBody
		if err := decodeStrictAdviceJSON(r, &body); err != nil {
			writeWorkAdviceHTTPError(w, err)
			return
		}
		if body.Profile == "" || body.Executable == "" || body.BinarySHA256 == "" || body.CodexHome == "" || body.Model == "" {
			writeWorkAdviceHTTPError(w, &core.Error{Code: "INVALID_REQUEST", Message: "profile, executable, binary_sha256, codex_home, and model required"})
			return
		}
		route := body.Route
		if route == "" {
			route = advice.RouteCodexSubscription
		}
		prof := &advice.Profile{
			Name:                   body.Profile,
			Route:                  route,
			ExecutablePath:         body.Executable,
			BinarySHA256:           body.BinarySHA256,
			ExpectedUpstreamCommit: body.ExpectedUpstreamCommit,
			ExpectedPatchSHA256:    body.ExpectedPatchSHA256,
			CodexHome:              body.CodexHome,
			Model:                  body.Model,
			TimeoutMs:              body.TimeoutMs,
			MaxOutputBytes:         body.MaxOutputBytes,
		}
		if err := store.RegisterAdviceProfile(ctx, prof); err != nil {
			writeWorkAdviceHTTPError(w, err)
			return
		}
		_ = json.NewEncoder(w).Encode(map[string]any{"profile": prof})

	case r.URL.Path == "/api/work/advice/profile/list" && r.Method == http.MethodGet:
		profiles, err := store.ListAdviceProfiles(ctx)
		if err != nil {
			writeWorkAdviceHTTPError(w, err)
			return
		}
		if profiles == nil {
			profiles = make([]*advice.Profile, 0)
		}
		_ = json.NewEncoder(w).Encode(map[string]any{"profiles": profiles})

	case r.URL.Path == "/api/work/advice/profile/show" && r.Method == http.MethodGet:
		rawName := r.URL.Query().Get("profile")
		if rawName == "" {
			writeWorkAdviceHTTPError(w, &core.Error{Code: "INVALID_REQUEST", Message: "profile query param required"})
			return
		}
		prof, err := store.GetAdviceProfile(ctx, rawName)
		if err != nil {
			writeWorkAdviceHTTPError(w, err)
			return
		}
		_ = json.NewEncoder(w).Encode(map[string]any{"profile": prof})

	case r.URL.Path == "/api/work/advice/policy" && r.Method == http.MethodGet:
		slug := r.URL.Query().Get("project")
		if slug == "" {
			writeWorkAdviceHTTPError(w, &core.Error{Code: "INVALID_REQUEST", Message: "project query param required"})
			return
		}
		proj, err := store.GetWorkProject(ctx, collection, slug)
		if err != nil {
			writeWorkAdviceHTTPError(w, err)
			return
		}
		pol, err := store.GetWorkAdvicePolicyDetail(ctx, proj.BindingID)
		if err != nil {
			writeWorkAdviceHTTPError(w, err)
			return
		}
		_ = json.NewEncoder(w).Encode(map[string]any{
			"binding_id":      proj.BindingID,
			"policy_revision": pol.PolicyRevision,
			"allowed_fields":  pol.AllowedFields,
		})

	case r.URL.Path == "/api/work/advice/policy" && r.Method == http.MethodPost:
		var body advicePolicySetBody
		if err := decodeStrictAdviceJSON(r, &body); err != nil {
			writeWorkAdviceHTTPError(w, err)
			return
		}
		if body.Project == "" {
			writeWorkAdviceHTTPError(w, &core.Error{Code: "INVALID_REQUEST", Message: "project is required"})
			return
		}
		proj, err := store.GetWorkProject(ctx, collection, body.Project)
		if err != nil {
			writeWorkAdviceHTTPError(w, err)
			return
		}
		if err := store.SetWorkAdvicePolicy(ctx, proj.BindingID, body.Fields); err != nil {
			writeWorkAdviceHTTPError(w, err)
			return
		}
		pol, _ := store.GetWorkAdvicePolicyDetail(ctx, proj.BindingID)
		var rev int64
		var fields []string
		if pol != nil {
			rev = pol.PolicyRevision
			fields = pol.AllowedFields
		}
		_ = json.NewEncoder(w).Encode(map[string]any{
			"ok":              true,
			"binding_id":      proj.BindingID,
			"policy_revision": rev,
			"allowed_fields":  fields,
		})

	case r.URL.Path == "/api/work/advice/reconcile" && r.Method == http.MethodPost:
		reconciled, err := store.ReconcileAdviceJobs(ctx)
		if err != nil {
			writeWorkAdviceHTTPError(w, err)
			return
		}
		if reconciled == nil {
			reconciled = make([]core.ReconciledAdviceJob, 0)
		}
		swept, err := store.SweepExpiredAdvice(ctx)
		if err != nil {
			writeWorkAdviceHTTPError(w, err)
			return
		}
		_ = json.NewEncoder(w).Encode(map[string]any{"reconciled": reconciled, "swept": swept})

	default:
		http.NotFound(w, r)
	}
}
