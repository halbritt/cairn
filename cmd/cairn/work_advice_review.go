package main

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"time"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
	"github.com/halbritt/cairn/internal/advice"
)

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
	rejectAllowed := (prop != nil && (prop.Status == "pending" || prop.Status == "partially_applied"))
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
		LastRefreshedAt:    rawMan.LastRefreshedAt.UTC().Format(time.RFC3339Nano),
		RestoreGeneration:  rawMan.RestoreGeneration,
		Sources:            make([]ReviewedManifestSource, 0, len(rawMan.Sources)),
		CandidateRevisions: make([]ReviewedCandidateRevision, 0, len(rawMan.CandidateRevisionIDs)),
	}
	for _, s := range rawMan.Sources {
		eligible := true
		if store != nil {
			eligible = store.CheckSourceEligibleLive(ctx, s.RecordID, s.Version)
		}
		obsAtStr := ""
		if !s.ObservedAt.IsZero() {
			obsAtStr = s.ObservedAt.UTC().Format(time.RFC3339Nano)
		} else if job != nil && !job.CreatedAt.IsZero() {
			obsAtStr = job.CreatedAt.UTC().Format(time.RFC3339Nano)
		}
		manifest.Sources = append(manifest.Sources, ReviewedManifestSource{
			RecordID:   s.RecordID,
			Version:    s.Version,
			BodySHA256: s.BodySHA256,
			ObservedAt: obsAtStr,
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

func buildReviewedActions(ctx context.Context, store *core.Store, job *core.WorkAdviceJob, actions []*core.WorkAdviceAction, snapshot *advice.Snapshot, channelPrincipal string) ([]ReviewedAction, error) {
	reviewedActions := make([]ReviewedAction, 0, len(actions))
	requester := "operator"
	if job != nil && job.Caller != "" {
		requester = job.Caller
	}
	acceptingActor := channelPrincipal
	if acceptingActor == "" {
		acceptingActor = "operator"
	}

	var rawMan core.WorkAdviceInputManifest
	if job != nil && len(job.InputManifest) > 0 {
		_ = json.Unmarshal(job.InputManifest, &rawMan)
	}

	type sourceInfo struct {
		RecordID   uuid.UUID
		Version    int
		BodySHA256 string
		ObservedAt string
	}
	sourceMap := make(map[uuid.UUID]sourceInfo)
	for _, s := range rawMan.Sources {
		obsStr := ""
		if !s.ObservedAt.IsZero() {
			obsStr = s.ObservedAt.UTC().Format(time.RFC3339Nano)
		} else if job != nil && !job.CreatedAt.IsZero() {
			obsStr = job.CreatedAt.UTC().Format(time.RFC3339Nano)
		}
		sourceMap[s.RecordID] = sourceInfo{
			RecordID:   s.RecordID,
			Version:    s.Version,
			BodySHA256: s.BodySHA256,
			ObservedAt: obsStr,
		}
	}

	lookupSourceRef := func(ref string) (ActionSourceRef, bool) {
		u, err := uuid.Parse(ref)
		if err != nil {
			return ActionSourceRef{}, false
		}
		if info, ok := sourceMap[u]; ok {
			return ActionSourceRef{
				RecordID:   info.RecordID,
				Version:    info.Version,
				BodySHA256: info.BodySHA256,
				ObservedAt: info.ObservedAt,
			}, true
		}
		obs := ""
		if job != nil && !job.CreatedAt.IsZero() {
			obs = job.CreatedAt.UTC().Format(time.RFC3339Nano)
		}
		return ActionSourceRef{
			RecordID:   u,
			Version:    1,
			ObservedAt: obs,
		}, true
	}

	for _, act := range actions {
		var before any
		var after any
		srcRefs := make([]ActionSourceRef, 0)
		payloadPurged := len(act.TargetPayload) == 0 || string(act.TargetPayload) == "{}"

		switch act.ActionType {
		case "annotate_progress":
			var p struct {
				Text             string   `json:"text"`
				SourceReferences []string `json:"source_references"`
			}
			if !payloadPurged {
				_ = json.Unmarshal(act.TargetPayload, &p)
			}
			unavailable := false
			if rawMan.PreconditionProgressPresent && rawMan.PreconditionProgressID != nil {
				if store != nil {
					b, st, ok, err := store.GetAnnotationHistorySnapshot(ctx, *rawMan.PreconditionProgressID, rawMan.PreconditionProgressRevision)
					if err != nil {
						return nil, err
					}
					if !ok {
						unavailable = true
						before = map[string]any{
							"annotation_id": rawMan.PreconditionProgressID.String(),
							"revision":      rawMan.PreconditionProgressRevision,
							"kind":          "progress",
							"state":         "unavailable",
							"unavailable":   true,
						}
					} else {
						before = map[string]any{
							"annotation_id": rawMan.PreconditionProgressID.String(),
							"revision":      rawMan.PreconditionProgressRevision,
							"kind":          "progress",
							"body":          b,
							"state":         st,
						}
					}
				} else {
					unavailable = true
					before = map[string]any{
						"annotation_id": rawMan.PreconditionProgressID.String(),
						"revision":      rawMan.PreconditionProgressRevision,
						"kind":          "progress",
						"state":         "unavailable",
						"unavailable":   true,
					}
				}
			}
			if !payloadPurged && !unavailable {
				after = map[string]any{
					"kind":  "progress",
					"body":  p.Text,
					"state": "active",
				}
			}
			for _, ref := range p.SourceReferences {
				if sr, ok := lookupSourceRef(ref); ok {
					srcRefs = append(srcRefs, sr)
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
			if !payloadPurged {
				_ = json.Unmarshal(act.TargetPayload, &b)
			}
			if b.BlockerAction == "resolve" {
				parsedID, pErr := uuid.Parse(b.ExistingBlockerID)
				if pErr != nil || store == nil {
					before = map[string]any{
						"annotation_id": b.ExistingBlockerID,
						"revision":      b.ExpectedRevision,
						"kind":          "blocker",
						"state":         "unavailable",
						"unavailable":   true,
					}
				} else {
					bText, st, ok, err := store.GetAnnotationHistorySnapshot(ctx, parsedID, b.ExpectedRevision)
					if err != nil {
						return nil, err
					}
					if !ok {
						before = map[string]any{
							"annotation_id": b.ExistingBlockerID,
							"revision":      b.ExpectedRevision,
							"kind":          "blocker",
							"state":         "unavailable",
							"unavailable":   true,
						}
					} else {
						before = map[string]any{
							"annotation_id": b.ExistingBlockerID,
							"revision":      b.ExpectedRevision,
							"kind":          "blocker",
							"body":          bText,
							"state":         st,
						}
						if !payloadPurged {
							afterBody := b.Text
							if afterBody == "" {
								afterBody = bText
							}
							after = map[string]any{
								"annotation_id": b.ExistingBlockerID,
								"revision":      b.ExpectedRevision + 1,
								"kind":          "blocker",
								"body":          afterBody,
								"state":         "resolved",
							}
						}
					}
				}
			} else {
				if !payloadPurged {
					after = map[string]any{
						"kind":  "blocker",
						"body":  b.Text,
						"state": "open",
					}
				}
			}
			for _, ref := range b.SourceReferences {
				if sr, ok := lookupSourceRef(ref); ok {
					srcRefs = append(srcRefs, sr)
				}
			}
		case "link_artifact":
			var l struct {
				ArtifactRevisionID string `json:"artifact_revision_id"`
				Role               string `json:"role"`
			}
			if !payloadPurged {
				_ = json.Unmarshal(act.TargetPayload, &l)
				targetRevID, _ := uuid.Parse(l.ArtifactRevisionID)
				for _, cl := range rawMan.CapturedArtifactLinks {
					if cl.RevisionID == targetRevID && cl.Role == l.Role {
						before = map[string]any{
							"link_id":     cl.LinkID.String(),
							"revision":    cl.Revision,
							"revision_id": cl.RevisionID.String(),
							"role":        cl.Role,
							"state":       cl.State,
						}
						break
					}
				}
				after = map[string]any{
					"revision_id": l.ArtifactRevisionID,
					"role":        l.Role,
					"state":       "active",
				}
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
	return reviewedActions, nil
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
	reviewedActions, err := buildReviewedActions(ctx, store, job, actions, input.Snapshot, store.Channel().Principal)
	if err != nil {
		return nil, err
	}
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
