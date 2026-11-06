package advice

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"strings"

	"github.com/google/uuid"
)

type rawProposalEnvelope struct {
	Actions []json.RawMessage `json:"actions"`
	Summary *string           `json:"summary,omitempty"`
}

type rawActionEnvelope struct {
	ActionType         string    `json:"action_type"`
	Rationale          *string   `json:"rationale,omitempty"`
	Text               *string   `json:"text,omitempty"`
	SourceReferences   *[]string `json:"source_references,omitempty"`
	BlockerAction      *string   `json:"blocker_action,omitempty"`
	ExistingBlockerID  *string   `json:"existing_blocker_id,omitempty"`
	ExpectedRevision   *int64    `json:"expected_revision,omitempty"`
	ArtifactRevisionID *string   `json:"artifact_revision_id,omitempty"`
	Role               *string   `json:"role,omitempty"`
}

// ParseProposal strictly parses a model's result string against the closed advice schema.
// It enforces that no markdown fences, no trailing data, no unknown fields, and no
// invalid IDs are accepted.
func ParseProposal(rawResult string, snapshot *Snapshot) (*Proposal, error) {
	if strings.Contains(rawResult, "```") {
		return nil, NewAdviceError(ErrInvalidProposal, "markdown code fences rejected in closed-schema advice output", nil)
	}

	trimmed := strings.TrimSpace(rawResult)
	if trimmed == "" {
		return nil, NewAdviceError(ErrInvalidProposal, "empty proposal result", nil)
	}
	if !strings.HasPrefix(trimmed, "{") || !strings.HasSuffix(trimmed, "}") {
		return nil, NewAdviceError(ErrInvalidProposal, "proposal output must be exactly one JSON object with no surrounding prose", nil)
	}

	dec := json.NewDecoder(strings.NewReader(trimmed))
	dec.DisallowUnknownFields()

	var envelope rawProposalEnvelope
	if err := dec.Decode(&envelope); err != nil {
		return nil, NewAdviceError(ErrInvalidProposal, fmt.Sprintf("malformed proposal JSON: %v", err), err)
	}

	// Verify no trailing JSON or non-whitespace data exists.
	var extra json.RawMessage
	if err := dec.Decode(&extra); err != io.EOF {
		return nil, NewAdviceError(ErrInvalidProposal, "trailing data after proposal JSON object", err)
	}

	if len(envelope.Actions) > MaxActions {
		return nil, NewAdviceError(ErrInvalidProposal, fmt.Sprintf("action count %d exceeds maximum limit of %d", len(envelope.Actions), MaxActions), nil)
	}

	proposal := &Proposal{
		Actions: make([]ProposedAction, 0, len(envelope.Actions)),
	}
	if envelope.Summary != nil {
		if len([]byte(*envelope.Summary)) > MaxSummaryBytes {
			return nil, NewAdviceError(ErrInvalidProposal, fmt.Sprintf("proposal summary exceeds maximum limit of %d bytes", MaxSummaryBytes), nil)
		}
		proposal.Summary = *envelope.Summary
	}

	// Build lookup sets from snapshot if provided.
	validSources := make(map[string]struct{})
	validArtifacts := make(map[string]struct{})
	validBlockers := make(map[string]int64)

	if snapshot != nil {
		for _, s := range snapshot.SourceReferences {
			validSources[s.ID] = struct{}{}
		}
		for _, a := range snapshot.CandidateArtifacts {
			validArtifacts[a.RevisionUUID] = struct{}{}
		}
		for _, b := range snapshot.ExistingBlockers {
			validBlockers[b.BlockerID] = b.Revision
		}
	}

	for idx, rawAct := range envelope.Actions {
		actDec := json.NewDecoder(bytes.NewReader(rawAct))
		actDec.DisallowUnknownFields()

		var act rawActionEnvelope
		if err := actDec.Decode(&act); err != nil {
			return nil, NewAdviceError(ErrInvalidProposal, fmt.Sprintf("action %d contains unknown or malformed fields: %v", idx, err), err)
		}

		var extraAct json.RawMessage
		if err := actDec.Decode(&extraAct); err != io.EOF {
			return nil, NewAdviceError(ErrInvalidProposal, fmt.Sprintf("action %d contains trailing tokens", idx), nil)
		}

		rationale := ""
		if act.Rationale != nil {
			rationale = *act.Rationale
			if len([]byte(rationale)) > MaxRationaleBytes {
				return nil, NewAdviceError(ErrInvalidProposal, fmt.Sprintf("action %d rationale exceeds limit of %d bytes", idx, MaxRationaleBytes), nil)
			}
		}

		parsedAction := ProposedAction{
			ActionIndex: idx,
			ActionType:  act.ActionType,
			Rationale:   rationale,
		}

		switch act.ActionType {
		case ActionAnnotateProgress:
			if act.BlockerAction != nil || act.ExistingBlockerID != nil || act.ExpectedRevision != nil ||
				act.ArtifactRevisionID != nil || act.Role != nil {
				return nil, NewAdviceError(ErrInvalidProposal, fmt.Sprintf("action %d (annotate_progress) contains foreign fields", idx), nil)
			}
			if act.Text == nil || strings.TrimSpace(*act.Text) == "" {
				return nil, NewAdviceError(ErrInvalidProposal, fmt.Sprintf("action %d (annotate_progress) requires non-empty text", idx), nil)
			}
			text := *act.Text
			if len([]byte(text)) > MaxAnnotationTextBytes {
				return nil, NewAdviceError(ErrInvalidProposal, fmt.Sprintf("action %d (annotate_progress) text exceeds limit of %d bytes", idx, MaxAnnotationTextBytes), nil)
			}

			var sourceRefs []string
			if act.SourceReferences != nil {
				sourceRefs = *act.SourceReferences
				for _, ref := range sourceRefs {
					if snapshot != nil {
						if _, ok := validSources[ref]; !ok {
							return nil, NewAdviceError(ErrInvalidProposal, fmt.Sprintf("action %d (annotate_progress) cites unknown source reference %q", idx, ref), nil)
						}
					}
				}
			}

			parsedAction.Progress = &ProgressPayload{
				Text:             text,
				SourceReferences: sourceRefs,
			}

		case ActionAnnotateBlocker:
			if act.ArtifactRevisionID != nil || act.Role != nil {
				return nil, NewAdviceError(ErrInvalidProposal, fmt.Sprintf("action %d (annotate_blocker) contains foreign artifact fields", idx), nil)
			}
			if act.BlockerAction == nil {
				return nil, NewAdviceError(ErrInvalidProposal, fmt.Sprintf("action %d (annotate_blocker) requires blocker_action ('create' or 'resolve')", idx), nil)
			}

			blockerAction := *act.BlockerAction
			var sourceRefs []string
			if act.SourceReferences != nil {
				sourceRefs = *act.SourceReferences
				for _, ref := range sourceRefs {
					if snapshot != nil {
						if _, ok := validSources[ref]; !ok {
							return nil, NewAdviceError(ErrInvalidProposal, fmt.Sprintf("action %d (annotate_blocker) cites unknown source reference %q", idx, ref), nil)
						}
					}
				}
			}

			switch blockerAction {
			case "create":
				if act.ExistingBlockerID != nil || act.ExpectedRevision != nil {
					return nil, NewAdviceError(ErrInvalidProposal, fmt.Sprintf("action %d (annotate_blocker create) must not specify existing_blocker_id or expected_revision", idx), nil)
				}
				if act.Text == nil || strings.TrimSpace(*act.Text) == "" {
					return nil, NewAdviceError(ErrInvalidProposal, fmt.Sprintf("action %d (annotate_blocker create) requires non-empty text", idx), nil)
				}
				text := *act.Text
				if len([]byte(text)) > MaxAnnotationTextBytes {
					return nil, NewAdviceError(ErrInvalidProposal, fmt.Sprintf("action %d (annotate_blocker create) text exceeds limit of %d bytes", idx, MaxAnnotationTextBytes), nil)
				}

				parsedAction.Blocker = &BlockerPayload{
					BlockerAction:    "create",
					Text:             text,
					SourceReferences: sourceRefs,
				}

			case "resolve":
				if act.ExistingBlockerID == nil || strings.TrimSpace(*act.ExistingBlockerID) == "" {
					return nil, NewAdviceError(ErrInvalidProposal, fmt.Sprintf("action %d (annotate_blocker resolve) requires existing_blocker_id", idx), nil)
				}
				blockerID := *act.ExistingBlockerID
				if _, err := uuid.Parse(blockerID); err != nil {
					return nil, NewAdviceError(ErrInvalidProposal, fmt.Sprintf("action %d (annotate_blocker resolve) invalid existing_blocker_id UUID %q", idx, blockerID), err)
				}

				if act.ExpectedRevision == nil || *act.ExpectedRevision < 1 {
					return nil, NewAdviceError(ErrInvalidProposal, fmt.Sprintf("action %d (annotate_blocker resolve) requires positive expected_revision", idx), nil)
				}
				rev := *act.ExpectedRevision

				if snapshot != nil {
					expectedRev, ok := validBlockers[blockerID]
					if !ok {
						return nil, NewAdviceError(ErrInvalidProposal, fmt.Sprintf("action %d (annotate_blocker resolve) references unknown blocker %q", idx, blockerID), nil)
					}
					if expectedRev != rev {
						return nil, NewAdviceError(ErrInvalidProposal, fmt.Sprintf("action %d (annotate_blocker resolve) revision %d does not match snapshot revision %d", idx, rev, expectedRev), nil)
					}
				}

				text := ""
				if act.Text != nil {
					text = *act.Text
					if len([]byte(text)) > MaxAnnotationTextBytes {
						return nil, NewAdviceError(ErrInvalidProposal, fmt.Sprintf("action %d (annotate_blocker resolve) text exceeds limit of %d bytes", idx, MaxAnnotationTextBytes), nil)
					}
				}

				parsedAction.Blocker = &BlockerPayload{
					BlockerAction:     "resolve",
					ExistingBlockerID: blockerID,
					ExpectedRevision:  rev,
					Text:              text,
					SourceReferences:  sourceRefs,
				}

			default:
				return nil, NewAdviceError(ErrInvalidProposal, fmt.Sprintf("action %d unknown blocker_action %q (must be 'create' or 'resolve')", idx, blockerAction), nil)
			}

		case ActionLinkArtifact:
			if act.Text != nil || act.SourceReferences != nil || act.BlockerAction != nil ||
				act.ExistingBlockerID != nil || act.ExpectedRevision != nil {
				return nil, NewAdviceError(ErrInvalidProposal, fmt.Sprintf("action %d (link_artifact) contains foreign annotation fields", idx), nil)
			}
			if act.ArtifactRevisionID == nil || strings.TrimSpace(*act.ArtifactRevisionID) == "" {
				return nil, NewAdviceError(ErrInvalidProposal, fmt.Sprintf("action %d (link_artifact) requires artifact_revision_id", idx), nil)
			}
			artID := *act.ArtifactRevisionID
			if _, err := uuid.Parse(artID); err != nil {
				return nil, NewAdviceError(ErrInvalidProposal, fmt.Sprintf("action %d (link_artifact) invalid artifact_revision_id UUID %q", idx, artID), err)
			}

			if snapshot != nil {
				if _, ok := validArtifacts[artID]; !ok {
					return nil, NewAdviceError(ErrInvalidProposal, fmt.Sprintf("action %d (link_artifact) artifact revision %q is not in snapshot candidate revisions", idx, artID), nil)
				}
			}

			if act.Role == nil {
				return nil, NewAdviceError(ErrInvalidProposal, fmt.Sprintf("action %d (link_artifact) requires role", idx), nil)
			}
			role := *act.Role
			switch role {
			case "input", "output", "evidence":
				// valid
			default:
				return nil, NewAdviceError(ErrInvalidProposal, fmt.Sprintf("action %d (link_artifact) invalid role %q (must be 'input', 'output', or 'evidence')", idx, role), nil)
			}

			parsedAction.Artifact = &ArtifactPayload{
				ArtifactRevisionID: artID,
				Role:               role,
			}

		default:
			return nil, NewAdviceError(ErrInvalidProposal, fmt.Sprintf("action %d unknown action_type %q", idx, act.ActionType), nil)
		}

		proposal.Actions = append(proposal.Actions, parsedAction)
	}

	canonical, err := json.Marshal(proposal)
	if err != nil {
		return nil, NewAdviceError(ErrInvalidProposal, "failed to marshal normalized proposal", err)
	}
	if len(canonical) > MaxNormalizedProposalBytes {
		return nil, NewAdviceError(ErrInvalidProposal, fmt.Sprintf("canonicalized proposal JSON size %d exceeds limit of %d bytes", len(canonical), MaxNormalizedProposalBytes), nil)
	}

	return proposal, nil
}
