package main

import (
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"strconv"
	"strings"
	"time"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
	"github.com/halbritt/cairn/internal/refusal"
)

func writeWorkArtifactHTTPError(w http.ResponseWriter, err error) {
	code := core.Code(err)
	status := refusal.Classify(code).HTTPStatus
	if status == 0 {
		status = http.StatusInternalServerError
	}
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(map[string]any{
		"error": err.Error(),
		"code":  code,
	})
}

func decodeStrictJSON[T any](r *http.Request, target *T) error {
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

type artifactRegisterBody struct {
	RequestID        string         `json:"request_id"`
	Kind             string         `json:"kind"`
	DisplayName      string         `json:"display_name,omitempty"`
	RevisionLabel    string         `json:"revision_label,omitempty"`
	GitRepo          string         `json:"git_repo,omitempty"`
	GitCommitOID     string         `json:"git_commit_oid,omitempty"`
	GitObjectFormat  string         `json:"git_object_format,omitempty"`
	GitBranchHint    string         `json:"git_branch_hint,omitempty"`
	ReportLocator    string         `json:"report_locator,omitempty"`
	ReportDigest     string         `json:"report_digest,omitempty"`
	ReportProvenance string         `json:"report_provenance,omitempty"`
	CIProvider       string         `json:"ci_provider,omitempty"`
	CIRepo           string         `json:"ci_repo,omitempty"`
	CIRunID          string         `json:"ci_run_id,omitempty"`
	CIAttempt        int            `json:"ci_attempt,omitempty"`
	CIStatus         string         `json:"ci_status,omitempty"`
	CIReportedAt     *time.Time     `json:"ci_reported_at,omitempty"`
	CairnRecordID    *uuid.UUID     `json:"cairn_record_id,omitempty"`
	CairnVersion     *int           `json:"cairn_version,omitempty"`
	Metadata         map[string]any `json:"metadata,omitempty"`
}

type artifactLinkBody struct {
	RequestID  string    `json:"request_id"`
	Project    string    `json:"project"`
	ItemUUID   uuid.UUID `json:"item_uuid"`
	RevisionID uuid.UUID `json:"revision_id"`
	Role       string    `json:"role"`
	Rationale  string    `json:"rationale,omitempty"`
}

type artifactUnlinkBody struct {
	RequestID        string    `json:"request_id"`
	Project          string    `json:"project"`
	ItemUUID         uuid.UUID `json:"item_uuid"`
	LinkID           uuid.UUID `json:"link_id"`
	ExpectedRevision int64     `json:"expected_revision"`
}

type artifactPurgeBody struct {
	RequestID  string    `json:"request_id"`
	ArtifactID uuid.UUID `json:"artifact_id"`
}

// HandleWorkArtifactAPI handles HTTP routes under /api/work/artifact/*
func HandleWorkArtifactAPI(store *core.Store, collection string, w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	subpath := strings.TrimPrefix(r.URL.Path, "/api/work/artifact")
	subpath = strings.TrimPrefix(subpath, "/")

	w.Header().Set("Content-Type", "application/json; charset=utf-8")

	switch {
	case subpath == "status" && r.Method == http.MethodGet:
		st, err := store.GetWorkFeatureStatus(ctx, "work-artifacts/1")
		if err != nil {
			writeWorkArtifactHTTPError(w, err)
			return
		}
		_ = json.NewEncoder(w).Encode(map[string]any{
			"feature_name": "work-artifacts/1",
			"status":       st,
		})

	case subpath == "show" && r.Method == http.MethodGet:
		revStr := r.URL.Query().Get("revision")
		revID, err := uuid.Parse(revStr)
		if err != nil || revID == uuid.Nil {
			writeWorkArtifactHTTPError(w, &core.Error{Code: "INVALID_REQUEST", Message: "valid nonzero revision UUID required"})
			return
		}
		rev, err := store.GetWorkArtifactRevision(ctx, collection, revID)
		if err != nil {
			writeWorkArtifactHTTPError(w, err)
			return
		}
		_ = json.NewEncoder(w).Encode(rev)

	case subpath == "list" && r.Method == http.MethodGet:
		proj := strings.TrimSpace(r.URL.Query().Get("project"))
		if proj == "" {
			writeWorkArtifactHTTPError(w, &core.Error{Code: "INVALID_REQUEST", Message: "project required"})
			return
		}
		itemStr := strings.TrimSpace(r.URL.Query().Get("item"))
		itemID, err := uuid.Parse(itemStr)
		if err != nil || itemID == uuid.Nil {
			writeWorkArtifactHTTPError(w, &core.Error{Code: "INVALID_REQUEST", Message: "valid nonzero item UUID required"})
			return
		}
		role := strings.TrimSpace(r.URL.Query().Get("role"))
		var includeRemoved bool
		if incRem := r.URL.Query().Get("include_removed"); incRem != "" {
			if incRem == "1" || incRem == "true" {
				includeRemoved = true
			} else if incRem == "0" || incRem == "false" {
				includeRemoved = false
			} else {
				writeWorkArtifactHTTPError(w, &core.Error{Code: "INVALID_REQUEST", Message: "invalid include_removed parameter; expected 1 or true"})
				return
			}
		}

		links, err := store.ListWorkArtifactLinks(ctx, core.ListWorkArtifactLinksRequest{
			Collection:     collection,
			ProjectSlug:    proj,
			ItemUUID:       itemID,
			Role:           role,
			IncludeRemoved: includeRemoved,
		})
		if err != nil {
			writeWorkArtifactHTTPError(w, err)
			return
		}
		if links == nil {
			links = []core.WorkArtifactLink{}
		}
		_ = json.NewEncoder(w).Encode(map[string]any{
			"artifacts": links,
		})

	case subpath == "impact" && r.Method == http.MethodGet:
		revStr := r.URL.Query().Get("revision")
		revID, err := uuid.Parse(revStr)
		if err != nil || revID == uuid.Nil {
			writeWorkArtifactHTTPError(w, &core.Error{Code: "INVALID_REQUEST", Message: "valid nonzero revision UUID required"})
			return
		}
		limit := 50
		if lStr := r.URL.Query().Get("limit"); lStr != "" {
			l, err := strconv.Atoi(lStr)
			if err != nil || l < 1 || l > 200 {
				writeWorkArtifactHTTPError(w, &core.Error{Code: "INVALID_REQUEST", Message: "limit must be an integer between 1 and 200"})
				return
			}
			limit = l
		}
		cursor := r.URL.Query().Get("cursor")

		page, err := store.ReverseImpact(ctx, core.ReverseImpactRequest{
			Collection: collection,
			RevisionID: revID,
			Limit:      limit,
			Cursor:     cursor,
		})
		if err != nil {
			writeWorkArtifactHTTPError(w, err)
			return
		}
		if page.Impacts == nil {
			page.Impacts = []core.WorkImpactItem{}
		}
		_ = json.NewEncoder(w).Encode(page)

	case subpath == "history" && r.Method == http.MethodGet:
		proj := strings.TrimSpace(r.URL.Query().Get("project"))
		if proj == "" {
			writeWorkArtifactHTTPError(w, &core.Error{Code: "INVALID_REQUEST", Message: "project required"})
			return
		}
		itemStr := strings.TrimSpace(r.URL.Query().Get("item"))
		itemID, err := uuid.Parse(itemStr)
		if err != nil || itemID == uuid.Nil {
			writeWorkArtifactHTTPError(w, &core.Error{Code: "INVALID_REQUEST", Message: "valid nonzero item UUID required"})
			return
		}
		linkStr := strings.TrimSpace(r.URL.Query().Get("link_id"))
		linkID, err := uuid.Parse(linkStr)
		if err != nil || linkID == uuid.Nil {
			writeWorkArtifactHTTPError(w, &core.Error{Code: "INVALID_REQUEST", Message: "valid nonzero link_id UUID required"})
			return
		}
		limit := 50
		if lStr := r.URL.Query().Get("limit"); lStr != "" {
			l, err := strconv.Atoi(lStr)
			if err != nil || l < 1 || l > 200 {
				writeWorkArtifactHTTPError(w, &core.Error{Code: "INVALID_REQUEST", Message: "limit must be an integer between 1 and 200"})
				return
			}
			limit = l
		}
		cursor := r.URL.Query().Get("cursor")

		page, err := store.GetWorkArtifactLinkHistory(ctx, core.GetWorkArtifactLinkHistoryRequest{
			Collection:  collection,
			ProjectSlug: proj,
			ItemUUID:    itemID,
			LinkID:      linkID,
			Limit:       limit,
			Cursor:      cursor,
		})
		if err != nil {
			writeWorkArtifactHTTPError(w, err)
			return
		}
		if page.History == nil {
			page.History = []core.WorkArtifactLinkHistory{}
		}
		_ = json.NewEncoder(w).Encode(page)

	case subpath == "register" && r.Method == http.MethodPost:
		var body artifactRegisterBody
		if err := decodeStrictJSON(r, &body); err != nil {
			writeWorkArtifactHTTPError(w, err)
			return
		}
		reqUUID, err := uuid.Parse(body.RequestID)
		if err != nil || reqUUID == uuid.Nil {
			writeWorkArtifactHTTPError(w, &core.Error{Code: "INVALID_REQUEST", Message: "valid nonzero request_id UUID required"})
			return
		}
		kind := strings.ToLower(strings.TrimSpace(body.Kind))
		switch kind {
		case "git":
			if strings.TrimSpace(body.GitRepo) == "" || strings.TrimSpace(body.GitCommitOID) == "" {
				writeWorkArtifactHTTPError(w, &core.Error{Code: "INVALID_REQUEST", Message: "git_repo and git_commit_oid required for git artifact"})
				return
			}
			if body.ReportLocator != "" || body.ReportDigest != "" || body.ReportProvenance != "" ||
				body.CIProvider != "" || body.CIRepo != "" || body.CIRunID != "" || body.CIAttempt != 0 || body.CIStatus != "" || body.CIReportedAt != nil ||
				body.CairnRecordID != nil || body.CairnVersion != nil {
				writeWorkArtifactHTTPError(w, &core.Error{Code: "INVALID_REQUEST", Message: "unrelated kind fields supplied for git artifact"})
				return
			}
		case "report":
			if strings.TrimSpace(body.ReportLocator) == "" || strings.TrimSpace(body.RevisionLabel) == "" {
				writeWorkArtifactHTTPError(w, &core.Error{Code: "INVALID_REQUEST", Message: "report_locator and revision_label required for report artifact"})
				return
			}
			if body.GitRepo != "" || body.GitCommitOID != "" || body.GitObjectFormat != "" || body.GitBranchHint != "" ||
				body.CIProvider != "" || body.CIRepo != "" || body.CIRunID != "" || body.CIAttempt != 0 || body.CIStatus != "" || body.CIReportedAt != nil ||
				body.CairnRecordID != nil || body.CairnVersion != nil {
				writeWorkArtifactHTTPError(w, &core.Error{Code: "INVALID_REQUEST", Message: "unrelated kind fields supplied for report artifact"})
				return
			}
		case "ci":
			if strings.TrimSpace(body.CIProvider) == "" || strings.TrimSpace(body.CIRepo) == "" || strings.TrimSpace(body.CIRunID) == "" || body.CIAttempt <= 0 {
				writeWorkArtifactHTTPError(w, &core.Error{Code: "INVALID_REQUEST", Message: "ci_provider, ci_repo, ci_run_id, and positive ci_attempt required for ci artifact"})
				return
			}
			if body.GitRepo != "" || body.GitCommitOID != "" || body.GitObjectFormat != "" || body.GitBranchHint != "" ||
				body.ReportLocator != "" || body.ReportDigest != "" || body.ReportProvenance != "" ||
				body.CairnRecordID != nil || body.CairnVersion != nil {
				writeWorkArtifactHTTPError(w, &core.Error{Code: "INVALID_REQUEST", Message: "unrelated kind fields supplied for ci artifact"})
				return
			}
		case "cairn_result":
			if body.CairnRecordID == nil || *body.CairnRecordID == uuid.Nil || body.CairnVersion == nil || *body.CairnVersion <= 0 {
				writeWorkArtifactHTTPError(w, &core.Error{Code: "INVALID_REQUEST", Message: "valid nonzero cairn_record_id and positive cairn_version required for cairn_result artifact"})
				return
			}
			if body.GitRepo != "" || body.GitCommitOID != "" || body.GitObjectFormat != "" || body.GitBranchHint != "" ||
				body.ReportLocator != "" || body.ReportDigest != "" || body.ReportProvenance != "" ||
				body.CIProvider != "" || body.CIRepo != "" || body.CIRunID != "" || body.CIAttempt != 0 || body.CIStatus != "" || body.CIReportedAt != nil {
				writeWorkArtifactHTTPError(w, &core.Error{Code: "INVALID_REQUEST", Message: "unrelated kind fields supplied for cairn_result artifact"})
				return
			}
		default:
			writeWorkArtifactHTTPError(w, &core.Error{Code: "INVALID_REQUEST", Message: fmt.Sprintf("unsupported artifact kind %q", body.Kind)})
			return
		}
		rev, err := store.RegisterWorkArtifact(ctx, core.RegisterWorkArtifactRequest{
			RequestID:        body.RequestID,
			Collection:       collection,
			Kind:             body.Kind,
			DisplayName:      body.DisplayName,
			RevisionLabel:    body.RevisionLabel,
			GitRepo:          body.GitRepo,
			GitCommitOID:     body.GitCommitOID,
			GitObjectFormat:  body.GitObjectFormat,
			GitBranchHint:    body.GitBranchHint,
			ReportLocator:    body.ReportLocator,
			ReportDigest:     body.ReportDigest,
			ReportProvenance: body.ReportProvenance,
			CIProvider:       body.CIProvider,
			CIRepo:           body.CIRepo,
			CIRunID:          body.CIRunID,
			CIAttempt:        body.CIAttempt,
			CIStatus:         body.CIStatus,
			CIReportedAt:     body.CIReportedAt,
			CairnRecordID:    body.CairnRecordID,
			CairnVersion:     body.CairnVersion,
			Metadata:         body.Metadata,
		})
		if err != nil {
			writeWorkArtifactHTTPError(w, err)
			return
		}
		_ = json.NewEncoder(w).Encode(rev)

	case subpath == "link" && r.Method == http.MethodPost:
		var body artifactLinkBody
		if err := decodeStrictJSON(r, &body); err != nil {
			writeWorkArtifactHTTPError(w, err)
			return
		}
		reqUUID, err := uuid.Parse(body.RequestID)
		if err != nil || reqUUID == uuid.Nil {
			writeWorkArtifactHTTPError(w, &core.Error{Code: "INVALID_REQUEST", Message: "valid nonzero request_id UUID required"})
			return
		}
		if strings.TrimSpace(body.Project) == "" {
			writeWorkArtifactHTTPError(w, &core.Error{Code: "INVALID_REQUEST", Message: "project required"})
			return
		}
		if body.ItemUUID == uuid.Nil {
			writeWorkArtifactHTTPError(w, &core.Error{Code: "INVALID_REQUEST", Message: "valid nonzero item_uuid required"})
			return
		}
		if body.RevisionID == uuid.Nil {
			writeWorkArtifactHTTPError(w, &core.Error{Code: "INVALID_REQUEST", Message: "valid nonzero revision_id required"})
			return
		}
		role := strings.ToLower(strings.TrimSpace(body.Role))
		if role != "input" && role != "output" && role != "evidence" {
			writeWorkArtifactHTTPError(w, &core.Error{Code: "INVALID_REQUEST", Message: "role must be input, output, or evidence"})
			return
		}
		link, err := store.LinkWorkArtifact(ctx, core.LinkWorkArtifactRequest{
			RequestID:   body.RequestID,
			Collection:  collection,
			ProjectSlug: body.Project,
			ItemUUID:    body.ItemUUID,
			RevisionID:  body.RevisionID,
			Role:        role,
			Rationale:   body.Rationale,
		})
		if err != nil {
			writeWorkArtifactHTTPError(w, err)
			return
		}
		_ = json.NewEncoder(w).Encode(link)

	case subpath == "unlink" && r.Method == http.MethodPost:
		var body artifactUnlinkBody
		if err := decodeStrictJSON(r, &body); err != nil {
			writeWorkArtifactHTTPError(w, err)
			return
		}
		reqUUID, err := uuid.Parse(body.RequestID)
		if err != nil || reqUUID == uuid.Nil {
			writeWorkArtifactHTTPError(w, &core.Error{Code: "INVALID_REQUEST", Message: "valid nonzero request_id UUID required"})
			return
		}
		if strings.TrimSpace(body.Project) == "" {
			writeWorkArtifactHTTPError(w, &core.Error{Code: "INVALID_REQUEST", Message: "project required"})
			return
		}
		if body.ItemUUID == uuid.Nil {
			writeWorkArtifactHTTPError(w, &core.Error{Code: "INVALID_REQUEST", Message: "valid nonzero item_uuid required"})
			return
		}
		if body.LinkID == uuid.Nil {
			writeWorkArtifactHTTPError(w, &core.Error{Code: "INVALID_REQUEST", Message: "valid nonzero link_id required"})
			return
		}
		if body.ExpectedRevision <= 0 {
			writeWorkArtifactHTTPError(w, &core.Error{Code: "INVALID_REQUEST", Message: "expected_revision must be positive"})
			return
		}
		link, err := store.UnlinkWorkArtifact(ctx, core.UnlinkWorkArtifactRequest{
			RequestID:        body.RequestID,
			Collection:       collection,
			ProjectSlug:      body.Project,
			ItemUUID:         body.ItemUUID,
			LinkID:           body.LinkID,
			ExpectedRevision: body.ExpectedRevision,
		})
		if err != nil {
			writeWorkArtifactHTTPError(w, err)
			return
		}
		_ = json.NewEncoder(w).Encode(link)

	case subpath == "purge" && r.Method == http.MethodPost:
		var body artifactPurgeBody
		if err := decodeStrictJSON(r, &body); err != nil {
			writeWorkArtifactHTTPError(w, err)
			return
		}
		reqUUID, err := uuid.Parse(body.RequestID)
		if err != nil || reqUUID == uuid.Nil {
			writeWorkArtifactHTTPError(w, &core.Error{Code: "INVALID_REQUEST", Message: "valid nonzero request_id UUID required"})
			return
		}
		if body.ArtifactID == uuid.Nil {
			writeWorkArtifactHTTPError(w, &core.Error{Code: "INVALID_REQUEST", Message: "valid nonzero artifact_id required"})
			return
		}
		err = store.PurgeWorkArtifact(ctx, core.PurgeWorkArtifactRequest{
			RequestID:  body.RequestID,
			Collection: collection,
			ArtifactID: body.ArtifactID,
		})
		if err != nil {
			writeWorkArtifactHTTPError(w, err)
			return
		}
		_ = json.NewEncoder(w).Encode(map[string]any{
			"purged":      true,
			"artifact_id": body.ArtifactID,
		})

	default:
		writeWorkArtifactHTTPError(w, &core.Error{Code: "NOT_FOUND", Message: fmt.Sprintf("route %q not found", r.URL.Path)})
	}
}
