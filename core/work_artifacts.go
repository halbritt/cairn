package core

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"net/url"
	"regexp"
	"strings"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
)

var (
	sha1HexRegex   = regexp.MustCompile("^[0-9a-fA-F]{40}$")
	sha256HexRegex = regexp.MustCompile("^[0-9a-fA-F]{64}$")
	digestRegex    = regexp.MustCompile("^[a-zA-Z0-9_-]+:[0-9a-fA-F]{32,128}$|^[0-9a-fA-F]{32,128}$")
)

func canonicalArtifactKey(kind string, parts ...string) string {
	tuple := append([]string{kind}, parts...)
	b, _ := json.Marshal(tuple)
	return string(b)
}

// WorkArtifact represents a named external deliverable with a canonical locator.
type WorkArtifact struct {
	ArtifactID    uuid.UUID  `json:"artifact_id"`
	Collection    string     `json:"collection"`
	Kind          string     `json:"kind"` // git, report, ci, cairn_result
	CanonicalKey  string     `json:"canonical_key"`
	DisplayName   string     `json:"display_name,omitempty"`
	GitRepo       string     `json:"git_repo,omitempty"`
	ReportLocator string     `json:"report_locator,omitempty"`
	CIProvider    string     `json:"ci_provider,omitempty"`
	CIRepo        string     `json:"ci_repo,omitempty"`
	CIRunID       string     `json:"ci_run_id,omitempty"`
	CairnRecordID *uuid.UUID `json:"cairn_record_id,omitempty"`
	CreatedAt     time.Time  `json:"created_at"`
	UpdatedAt     time.Time  `json:"updated_at"`
}

// WorkArtifactRevision represents an immutable revision of an external deliverable.
type WorkArtifactRevision struct {
	RevisionID        uuid.UUID      `json:"revision_id"`
	ArtifactID        uuid.UUID      `json:"artifact_id"`
	Collection        string         `json:"collection"`
	RevisionLabel     string         `json:"revision_label"`
	GitCommitOID      string         `json:"git_commit_oid,omitempty"`
	GitObjectFormat   string         `json:"git_object_format,omitempty"` // sha1, sha256
	GitBranchHint     string         `json:"git_branch_hint,omitempty"`
	ReportDigest      string         `json:"report_digest,omitempty"`
	ReportProvenance  string         `json:"report_provenance,omitempty"` // unverified, asserted, verified
	ReportMutable     bool           `json:"report_mutable"`
	CIAttempt         int            `json:"ci_attempt,omitempty"`
	CIStatus          string         `json:"ci_status,omitempty"`
	CIReportedAt      *time.Time     `json:"ci_reported_at,omitempty"`
	CairnVersion      *int           `json:"cairn_version,omitempty"`
	Metadata          map[string]any `json:"metadata,omitempty"`
	CreatedBy         string         `json:"created_by"`
	CreatedAt         time.Time      `json:"created_at"`
	Artifact          *WorkArtifact  `json:"artifact,omitempty"`
	Unavailable       bool           `json:"unavailable,omitempty"`
	UnavailableReason string         `json:"unavailable_reason,omitempty"`
}

// WorkArtifactLink represents an attributed input/output/evidence link between a work item and an artifact revision.
type WorkArtifactLink struct {
	LinkID           uuid.UUID             `json:"link_id"`
	Revision         int64                 `json:"revision"`
	Collection       string                `json:"collection"`
	BindingID        uuid.UUID             `json:"binding_id"`
	PlaneItemUUID    uuid.UUID             `json:"plane_item_uuid"`
	ArtifactID       uuid.UUID             `json:"artifact_id"`
	RevisionID       uuid.UUID             `json:"revision_id"`
	Role             string                `json:"role"`  // input, output, evidence
	State            string                `json:"state"` // active, removed
	Rationale        string                `json:"rationale,omitempty"`
	Author           string                `json:"author"`
	AssertedAt       time.Time             `json:"asserted_at"`
	CreatedAt        time.Time             `json:"created_at"`
	RemovedAt        *time.Time            `json:"removed_at,omitempty"`
	RemovedBy        string                `json:"removed_by,omitempty"`
	ArtifactRevision *WorkArtifactRevision `json:"artifact_revision,omitempty"`
	ItemIdentifier   string                `json:"item_identifier,omitempty"`
	ItemTitle        string                `json:"item_title,omitempty"`
}

// WorkArtifactLinkHistory records historical states of a work artifact link.
type WorkArtifactLinkHistory struct {
	HistoryID uuid.UUID `json:"history_id"`
	LinkID    uuid.UUID `json:"link_id"`
	Revision  int64     `json:"revision"`
	State     string    `json:"state"`
	Role      string    `json:"role"`
	Rationale string    `json:"rationale,omitempty"`
	Author    string    `json:"author"`
	CreatedAt time.Time `json:"created_at"`
}

// RegisterWorkArtifactRequest defines parameters for registering an external deliverable revision.
type RegisterWorkArtifactRequest struct {
	RequestID        string         `json:"request_id,omitempty"`
	Collection       string         `json:"collection,omitempty"`
	Kind             string         `json:"kind"` // git, report, ci, cairn_result
	DisplayName      string         `json:"display_name,omitempty"`
	RevisionLabel    string         `json:"revision_label,omitempty"`
	GitRepo          string         `json:"git_repo,omitempty"`
	GitCommitOID     string         `json:"git_commit_oid,omitempty"`
	GitObjectFormat  string         `json:"git_object_format,omitempty"` // sha1, sha256
	GitBranchHint    string         `json:"git_branch_hint,omitempty"`
	ReportLocator    string         `json:"report_locator,omitempty"`
	ReportDigest     string         `json:"report_digest,omitempty"`
	ReportProvenance string         `json:"report_provenance,omitempty"` // unverified, asserted, verified
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

// LinkWorkArtifactRequest defines parameters for linking an item to an artifact revision.
type LinkWorkArtifactRequest struct {
	RequestID   string     `json:"request_id,omitempty"`
	Collection  string     `json:"collection,omitempty"`
	ProjectSlug string     `json:"project_slug"`
	ItemUUID    uuid.UUID  `json:"item_uuid"`
	RevisionID  uuid.UUID  `json:"revision_id"`
	Role        string     `json:"role"` // input, output, evidence
	Rationale   string     `json:"rationale,omitempty"`
	AssertedAt  *time.Time `json:"asserted_at,omitempty"`
}

// UnlinkWorkArtifactRequest defines parameters for removing an artifact link.
type UnlinkWorkArtifactRequest struct {
	RequestID        string    `json:"request_id,omitempty"`
	Collection       string    `json:"collection,omitempty"`
	ProjectSlug      string    `json:"project_slug"`
	ItemUUID         uuid.UUID `json:"item_uuid"`
	LinkID           uuid.UUID `json:"link_id"`
	ExpectedRevision int64     `json:"expected_revision"`
}

// PurgeWorkArtifactRequest defines parameters for purging an external deliverable.
type PurgeWorkArtifactRequest struct {
	RequestID  string    `json:"request_id"`
	Collection string    `json:"collection,omitempty"`
	ArtifactID uuid.UUID `json:"artifact_id"`
}

// ListWorkArtifactLinksRequest defines query parameters for listing item artifact links.
type ListWorkArtifactLinksRequest struct {
	Collection     string    `json:"collection,omitempty"`
	ProjectSlug    string    `json:"project_slug"`
	ItemUUID       uuid.UUID `json:"item_uuid"`
	Role           string    `json:"role,omitempty"`
	IncludeRemoved bool      `json:"include_removed,omitempty"`
}

// ReverseImpactRequest defines query parameters for reverse impact keyset pagination.
type ReverseImpactRequest struct {
	Collection string    `json:"collection,omitempty"`
	RevisionID uuid.UUID `json:"revision_id"`
	Limit      int       `json:"limit,omitempty"`
	Cursor     string    `json:"cursor,omitempty"`
}

// ReverseImpactPage contains a page of reverse impact items and the next keyset cursor.
type ReverseImpactPage struct {
	Revision   WorkArtifactRevision `json:"revision"`
	Impacts    []WorkImpactItem     `json:"impacts"`
	NextCursor string               `json:"next_cursor,omitempty"`
	More       bool                 `json:"more"`
}

// WorkImpactItem represents an impacted work item linked to a specific artifact revision.
type WorkImpactItem struct {
	LinkID          uuid.UUID  `json:"link_id"`
	Role            string     `json:"role"`
	LinkState       string     `json:"link_state"`
	LinkAssertedAt  time.Time  `json:"link_asserted_at"`
	LinkAuthor      string     `json:"link_author"`
	BindingID       uuid.UUID  `json:"binding_id"`
	ProjectSlug     string     `json:"project_slug"`
	PlaneItemUUID   uuid.UUID  `json:"plane_item_uuid"`
	ItemIdentifier  string     `json:"item_identifier"`
	ItemTitle       string     `json:"item_title"`
	StateName       string     `json:"state_name"`
	StateGroup      string     `json:"state_group"`
	UpdatedAtSource *time.Time `json:"updated_at_source,omitempty"`
	SourceURL       string     `json:"source_url,omitempty"`
	LinkedSessions  []string   `json:"linked_sessions,omitempty"`
	OpenBlockers    []string   `json:"open_blockers,omitempty"`
}

type artifactImpactCursor struct {
	Schema     string    `json:"schema"`
	Collection string    `json:"collection"`
	RevisionID uuid.UUID `json:"revision_id"`
	Generation int64     `json:"generation"`
	AssertedAt time.Time `json:"asserted_at"`
	LinkID     uuid.UUID `json:"link_id"`
}

func (s *Store) validateArtifactKindAndFields(req *RegisterWorkArtifactRequest) (canonicalKey string, revisionLabel string, err error) {
	kind := strings.ToLower(strings.TrimSpace(req.Kind))
	if kind == "" {
		return "", "", failure("INVALID_REQUEST", "artifact kind required (git, report, ci, cairn_result)")
	}
	req.Kind = kind

	switch kind {
	case "git":
		repo := strings.TrimSpace(req.GitRepo)
		if repo == "" {
			return "", "", failure("INVALID_REQUEST", "git_repo required for git artifact")
		}
		oid := strings.TrimSpace(req.GitCommitOID)
		if oid == "" {
			return "", "", failure("INVALID_REQUEST", "git_commit_oid required for git artifact")
		}
		format := strings.ToLower(strings.TrimSpace(req.GitObjectFormat))
		if format == "" {
			if len(oid) == 40 {
				format = "sha1"
			} else if len(oid) == 64 {
				format = "sha256"
			} else {
				return "", "", failure("INVALID_REQUEST", "git commit OID must be full 40-char (sha1) or 64-char (sha256) hex string; abbreviations and branches are refused")
			}
		}
		if format == "sha1" {
			if len(oid) != 40 || !sha1HexRegex.MatchString(oid) {
				return "", "", failure("INVALID_REQUEST", "git commit OID for sha1 must be exactly 40 hex characters; abbreviations and branches are refused")
			}
		} else if format == "sha256" {
			if len(oid) != 64 || !sha256HexRegex.MatchString(oid) {
				return "", "", failure("INVALID_REQUEST", "git commit OID for sha256 must be exactly 64 hex characters; abbreviations and branches are refused")
			}
		} else {
			return "", "", failure("INVALID_REQUEST", fmt.Sprintf("invalid git object format %q: must be sha1 or sha256", format))
		}
		oid = strings.ToLower(oid)
		req.GitRepo = repo
		req.GitCommitOID = oid
		req.GitObjectFormat = format
		canonicalKey = canonicalArtifactKey("git", repo)
		if strings.TrimSpace(req.RevisionLabel) != "" {
			revisionLabel = strings.TrimSpace(req.RevisionLabel)
		} else {
			revisionLabel = oid
		}

	case "report":
		loc := strings.TrimSpace(req.ReportLocator)
		if loc == "" {
			return "", "", failure("INVALID_REQUEST", "report_locator required for report artifact")
		}
		u, parseErr := url.Parse(loc)
		if parseErr != nil {
			return "", "", failure("INVALID_REQUEST", fmt.Sprintf("invalid report_locator URL: %v", parseErr))
		}
		if strings.ToLower(u.Scheme) != "https" {
			return "", "", failure("INVALID_REQUEST", "report locator must use https scheme; other schemes refused")
		}
		if u.User != nil && (u.User.Username() != "" || u.User.String() != "") {
			return "", "", failure("INVALID_REQUEST", "report locator must not contain user credentials")
		}
		if u.Host == "" {
			return "", "", failure("INVALID_REQUEST", "report locator must specify host")
		}
		revLabel := strings.TrimSpace(req.RevisionLabel)
		if revLabel == "" {
			return "", "", failure("INVALID_REQUEST", "revision_label required for report artifact")
		}
		digest := strings.TrimSpace(req.ReportDigest)
		if digest != "" && !digestRegex.MatchString(digest) {
			return "", "", failure("INVALID_REQUEST", "invalid report digest format: must be hex or algorithm:hex")
		}
		prov := strings.ToLower(strings.TrimSpace(req.ReportProvenance))
		if prov == "" {
			prov = "unverified"
		}
		if prov != "unverified" && prov != "asserted" && prov != "verified" {
			return "", "", failure("INVALID_REQUEST", fmt.Sprintf("invalid report provenance %q: must be unverified, asserted, or verified", prov))
		}
		if prov == "verified" {
			// Do NOT allow uncorroborated caller-selected "verified" enum to masquerade as tool verification without local verification
			return "", "", failure("INVALID_REQUEST", "verified report provenance requires bounded local file digest verification; external URLs must declare 'unverified' or 'asserted'")
		}
		req.ReportLocator = u.String()
		req.ReportDigest = digest
		req.ReportProvenance = prov
		canonicalKey = canonicalArtifactKey("report", u.String())
		revisionLabel = revLabel

	case "ci":
		provider := strings.TrimSpace(req.CIProvider)
		if provider == "" {
			return "", "", failure("INVALID_REQUEST", "ci_provider required for ci artifact")
		}
		repo := strings.TrimSpace(req.CIRepo)
		if repo == "" {
			return "", "", failure("INVALID_REQUEST", "ci_repo required for ci artifact")
		}
		runID := strings.TrimSpace(req.CIRunID)
		if runID == "" {
			return "", "", failure("INVALID_REQUEST", "ci_run_id required for ci artifact")
		}
		attempt := req.CIAttempt
		if attempt <= 0 {
			attempt = 1
		}
		req.CIProvider = provider
		req.CIRepo = repo
		req.CIRunID = runID
		req.CIAttempt = attempt
		canonicalKey = canonicalArtifactKey("ci", provider, repo, runID)
		if strings.TrimSpace(req.RevisionLabel) != "" {
			revisionLabel = strings.TrimSpace(req.RevisionLabel)
		} else {
			revisionLabel = fmt.Sprintf("attempt/%d", attempt)
		}

	case "cairn_result":
		if req.CairnRecordID == nil || *req.CairnRecordID == uuid.Nil {
			return "", "", failure("INVALID_REQUEST", "cairn_record_id required for cairn_result artifact")
		}
		if req.CairnVersion == nil || *req.CairnVersion <= 0 {
			return "", "", failure("INVALID_REQUEST", "cairn_version must be positive integer")
		}
		canonicalKey = canonicalArtifactKey("cairn_result", req.CairnRecordID.String())
		revisionLabel = fmt.Sprintf("v%d", *req.CairnVersion)

	default:
		return "", "", failure("INVALID_REQUEST", fmt.Sprintf("unknown artifact kind %q", kind))
	}

	return canonicalKey, revisionLabel, nil
}

func checkCairnResultEligibilityTx(ctx context.Context, tx pgx.Tx, collection string, recordID uuid.UUID, exactVersion int, forUpdate bool) (eligible bool, reason string, err error) {
	query := `
		SELECT 
			m.lifecycle,
			m.current_version,
			v_exact.repo,
			(v_exact.payload_deleted_by IS NOT NULL),
			v_cur.repo,
			(v_cur.payload_deleted_by IS NOT NULL),
			EXISTS(
				SELECT 1 FROM cairn.deletion_dependency d
				WHERE d.record_id = m.record_id AND (d.version = $2 OR d.version = m.current_version)
			)
		FROM cairn.memory_record m
		JOIN cairn.record_version v_exact ON v_exact.record_id = m.record_id AND v_exact.version = $2
		JOIN cairn.record_version v_cur ON v_cur.record_id = m.record_id AND v_cur.version = m.current_version
		WHERE m.record_id = $1`
	if forUpdate {
		query += " FOR SHARE OF m, v_exact, v_cur"
	}

	var lifecycle string
	var curVersion int
	var exactRepo, curRepo string
	var exactDeleted, curDeleted bool
	var hasDep bool

	err = tx.QueryRow(ctx, query, recordID, exactVersion).Scan(
		&lifecycle, &curVersion, &exactRepo, &exactDeleted, &curRepo, &curDeleted, &hasDep,
	)
	if errors.Is(err, pgx.ErrNoRows) {
		return false, "record or version not found", nil
	}
	if err != nil {
		return false, "", err
	}
	if lifecycle == "tombstoned" {
		return false, "referenced record was forgotten", nil
	}
	if exactRepo != collection || curRepo != collection {
		return false, "referenced record repository outside collection scope", nil
	}
	if exactDeleted || curDeleted {
		return false, "referenced record payload excluded by deletion", nil
	}
	if hasDep {
		return false, "referenced record has active deletion dependency", nil
	}
	return true, "", nil
}

// RegisterWorkArtifact registers an external deliverable and an immutable revision.
func (s *Store) RegisterWorkArtifact(ctx context.Context, req RegisterWorkArtifactRequest) (WorkArtifactRevision, error) {
	if err := s.requireOperator(); err != nil {
		return WorkArtifactRevision{}, err
	}
	if req.Collection == "" {
		req.Collection = s.channel.Repo
		if req.Collection == "" {
			req.Collection = "default"
		}
	}
	if err := s.checkRepo(req.Collection); err != nil {
		return WorkArtifactRevision{}, err
	}

	canonicalKey, revLabel, err := s.validateArtifactKindAndFields(&req)
	if err != nil {
		return WorkArtifactRevision{}, err
	}

	if req.RequestID == "" {
		req.RequestID = uuid.NewString()
	}

	guard := func(tx pgx.Tx) error {
		var rawResp []byte
		var prevDigest []byte
		err := tx.QueryRow(ctx, `
			SELECT request_digest, response
			FROM cairn.mutation_request
			WHERE caller = $1 AND operation = $2 AND request_id = $3
		`, s.channel.Principal, "work-artifact-register", req.RequestID).Scan(&prevDigest, &rawResp)
		if errors.Is(err, pgx.ErrNoRows) {
			return nil
		}
		if err != nil {
			return err
		}
		canonical, err := json.Marshal(req)
		if err != nil {
			return err
		}
		expectedDigest := sha256.Sum256(canonical)
		if !bytes.Equal(prevDigest, expectedDigest[:]) {
			return nil
		}
		if len(rawResp) == 0 {
			return nil
		}

		var cachedRev WorkArtifactRevision
		if err := json.Unmarshal(rawResp, &cachedRev); err == nil && cachedRev.ArtifactID != uuid.Nil {
			var fenced bool
			err = tx.QueryRow(ctx, `
				SELECT true FROM cairn.work_artifact_purge_fence
				WHERE collection = $1 AND (artifact_id = $2 OR canonical_key = $3)
			`, req.Collection, cachedRev.ArtifactID, canonicalKey).Scan(&fenced)
			if err == nil && fenced {
				return failure("PAYLOAD_UNAVAILABLE", fmt.Sprintf("artifact %s has been purged", cachedRev.ArtifactID))
			}
			if err != nil && !errors.Is(err, pgx.ErrNoRows) {
				return err
			}
		}

		if req.Kind == "cairn_result" && req.CairnRecordID != nil && req.CairnVersion != nil {
			eligible, reason, err := checkCairnResultEligibilityTx(ctx, tx, req.Collection, *req.CairnRecordID, *req.CairnVersion, false)
			if err != nil {
				return err
			}
			if !eligible {
				if strings.Contains(reason, "repository outside") {
					return failure("AUTHORITY_DENIED", fmt.Sprintf("cairn record %s version %d is outside collection %q", req.CairnRecordID, *req.CairnVersion, req.Collection))
				}
				return failure("PAYLOAD_UNAVAILABLE", fmt.Sprintf("cairn record %s version %d: %s", req.CairnRecordID, *req.CairnVersion, reason))
			}
		}
		return nil
	}

	return mutate(ctx, s, "work-artifact-register", req.RequestID, req, func(tx pgx.Tx) (WorkArtifactRevision, error) {
		if err := s.checkWorkArtifactFeatureTx(ctx, tx); err != nil {
			return WorkArtifactRevision{}, err
		}

		// Take deterministic advisory lock before checking purge fence
		lockKey := fmt.Sprintf("work-artifact:%s:%s", req.Collection, canonicalKey)
		if _, err := tx.Exec(ctx, `SELECT pg_advisory_xact_lock(hashtextextended($1, 0))`, lockKey); err != nil {
			return WorkArtifactRevision{}, err
		}

		// Check purge fence (exclusion fence prevents replay of purged deliverables)
		var fenced bool
		err = tx.QueryRow(ctx, `SELECT true FROM cairn.work_artifact_purge_fence WHERE collection = $1 AND canonical_key = $2`, req.Collection, canonicalKey).Scan(&fenced)
		if err == nil && fenced {
			return WorkArtifactRevision{}, failure("INVALID_REQUEST", fmt.Sprintf("artifact with canonical key %q has been purged and cannot be re-registered", canonicalKey))
		}
		if err != nil && !errors.Is(err, pgx.ErrNoRows) {
			return WorkArtifactRevision{}, err
		}

		// If cairn_result, verify eligibility in this transaction with FOR SHARE locks
		if req.Kind == "cairn_result" && req.CairnRecordID != nil && req.CairnVersion != nil {
			eligible, reason, err := checkCairnResultEligibilityTx(ctx, tx, req.Collection, *req.CairnRecordID, *req.CairnVersion, true)
			if err != nil {
				return WorkArtifactRevision{}, err
			}
			if !eligible {
				if reason == "record or version not found" {
					return WorkArtifactRevision{}, failure("NOT_FOUND", fmt.Sprintf("cairn record %s version %d not found", req.CairnRecordID, *req.CairnVersion))
				}
				if strings.Contains(reason, "repository outside") {
					return WorkArtifactRevision{}, failure("AUTHORITY_DENIED", fmt.Sprintf("cairn record %s version %d is outside collection %q", req.CairnRecordID, *req.CairnVersion, req.Collection))
				}
				return WorkArtifactRevision{}, failure("PAYLOAD_UNAVAILABLE", fmt.Sprintf("cairn record %s version %d: %s", req.CairnRecordID, *req.CairnVersion, reason))
			}
		}

		// 1. Insert or get work_artifact
		var gitRepoVal, repLocVal, ciProvVal, ciRepoVal, ciRunIDVal *string
		if req.GitRepo != "" {
			gitRepoVal = &req.GitRepo
		}
		if req.ReportLocator != "" {
			repLocVal = &req.ReportLocator
		}
		if req.CIProvider != "" {
			ciProvVal = &req.CIProvider
		}
		if req.CIRepo != "" {
			ciRepoVal = &req.CIRepo
		}
		if req.CIRunID != "" {
			ciRunIDVal = &req.CIRunID
		}

		var artifact WorkArtifact
		err := tx.QueryRow(ctx, `
			INSERT INTO cairn.work_artifact (collection, kind, canonical_key, display_name, git_repo, report_locator, ci_provider, ci_repo, ci_run_id, cairn_record_id, created_at, updated_at)
			VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, clock_timestamp(), clock_timestamp())
			ON CONFLICT (collection, canonical_key) DO UPDATE
			SET updated_at = clock_timestamp()
			RETURNING artifact_id, collection, kind, canonical_key, display_name,
			          COALESCE(git_repo, ''), COALESCE(report_locator, ''),
			          COALESCE(ci_provider, ''), COALESCE(ci_repo, ''), COALESCE(ci_run_id, ''),
			          cairn_record_id, created_at, updated_at
		`, req.Collection, req.Kind, canonicalKey, req.DisplayName, gitRepoVal, repLocVal, ciProvVal, ciRepoVal, ciRunIDVal, req.CairnRecordID).
			Scan(&artifact.ArtifactID, &artifact.Collection, &artifact.Kind, &artifact.CanonicalKey, &artifact.DisplayName,
				&artifact.GitRepo, &artifact.ReportLocator, &artifact.CIProvider, &artifact.CIRepo, &artifact.CIRunID,
				&artifact.CairnRecordID, &artifact.CreatedAt, &artifact.UpdatedAt)
		if err != nil {
			return WorkArtifactRevision{}, err
		}

		// 2. Check if revision already exists (safe duplicate retry)
		var existingRev WorkArtifactRevision
		var metaRaw []byte
		var objFmt, branchHint, repDigest, repProv *string
		var repMut *bool
		var ciAtt *int
		var ciStat *string
		var ciRepAt *time.Time
		var cairnVer *int
		var gitCommitOID *string

		err = tx.QueryRow(ctx, `
			SELECT revision_id, artifact_id, collection, revision_label, git_commit_oid, git_object_format, git_branch_hint,
			       report_digest, report_provenance, report_mutable, ci_attempt, ci_status, ci_reported_at,
			       cairn_version, metadata, created_by, created_at
			FROM cairn.work_artifact_revision
			WHERE artifact_id = $1 AND revision_label = $2
			FOR SHARE
		`, artifact.ArtifactID, revLabel).Scan(
			&existingRev.RevisionID, &existingRev.ArtifactID, &existingRev.Collection, &existingRev.RevisionLabel,
			&gitCommitOID, &objFmt, &branchHint, &repDigest, &repProv, &repMut,
			&ciAtt, &ciStat, &ciRepAt, &cairnVer, &metaRaw, &existingRev.CreatedBy, &existingRev.CreatedAt)
		if err == nil {
			if gitCommitOID != nil {
				existingRev.GitCommitOID = *gitCommitOID
			}
			if objFmt != nil {
				existingRev.GitObjectFormat = *objFmt
			}
			if branchHint != nil {
				existingRev.GitBranchHint = *branchHint
			}
			if repDigest != nil {
				existingRev.ReportDigest = *repDigest
			}
			if repProv != nil {
				existingRev.ReportProvenance = *repProv
			}
			if repMut != nil {
				existingRev.ReportMutable = *repMut
			}
			if ciAtt != nil {
				existingRev.CIAttempt = *ciAtt
			}
			if ciStat != nil {
				existingRev.CIStatus = *ciStat
			}
			existingRev.CIReportedAt = ciRepAt
			existingRev.CairnVersion = cairnVer
			_ = json.Unmarshal(metaRaw, &existingRev.Metadata)
			existingRev.Artifact = &artifact

			// Conflicting immutable fields must refuse atomically (ART-IDENTITY)
			if req.Kind == "git" {
				if existingRev.GitCommitOID != req.GitCommitOID {
					return WorkArtifactRevision{}, failure("CONFLICT", fmt.Sprintf("artifact revision %q already exists with different git commit OID", revLabel))
				}
				if req.GitObjectFormat != "" && existingRev.GitObjectFormat != req.GitObjectFormat {
					return WorkArtifactRevision{}, failure("CONFLICT", fmt.Sprintf("artifact revision %q already exists with different git object format", revLabel))
				}
			}
			if req.Kind == "report" {
				if req.ReportDigest != "" && existingRev.ReportDigest != req.ReportDigest {
					return WorkArtifactRevision{}, failure("CONFLICT", fmt.Sprintf("artifact revision %q already exists with different report digest", revLabel))
				}
				if req.ReportProvenance != "" && existingRev.ReportProvenance != req.ReportProvenance {
					return WorkArtifactRevision{}, failure("CONFLICT", fmt.Sprintf("artifact revision %q already exists with different report provenance", revLabel))
				}
			}
			if req.Kind == "ci" {
				if existingRev.CIAttempt != req.CIAttempt {
					return WorkArtifactRevision{}, failure("CONFLICT", fmt.Sprintf("artifact revision %q already exists with different CI attempt (%d vs %d)", revLabel, existingRev.CIAttempt, req.CIAttempt))
				}
				if req.CIStatus != "" && existingRev.CIStatus != req.CIStatus {
					return WorkArtifactRevision{}, failure("CONFLICT", fmt.Sprintf("artifact revision %q already exists with different CI status (%s vs %s)", revLabel, existingRev.CIStatus, req.CIStatus))
				}
			}
			if req.Kind == "cairn_result" {
				if req.CairnVersion != nil && (existingRev.CairnVersion == nil || *existingRev.CairnVersion != *req.CairnVersion) {
					return WorkArtifactRevision{}, failure("CONFLICT", fmt.Sprintf("artifact revision %q already exists with different cairn version", revLabel))
				}
			}

			return existingRev, nil
		}
		if !errors.Is(err, pgx.ErrNoRows) {
			return WorkArtifactRevision{}, err
		}

		// 3. Insert new revision
		reportMutable := true
		if req.Kind == "report" {
			if req.ReportDigest != "" && req.ReportProvenance == "verified" {
				reportMutable = false
			}
		}

		var gitCommitOIDVal, objFmtVal, branchHintVal, repDigestVal *string
		if req.GitCommitOID != "" {
			gitCommitOIDVal = &req.GitCommitOID
		}
		if req.GitObjectFormat != "" {
			objFmtVal = &req.GitObjectFormat
		}
		if req.GitBranchHint != "" {
			branchHintVal = &req.GitBranchHint
		}
		if req.ReportDigest != "" {
			repDigestVal = &req.ReportDigest
		}
		repProvVal := req.ReportProvenance
		if repProvVal == "" {
			repProvVal = "unverified"
		}
		var ciStatusVal *string
		if req.CIStatus != "" {
			ciStatusVal = &req.CIStatus
		}

		metaJSON, _ := json.Marshal(req.Metadata)
		var newRev WorkArtifactRevision
		err = tx.QueryRow(ctx, `
			INSERT INTO cairn.work_artifact_revision (
				artifact_id, collection, revision_label,
				git_commit_oid, git_object_format, git_branch_hint,
				report_digest, report_provenance, report_mutable,
				ci_attempt, ci_status, ci_reported_at,
				cairn_version, metadata, created_by, created_at
			) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, clock_timestamp())
			RETURNING revision_id, artifact_id, collection, revision_label,
			          git_commit_oid, git_object_format, git_branch_hint,
			          report_digest, report_provenance, report_mutable,
			          ci_attempt, ci_status, ci_reported_at,
			          cairn_version, metadata, created_by, created_at
		`, artifact.ArtifactID, req.Collection, revLabel,
			gitCommitOIDVal, objFmtVal, branchHintVal,
			repDigestVal, repProvVal, reportMutable,
			req.CIAttempt, ciStatusVal, req.CIReportedAt,
			req.CairnVersion, metaJSON, s.channel.Principal).Scan(
			&newRev.RevisionID, &newRev.ArtifactID, &newRev.Collection, &newRev.RevisionLabel,
			&gitCommitOID, &objFmt, &branchHint, &repDigest, &repProv, &repMut,
			&ciAtt, &ciStat, &ciRepAt, &cairnVer, &metaRaw, &newRev.CreatedBy, &newRev.CreatedAt)
		if err != nil {
			return WorkArtifactRevision{}, err
		}

		if gitCommitOID != nil {
			newRev.GitCommitOID = *gitCommitOID
		}
		if objFmt != nil {
			newRev.GitObjectFormat = *objFmt
		}
		if branchHint != nil {
			newRev.GitBranchHint = *branchHint
		}
		if repDigest != nil {
			newRev.ReportDigest = *repDigest
		}
		if repProv != nil {
			newRev.ReportProvenance = *repProv
		}
		if repMut != nil {
			newRev.ReportMutable = *repMut
		}
		if ciAtt != nil {
			newRev.CIAttempt = *ciAtt
		}
		if ciStat != nil {
			newRev.CIStatus = *ciStat
		}
		newRev.CIReportedAt = ciRepAt
		newRev.CairnVersion = cairnVer
		_ = json.Unmarshal(metaRaw, &newRev.Metadata)
		newRev.Artifact = &artifact
		return newRev, nil
	}, guard)
}

// GetWorkArtifact retrieves a work artifact by ID.
func (s *Store) GetWorkArtifact(ctx context.Context, collection string, artifactID uuid.UUID) (WorkArtifact, error) {
	if err := s.requireOperator(); err != nil {
		return WorkArtifact{}, err
	}
	if collection == "" {
		collection = s.channel.Repo
		if collection == "" {
			collection = "default"
		}
	}
	if err := s.checkRepo(collection); err != nil {
		return WorkArtifact{}, err
	}

	tx, err := s.beginLevel(ctx, pgx.RepeatableRead)
	if err != nil {
		return WorkArtifact{}, err
	}
	defer tx.Rollback(ctx)
	if err := s.checkWorkReadableTx(ctx, tx); err != nil {
		return WorkArtifact{}, err
	}

	var a WorkArtifact
	err = tx.QueryRow(ctx, `
		SELECT artifact_id, collection, kind, canonical_key, display_name,
		       COALESCE(git_repo, ''), COALESCE(report_locator, ''),
		       COALESCE(ci_provider, ''), COALESCE(ci_repo, ''), COALESCE(ci_run_id, ''),
		       cairn_record_id, created_at, updated_at
		FROM cairn.work_artifact
		WHERE collection = $1 AND artifact_id = $2
	`, collection, artifactID).Scan(
		&a.ArtifactID, &a.Collection, &a.Kind, &a.CanonicalKey, &a.DisplayName,
		&a.GitRepo, &a.ReportLocator, &a.CIProvider, &a.CIRepo, &a.CIRunID,
		&a.CairnRecordID, &a.CreatedAt, &a.UpdatedAt)
	if errors.Is(err, pgx.ErrNoRows) {
		return WorkArtifact{}, failure("NOT_FOUND", fmt.Sprintf("artifact %s not found in collection %q", artifactID, collection))
	}
	if err != nil {
		return WorkArtifact{}, err
	}
	if a.Kind == "cairn_result" && a.CairnRecordID != nil {
		var curVer int
		err = tx.QueryRow(ctx, `SELECT current_version FROM cairn.memory_record WHERE record_id = $1`, a.CairnRecordID).Scan(&curVer)
		if errors.Is(err, pgx.ErrNoRows) {
			a.DisplayName = ""
		} else if err != nil {
			return WorkArtifact{}, err
		} else {
			eligible, _, err := checkCairnResultEligibilityTx(ctx, tx, collection, *a.CairnRecordID, curVer, false)
			if err != nil {
				return WorkArtifact{}, err
			}
			if !eligible {
				a.DisplayName = ""
			}
		}
	}
	return a, tx.Commit(ctx)
}

// GetWorkArtifactByCanonicalKey retrieves a work artifact by its canonical key.
func (s *Store) GetWorkArtifactByCanonicalKey(ctx context.Context, collection string, canonicalKey string) (WorkArtifact, error) {
	if err := s.requireOperator(); err != nil {
		return WorkArtifact{}, err
	}
	if collection == "" {
		collection = s.channel.Repo
		if collection == "" {
			collection = "default"
		}
	}
	if err := s.checkRepo(collection); err != nil {
		return WorkArtifact{}, err
	}

	tx, err := s.beginLevel(ctx, pgx.RepeatableRead)
	if err != nil {
		return WorkArtifact{}, err
	}
	defer tx.Rollback(ctx)
	if err := s.checkWorkReadableTx(ctx, tx); err != nil {
		return WorkArtifact{}, err
	}

	var a WorkArtifact
	err = tx.QueryRow(ctx, `
		SELECT artifact_id, collection, kind, canonical_key, display_name,
		       COALESCE(git_repo, ''), COALESCE(report_locator, ''),
		       COALESCE(ci_provider, ''), COALESCE(ci_repo, ''), COALESCE(ci_run_id, ''),
		       cairn_record_id, created_at, updated_at
		FROM cairn.work_artifact
		WHERE collection = $1 AND canonical_key = $2
	`, collection, canonicalKey).Scan(
		&a.ArtifactID, &a.Collection, &a.Kind, &a.CanonicalKey, &a.DisplayName,
		&a.GitRepo, &a.ReportLocator, &a.CIProvider, &a.CIRepo, &a.CIRunID,
		&a.CairnRecordID, &a.CreatedAt, &a.UpdatedAt)
	if errors.Is(err, pgx.ErrNoRows) {
		return WorkArtifact{}, failure("NOT_FOUND", fmt.Sprintf("artifact with key %q not found in collection %q", canonicalKey, collection))
	}
	if err != nil {
		return WorkArtifact{}, err
	}
	if a.Kind == "cairn_result" && a.CairnRecordID != nil {
		var curVer int
		err = tx.QueryRow(ctx, `SELECT current_version FROM cairn.memory_record WHERE record_id = $1`, a.CairnRecordID).Scan(&curVer)
		if errors.Is(err, pgx.ErrNoRows) {
			a.DisplayName = ""
		} else if err != nil {
			return WorkArtifact{}, err
		} else {
			eligible, _, err := checkCairnResultEligibilityTx(ctx, tx, collection, *a.CairnRecordID, curVer, false)
			if err != nil {
				return WorkArtifact{}, err
			}
			if !eligible {
				a.DisplayName = ""
			}
		}
	}
	return a, tx.Commit(ctx)
}

// GetWorkArtifactRevision retrieves an exact artifact revision by ID.
func (s *Store) GetWorkArtifactRevision(ctx context.Context, collection string, revisionID uuid.UUID) (WorkArtifactRevision, error) {
	if err := s.requireOperator(); err != nil {
		return WorkArtifactRevision{}, err
	}
	if collection == "" {
		collection = s.channel.Repo
		if collection == "" {
			collection = "default"
		}
	}
	if err := s.checkRepo(collection); err != nil {
		return WorkArtifactRevision{}, err
	}

	tx, err := s.beginLevel(ctx, pgx.RepeatableRead)
	if err != nil {
		return WorkArtifactRevision{}, err
	}
	defer tx.Rollback(ctx)
	if err := s.checkWorkReadableTx(ctx, tx); err != nil {
		return WorkArtifactRevision{}, err
	}

	var rev WorkArtifactRevision
	var metaRaw []byte
	var gitCommitOID, objFmt, branchHint, repDigest, repProv *string
	var repMut *bool
	var ciAtt *int
	var ciStat *string
	var ciRepAt *time.Time
	var cairnVer *int

	var a WorkArtifact
	err = tx.QueryRow(ctx, `
		SELECT r.revision_id, r.artifact_id, r.collection, r.revision_label,
		       r.git_commit_oid, r.git_object_format, r.git_branch_hint,
		       r.report_digest, r.report_provenance, r.report_mutable,
		       r.ci_attempt, r.ci_status, r.ci_reported_at,
		       r.cairn_version, r.metadata, r.created_by, r.created_at,
		       a.artifact_id, a.collection, a.kind, a.canonical_key, a.display_name,
		       COALESCE(a.git_repo, ''), COALESCE(a.report_locator, ''),
		       COALESCE(a.ci_provider, ''), COALESCE(a.ci_repo, ''), COALESCE(a.ci_run_id, ''),
		       a.cairn_record_id, a.created_at, a.updated_at
		FROM cairn.work_artifact_revision r
		JOIN cairn.work_artifact a USING(artifact_id)
		WHERE r.collection = $1 AND r.revision_id = $2
	`, collection, revisionID).Scan(
		&rev.RevisionID, &rev.ArtifactID, &rev.Collection, &rev.RevisionLabel,
		&gitCommitOID, &objFmt, &branchHint, &repDigest, &repProv, &repMut,
		&ciAtt, &ciStat, &ciRepAt, &cairnVer, &metaRaw, &rev.CreatedBy, &rev.CreatedAt,
		&a.ArtifactID, &a.Collection, &a.Kind, &a.CanonicalKey, &a.DisplayName,
		&a.GitRepo, &a.ReportLocator, &a.CIProvider, &a.CIRepo, &a.CIRunID,
		&a.CairnRecordID, &a.CreatedAt, &a.UpdatedAt)
	if errors.Is(err, pgx.ErrNoRows) {
		return WorkArtifactRevision{}, failure("NOT_FOUND", fmt.Sprintf("artifact revision %s not found in collection %q", revisionID, collection))
	}
	if err != nil {
		return WorkArtifactRevision{}, err
	}

	if gitCommitOID != nil {
		rev.GitCommitOID = *gitCommitOID
	}
	if objFmt != nil {
		rev.GitObjectFormat = *objFmt
	}
	if branchHint != nil {
		rev.GitBranchHint = *branchHint
	}
	if repDigest != nil {
		rev.ReportDigest = *repDigest
	}
	if repProv != nil {
		rev.ReportProvenance = *repProv
	}
	if repMut != nil {
		rev.ReportMutable = *repMut
	}
	if ciAtt != nil {
		rev.CIAttempt = *ciAtt
	}
	if ciStat != nil {
		rev.CIStatus = *ciStat
	}
	rev.CIReportedAt = ciRepAt
	rev.CairnVersion = cairnVer
	_ = json.Unmarshal(metaRaw, &rev.Metadata)
	rev.Artifact = &a

	// Check eligibility if cairn_result (revalidate sensitivity, visibility, and deletion)
	if a.Kind == "cairn_result" && a.CairnRecordID != nil && rev.CairnVersion != nil {
		eligible, reason, err := checkCairnResultEligibilityTx(ctx, tx, collection, *a.CairnRecordID, *rev.CairnVersion, false)
		if err != nil {
			return WorkArtifactRevision{}, err
		}
		if !eligible {
			rev.Unavailable = true
			rev.UnavailableReason = reason
			rev.Metadata = nil
			if rev.Artifact != nil {
				rev.Artifact.DisplayName = ""
			}
		}
	}

	return rev, tx.Commit(ctx)
}

// ValidateArtifactLinkTx validates parameters for linking an item to an artifact revision within an open transaction.
// Exposes private transaction-scoped verification for composition (e.g. WC-05 advice apply).
func (s *Store) ValidateArtifactLinkTx(ctx context.Context, tx pgx.Tx, collection string, req LinkWorkArtifactRequest) (*WorkArtifactRevision, uuid.UUID, uuid.UUID, error) {
	if collection == "" {
		collection = s.channel.Repo
		if collection == "" {
			collection = "default"
		}
	}
	if err := s.checkRepo(collection); err != nil {
		return nil, uuid.Nil, uuid.Nil, err
	}
	if strings.TrimSpace(req.ProjectSlug) == "" {
		return nil, uuid.Nil, uuid.Nil, failure("INVALID_REQUEST", "project slug required")
	}
	if req.ItemUUID == uuid.Nil {
		return nil, uuid.Nil, uuid.Nil, failure("INVALID_REQUEST", "item UUID required")
	}
	if req.RevisionID == uuid.Nil {
		return nil, uuid.Nil, uuid.Nil, failure("INVALID_REQUEST", "artifact revision ID required")
	}

	role := strings.ToLower(strings.TrimSpace(req.Role))
	if role != "input" && role != "output" && role != "evidence" {
		return nil, uuid.Nil, uuid.Nil, failure("INVALID_REQUEST", fmt.Sprintf("invalid artifact link role %q: must be input, output, or evidence", req.Role))
	}

	// 0. Enforce feature admission (ART-ADMISSION)
	if err := s.checkWorkArtifactFeatureTx(ctx, tx); err != nil {
		return nil, uuid.Nil, uuid.Nil, err
	}

	// 1. Verify project exists and is active (ART-ADMISSION)
	var bindingID uuid.UUID
	var projStatus string
	err := tx.QueryRow(ctx, `SELECT binding_id, status FROM cairn.work_project WHERE collection = $1 AND local_slug = $2 FOR SHARE`, collection, req.ProjectSlug).Scan(&bindingID, &projStatus)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, uuid.Nil, uuid.Nil, failure("NOT_FOUND", fmt.Sprintf("work project %q not found in collection %q", req.ProjectSlug, collection))
	}
	if err != nil {
		return nil, uuid.Nil, uuid.Nil, err
	}
	if projStatus != "active" {
		return nil, uuid.Nil, uuid.Nil, failure("PROJECT_DISABLED", fmt.Sprintf("work project %q is %s", req.ProjectSlug, projStatus))
	}

	// 2. Verify target item exists in project
	var planeItemUUID uuid.UUID
	err = tx.QueryRow(ctx, `SELECT plane_item_uuid FROM cairn.work_item WHERE binding_id = $1 AND (item_id = $2 OR plane_item_uuid = $2)`, bindingID, req.ItemUUID).Scan(&planeItemUUID)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, uuid.Nil, uuid.Nil, failure("NOT_FOUND", fmt.Sprintf("work item %s not found in project %s", req.ItemUUID, req.ProjectSlug))
	}
	if err != nil {
		return nil, uuid.Nil, uuid.Nil, err
	}

	// 3. Verify artifact revision exists and lock it
	var rev WorkArtifactRevision
	var a WorkArtifact
	var metaRaw []byte
	var gitCommitOID, objFmt, branchHint, repDigest, repProv *string
	var repMut *bool
	var ciAtt *int
	var ciStat *string
	var ciRepAt *time.Time
	var cairnVer *int

	err = tx.QueryRow(ctx, `
		SELECT r.revision_id, r.artifact_id, r.collection, r.revision_label,
		       r.git_commit_oid, r.git_object_format, r.git_branch_hint,
		       r.report_digest, r.report_provenance, r.report_mutable,
		       r.ci_attempt, r.ci_status, r.ci_reported_at,
		       r.cairn_version, r.metadata, r.created_by, r.created_at,
		       a.artifact_id, a.collection, a.kind, a.canonical_key, a.display_name,
		       COALESCE(a.git_repo, ''), COALESCE(a.report_locator, ''),
		       COALESCE(a.ci_provider, ''), COALESCE(a.ci_repo, ''), COALESCE(a.ci_run_id, ''),
		       a.cairn_record_id, a.created_at, a.updated_at
		FROM cairn.work_artifact_revision r
		JOIN cairn.work_artifact a USING(artifact_id)
		WHERE r.collection = $1 AND r.revision_id = $2
		FOR SHARE
	`, collection, req.RevisionID).Scan(
		&rev.RevisionID, &rev.ArtifactID, &rev.Collection, &rev.RevisionLabel,
		&gitCommitOID, &objFmt, &branchHint, &repDigest, &repProv, &repMut,
		&ciAtt, &ciStat, &ciRepAt, &cairnVer, &metaRaw, &rev.CreatedBy, &rev.CreatedAt,
		&a.ArtifactID, &a.Collection, &a.Kind, &a.CanonicalKey, &a.DisplayName,
		&a.GitRepo, &a.ReportLocator, &a.CIProvider, &a.CIRepo, &a.CIRunID,
		&a.CairnRecordID, &a.CreatedAt, &a.UpdatedAt)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, uuid.Nil, uuid.Nil, failure("NOT_FOUND", fmt.Sprintf("artifact revision %s not found in collection %q", req.RevisionID, collection))
	}
	if err != nil {
		return nil, uuid.Nil, uuid.Nil, err
	}

	if gitCommitOID != nil {
		rev.GitCommitOID = *gitCommitOID
	}
	if objFmt != nil {
		rev.GitObjectFormat = *objFmt
	}
	if branchHint != nil {
		rev.GitBranchHint = *branchHint
	}
	if repDigest != nil {
		rev.ReportDigest = *repDigest
	}
	if repProv != nil {
		rev.ReportProvenance = *repProv
	}
	if repMut != nil {
		rev.ReportMutable = *repMut
	}
	if ciAtt != nil {
		rev.CIAttempt = *ciAtt
	}
	if ciStat != nil {
		rev.CIStatus = *ciStat
	}
	rev.CIReportedAt = ciRepAt
	rev.CairnVersion = cairnVer
	_ = json.Unmarshal(metaRaw, &rev.Metadata)
	rev.Artifact = &a

	// If cairn_result, lock source record FOR SHARE against concurrent Forget (ART-ADMISSION)
	if a.Kind == "cairn_result" && a.CairnRecordID != nil && rev.CairnVersion != nil {
		eligible, reason, err := checkCairnResultEligibilityTx(ctx, tx, collection, *a.CairnRecordID, *rev.CairnVersion, true)
		if err != nil {
			return nil, uuid.Nil, uuid.Nil, err
		}
		if !eligible {
			if strings.Contains(reason, "repository outside") {
				return nil, uuid.Nil, uuid.Nil, failure("AUTHORITY_DENIED", reason)
			}
			return nil, uuid.Nil, uuid.Nil, failure("PAYLOAD_UNAVAILABLE", reason)
		}
	}

	return &rev, bindingID, planeItemUUID, nil
}

// LinkWorkArtifactTx creates or reactivates an artifact link within an open transaction.
func (s *Store) LinkWorkArtifactTx(ctx context.Context, tx pgx.Tx, collection string, req LinkWorkArtifactRequest) (WorkArtifactLink, error) {
	rev, bindingID, planeItemUUID, err := s.ValidateArtifactLinkTx(ctx, tx, collection, req)
	if err != nil {
		return WorkArtifactLink{}, err
	}

	role := strings.ToLower(strings.TrimSpace(req.Role))
	assertedAt := time.Now()
	if req.AssertedAt != nil {
		assertedAt = *req.AssertedAt
	}

	// Lock existing link row if present
	var existing WorkArtifactLink
	var prevRationale *string
	err = tx.QueryRow(ctx, `
		SELECT link_id, revision, collection, binding_id, plane_item_uuid, artifact_id, revision_id,
		       role, state, COALESCE(rationale, ''), author, asserted_at, created_at, removed_at, COALESCE(removed_by, '')
		FROM cairn.work_artifact_link
		WHERE binding_id = $1 AND plane_item_uuid = $2 AND revision_id = $3 AND role = $4
		FOR UPDATE
	`, bindingID, planeItemUUID, req.RevisionID, role).Scan(
		&existing.LinkID, &existing.Revision, &existing.Collection, &existing.BindingID, &existing.PlaneItemUUID,
		&existing.ArtifactID, &existing.RevisionID, &existing.Role, &existing.State,
		&existing.Rationale, &existing.Author, &existing.AssertedAt, &existing.CreatedAt,
		&existing.RemovedAt, &existing.RemovedBy)
	if err == nil {
		if prevRationale != nil {
			existing.Rationale = *prevRationale
		}
		existing.ArtifactRevision = rev
		// Exact duplicate link retries safely (AC-12)
		if existing.State == "active" {
			return existing, nil
		}

		// Reactivate removed link
		newRevision := existing.Revision + 1
		var reactivated WorkArtifactLink
		err = tx.QueryRow(ctx, `
			UPDATE cairn.work_artifact_link
			SET state = 'active', revision = $1, author = $2, rationale = $3, asserted_at = $4, removed_at = NULL, removed_by = NULL
			WHERE link_id = $5
			RETURNING link_id, revision, collection, binding_id, plane_item_uuid, artifact_id, revision_id,
			          role, state, COALESCE(rationale, ''), author, asserted_at, created_at, removed_at, COALESCE(removed_by, '')
		`, newRevision, s.channel.Principal, req.Rationale, assertedAt, existing.LinkID).Scan(
			&reactivated.LinkID, &reactivated.Revision, &reactivated.Collection, &reactivated.BindingID, &reactivated.PlaneItemUUID,
			&reactivated.ArtifactID, &reactivated.RevisionID, &reactivated.Role, &reactivated.State,
			&reactivated.Rationale, &reactivated.Author, &reactivated.AssertedAt, &reactivated.CreatedAt,
			&reactivated.RemovedAt, &reactivated.RemovedBy)
		if err != nil {
			return WorkArtifactLink{}, err
		}
		if prevRationale != nil {
			reactivated.Rationale = *prevRationale
		}

		// Record history
		_, err = tx.Exec(ctx, `
			INSERT INTO cairn.work_artifact_link_history (link_id, revision, state, role, rationale, author, created_at)
			VALUES ($1, $2, 'active', $3, $4, $5, clock_timestamp())
			ON CONFLICT (link_id, revision) DO NOTHING
		`, reactivated.LinkID, reactivated.Revision, reactivated.Role, reactivated.Rationale, s.channel.Principal)
		if err != nil {
			return WorkArtifactLink{}, err
		}
		reactivated.ArtifactRevision = rev
		return reactivated, nil
	}
	if !errors.Is(err, pgx.ErrNoRows) {
		return WorkArtifactLink{}, err
	}

	// Insert new link (handle concurrent first link with ON CONFLICT)
	var newLink WorkArtifactLink
	err = tx.QueryRow(ctx, `
		INSERT INTO cairn.work_artifact_link (
			revision, collection, binding_id, plane_item_uuid, artifact_id, revision_id,
			role, state, rationale, author, asserted_at, created_at
		) VALUES (1, $1, $2, $3, $4, $5, $6, 'active', $7, $8, $9, clock_timestamp())
		ON CONFLICT (binding_id, plane_item_uuid, revision_id, role) DO UPDATE
		SET asserted_at = cairn.work_artifact_link.asserted_at
		RETURNING link_id, revision, collection, binding_id, plane_item_uuid, artifact_id, revision_id,
		          role, state, COALESCE(rationale, ''), author, asserted_at, created_at, removed_at, COALESCE(removed_by, '')
	`, collection, bindingID, planeItemUUID, rev.ArtifactID, req.RevisionID,
		role, req.Rationale, s.channel.Principal, assertedAt).Scan(
		&newLink.LinkID, &newLink.Revision, &newLink.Collection, &newLink.BindingID, &newLink.PlaneItemUUID,
		&newLink.ArtifactID, &newLink.RevisionID, &newLink.Role, &newLink.State,
		&newLink.Rationale, &newLink.Author, &newLink.AssertedAt, &newLink.CreatedAt,
		&newLink.RemovedAt, &newLink.RemovedBy)
	if err != nil {
		return WorkArtifactLink{}, err
	}
	if prevRationale != nil {
		newLink.Rationale = *prevRationale
	}

	// Record history
	_, err = tx.Exec(ctx, `
		INSERT INTO cairn.work_artifact_link_history (link_id, revision, state, role, rationale, author, created_at)
		VALUES ($1, 1, 'active', $2, $3, $4, clock_timestamp())
		ON CONFLICT (link_id, revision) DO NOTHING
	`, newLink.LinkID, newLink.Role, newLink.Rationale, s.channel.Principal)
	if err != nil {
		return WorkArtifactLink{}, err
	}

	newLink.ArtifactRevision = rev
	return newLink, nil
}

// LinkWorkArtifact links a work item to an artifact revision.
func (s *Store) LinkWorkArtifact(ctx context.Context, req LinkWorkArtifactRequest) (WorkArtifactLink, error) {
	if err := s.requireOperator(); err != nil {
		return WorkArtifactLink{}, err
	}
	if req.Collection == "" {
		req.Collection = s.channel.Repo
		if req.Collection == "" {
			req.Collection = "default"
		}
	}
	if err := s.checkRepo(req.Collection); err != nil {
		return WorkArtifactLink{}, err
	}

	if req.RequestID == "" {
		req.RequestID = uuid.NewString()
	}

	guard := func(tx pgx.Tx) error {
		var rawResp []byte
		var prevDigest []byte
		err := tx.QueryRow(ctx, `
			SELECT request_digest, response
			FROM cairn.mutation_request
			WHERE caller = $1 AND operation = $2 AND request_id = $3
		`, s.channel.Principal, "work-artifact-link", req.RequestID).Scan(&prevDigest, &rawResp)
		if errors.Is(err, pgx.ErrNoRows) {
			return nil
		}
		if err != nil {
			return err
		}
		canonical, err := json.Marshal(req)
		if err != nil {
			return err
		}
		expectedDigest := sha256.Sum256(canonical)
		if !bytes.Equal(prevDigest, expectedDigest[:]) {
			return nil
		}
		if len(rawResp) == 0 {
			return nil
		}

		var cachedLink WorkArtifactLink
		if err := json.Unmarshal(rawResp, &cachedLink); err != nil {
			return nil
		}

		var fenced bool
		err = tx.QueryRow(ctx, `
			SELECT true FROM cairn.work_artifact_purge_fence
			WHERE collection = $1 AND artifact_id = $2
		`, cachedLink.Collection, cachedLink.ArtifactID).Scan(&fenced)
		if err == nil && fenced {
			return failure("PAYLOAD_UNAVAILABLE", fmt.Sprintf("artifact %s has been purged", cachedLink.ArtifactID))
		}
		if err != nil && !errors.Is(err, pgx.ErrNoRows) {
			return err
		}

		var kind string
		var cairnRecID *uuid.UUID
		var cairnVer *int
		err = tx.QueryRow(ctx, `
			SELECT a.kind, a.cairn_record_id, r.cairn_version
			FROM cairn.work_artifact a
			JOIN cairn.work_artifact_revision r ON r.artifact_id = a.artifact_id
			WHERE r.revision_id = $1
		`, cachedLink.RevisionID).Scan(&kind, &cairnRecID, &cairnVer)
		if errors.Is(err, pgx.ErrNoRows) {
			return nil
		}
		if err != nil {
			return err
		}

		if kind == "cairn_result" && cairnRecID != nil && cairnVer != nil {
			eligible, reason, err := checkCairnResultEligibilityTx(ctx, tx, cachedLink.Collection, *cairnRecID, *cairnVer, false)
			if err != nil {
				return err
			}
			if !eligible {
				if strings.Contains(reason, "repository outside") {
					return failure("AUTHORITY_DENIED", fmt.Sprintf("cairn record %s version %d is outside collection %q", cairnRecID, *cairnVer, cachedLink.Collection))
				}
				return failure("PAYLOAD_UNAVAILABLE", fmt.Sprintf("cairn record %s version %d: %s", cairnRecID, *cairnVer, reason))
			}
		}
		return nil
	}

	return mutate(ctx, s, "work-artifact-link", req.RequestID, req, func(tx pgx.Tx) (WorkArtifactLink, error) {
		if err := s.checkWorkArtifactFeatureTx(ctx, tx); err != nil {
			return WorkArtifactLink{}, err
		}
		return s.LinkWorkArtifactTx(ctx, tx, req.Collection, req)
	}, guard)
}

// UnlinkWorkArtifact removes an artifact link from a work item with optimistic concurrency control.
func (s *Store) UnlinkWorkArtifact(ctx context.Context, req UnlinkWorkArtifactRequest) (WorkArtifactLink, error) {
	if err := s.requireOperator(); err != nil {
		return WorkArtifactLink{}, err
	}
	if req.Collection == "" {
		req.Collection = s.channel.Repo
		if req.Collection == "" {
			req.Collection = "default"
		}
	}
	if err := s.checkRepo(req.Collection); err != nil {
		return WorkArtifactLink{}, err
	}
	if strings.TrimSpace(req.ProjectSlug) == "" {
		return WorkArtifactLink{}, failure("INVALID_REQUEST", "project slug required")
	}
	if req.ItemUUID == uuid.Nil {
		return WorkArtifactLink{}, failure("INVALID_REQUEST", "item UUID required")
	}
	if req.LinkID == uuid.Nil {
		return WorkArtifactLink{}, failure("INVALID_REQUEST", "link ID required")
	}
	if req.ExpectedRevision <= 0 {
		return WorkArtifactLink{}, failure("INVALID_REQUEST", "positive expected_revision required for guarded unlink")
	}

	if req.RequestID == "" {
		req.RequestID = uuid.NewString()
	}

	guard := func(tx pgx.Tx) error {
		var rawResp []byte
		var prevDigest []byte
		err := tx.QueryRow(ctx, `
			SELECT request_digest, response
			FROM cairn.mutation_request
			WHERE caller = $1 AND operation = $2 AND request_id = $3
		`, s.channel.Principal, "work-artifact-unlink", req.RequestID).Scan(&prevDigest, &rawResp)
		if errors.Is(err, pgx.ErrNoRows) {
			return nil
		}
		if err != nil {
			return err
		}
		canonical, err := json.Marshal(req)
		if err != nil {
			return err
		}
		expectedDigest := sha256.Sum256(canonical)
		if !bytes.Equal(prevDigest, expectedDigest[:]) {
			return nil
		}
		if len(rawResp) == 0 {
			return nil
		}

		var cachedLink WorkArtifactLink
		if err := json.Unmarshal(rawResp, &cachedLink); err != nil {
			return nil
		}

		var fenced bool
		err = tx.QueryRow(ctx, `
			SELECT true FROM cairn.work_artifact_purge_fence
			WHERE collection = $1 AND artifact_id = $2
		`, cachedLink.Collection, cachedLink.ArtifactID).Scan(&fenced)
		if err == nil && fenced {
			return failure("PAYLOAD_UNAVAILABLE", fmt.Sprintf("artifact %s has been purged", cachedLink.ArtifactID))
		}
		if err != nil && !errors.Is(err, pgx.ErrNoRows) {
			return err
		}

		var kind string
		var cairnRecID *uuid.UUID
		var cairnVer *int
		err = tx.QueryRow(ctx, `
			SELECT a.kind, a.cairn_record_id, r.cairn_version
			FROM cairn.work_artifact a
			JOIN cairn.work_artifact_revision r ON r.artifact_id = a.artifact_id
			WHERE r.revision_id = $1
		`, cachedLink.RevisionID).Scan(&kind, &cairnRecID, &cairnVer)
		if errors.Is(err, pgx.ErrNoRows) {
			return nil
		}
		if err != nil {
			return err
		}

		if kind == "cairn_result" && cairnRecID != nil && cairnVer != nil {
			eligible, reason, err := checkCairnResultEligibilityTx(ctx, tx, cachedLink.Collection, *cairnRecID, *cairnVer, false)
			if err != nil {
				return err
			}
			if strings.Contains(reason, "repository outside") {
				return failure("AUTHORITY_DENIED", fmt.Sprintf("cairn record %s version %d is outside collection %q", cairnRecID, *cairnVer, cachedLink.Collection))
			}
			if !eligible && cachedLink.Rationale != "" {
				return failure("PAYLOAD_UNAVAILABLE", fmt.Sprintf("cairn record %s version %d: %s", cairnRecID, *cairnVer, reason))
			}
		}
		return nil
	}

	return mutate(ctx, s, "work-artifact-unlink", req.RequestID, req, func(tx pgx.Tx) (WorkArtifactLink, error) {
		if err := s.checkWorkReadableTx(ctx, tx); err != nil {
			return WorkArtifactLink{}, err
		}

		// Verify project exists
		var bindingID uuid.UUID
		err := tx.QueryRow(ctx, `SELECT binding_id FROM cairn.work_project WHERE collection = $1 AND local_slug = $2`, req.Collection, req.ProjectSlug).Scan(&bindingID)
		if errors.Is(err, pgx.ErrNoRows) {
			return WorkArtifactLink{}, failure("NOT_FOUND", fmt.Sprintf("work project %q not found in collection %q", req.ProjectSlug, req.Collection))
		}
		if err != nil {
			return WorkArtifactLink{}, err
		}

		var existing WorkArtifactLink
		err = tx.QueryRow(ctx, `
			SELECT link_id, revision, collection, binding_id, plane_item_uuid, artifact_id, revision_id,
			       role, state, COALESCE(rationale, ''), author, asserted_at, created_at, removed_at, COALESCE(removed_by, '')
			FROM cairn.work_artifact_link
			WHERE link_id = $1 AND collection = $2
			FOR UPDATE
		`, req.LinkID, req.Collection).Scan(
			&existing.LinkID, &existing.Revision, &existing.Collection, &existing.BindingID, &existing.PlaneItemUUID,
			&existing.ArtifactID, &existing.RevisionID, &existing.Role, &existing.State,
			&existing.Rationale, &existing.Author, &existing.AssertedAt, &existing.CreatedAt,
			&existing.RemovedAt, &existing.RemovedBy)
		if errors.Is(err, pgx.ErrNoRows) {
			return WorkArtifactLink{}, failure("NOT_FOUND", fmt.Sprintf("artifact link %s not found in collection %q", req.LinkID, req.Collection))
		}
		if err != nil {
			return WorkArtifactLink{}, err
		}

		if existing.BindingID != bindingID {
			return WorkArtifactLink{}, failure("NOT_FOUND", fmt.Sprintf("link %s does not belong to project %s", req.LinkID, req.ProjectSlug))
		}
		if existing.PlaneItemUUID != req.ItemUUID {
			return WorkArtifactLink{}, failure("NOT_FOUND", fmt.Sprintf("link %s does not belong to item %s", req.LinkID, req.ItemUUID))
		}

		if existing.Revision != req.ExpectedRevision {
			return WorkArtifactLink{}, failure("VERSION_CONFLICT", fmt.Sprintf("expected link revision %d but current is %d", req.ExpectedRevision, existing.Revision))
		}

		// Distinct request_id on an already-removed link must return VERSION_CONFLICT
		if existing.State == "removed" {
			return WorkArtifactLink{}, failure("VERSION_CONFLICT", fmt.Sprintf("artifact link %s is already removed", req.LinkID))
		}

		newRevision := existing.Revision + 1
		var removed WorkArtifactLink
		err = tx.QueryRow(ctx, `
			UPDATE cairn.work_artifact_link
			SET state = 'removed', revision = $1, removed_at = clock_timestamp(), removed_by = $2
			WHERE link_id = $3
			RETURNING link_id, revision, collection, binding_id, plane_item_uuid, artifact_id, revision_id,
			          role, state, COALESCE(rationale, ''), author, asserted_at, created_at, removed_at, COALESCE(removed_by, '')
		`, newRevision, s.channel.Principal, existing.LinkID).Scan(
			&removed.LinkID, &removed.Revision, &removed.Collection, &removed.BindingID, &removed.PlaneItemUUID,
			&removed.ArtifactID, &removed.RevisionID, &removed.Role, &removed.State,
			&removed.Rationale, &removed.Author, &removed.AssertedAt, &removed.CreatedAt,
			&removed.RemovedAt, &removed.RemovedBy)
		if err != nil {
			return WorkArtifactLink{}, err
		}

		// Check if source is purged or unavailable to mask rationale (Repair 2 / ART-PRIVACY)
		var fenced bool
		err = tx.QueryRow(ctx, `
			SELECT true FROM cairn.work_artifact_purge_fence
			WHERE collection = $1 AND artifact_id = $2
		`, removed.Collection, removed.ArtifactID).Scan(&fenced)
		if err != nil && !errors.Is(err, pgx.ErrNoRows) {
			return WorkArtifactLink{}, err
		}

		shouldMaskRationale := fenced
		var kind string
		var cairnRecID *uuid.UUID
		var cairnVer *int
		err = tx.QueryRow(ctx, `
			SELECT a.kind, a.cairn_record_id, r.cairn_version
			FROM cairn.work_artifact a
			JOIN cairn.work_artifact_revision r ON r.artifact_id = a.artifact_id
			WHERE r.revision_id = $1
		`, removed.RevisionID).Scan(&kind, &cairnRecID, &cairnVer)
		if err != nil && !errors.Is(err, pgx.ErrNoRows) {
			return WorkArtifactLink{}, err
		}

		if kind == "cairn_result" && cairnRecID != nil && cairnVer != nil {
			eligible, _, err := checkCairnResultEligibilityTx(ctx, tx, removed.Collection, *cairnRecID, *cairnVer, false)
			if err != nil {
				return WorkArtifactLink{}, err
			}
			if !eligible {
				shouldMaskRationale = true
			}
		}

		if shouldMaskRationale {
			removed.Rationale = ""
		}

		// Record history
		_, err = tx.Exec(ctx, `
			INSERT INTO cairn.work_artifact_link_history (link_id, revision, state, role, rationale, author, created_at)
			VALUES ($1, $2, 'removed', $3, $4, $5, clock_timestamp())
		`, removed.LinkID, removed.Revision, removed.Role, removed.Rationale, s.channel.Principal)
		if err != nil {
			return WorkArtifactLink{}, err
		}

		return removed, nil
	}, guard)
}

// listWorkArtifactLinksTx lists artifact links for an item within an existing transaction.
func (s *Store) listWorkArtifactLinksTx(ctx context.Context, tx pgx.Tx, collection string, bindingID uuid.UUID, planeItemUUID uuid.UUID, roleFilter string, includeRemoved bool) ([]WorkArtifactLink, error) {
	query := `
		SELECT l.link_id, l.revision, l.collection, l.binding_id, l.plane_item_uuid, l.artifact_id, l.revision_id,
		       l.role, l.state, COALESCE(l.rationale, ''), l.author, l.asserted_at, l.created_at, l.removed_at, COALESCE(l.removed_by, ''),
		       r.revision_id, r.artifact_id, r.collection, r.revision_label,
		       r.git_commit_oid, r.git_object_format, r.git_branch_hint,
		       r.report_digest, r.report_provenance, r.report_mutable,
		       r.ci_attempt, r.ci_status, r.ci_reported_at,
		       r.cairn_version, r.metadata, r.created_by, r.created_at,
		       a.artifact_id, a.collection, a.kind, a.canonical_key, a.display_name,
		       COALESCE(a.git_repo, ''), COALESCE(a.report_locator, ''),
		       COALESCE(a.ci_provider, ''), COALESCE(a.ci_repo, ''), COALESCE(a.ci_run_id, ''),
		       a.cairn_record_id, a.created_at, a.updated_at,
		       i.identifier, i.title, p.auth_rejected
		FROM cairn.work_artifact_link l
		JOIN cairn.work_artifact_revision r ON r.revision_id = l.revision_id
		JOIN cairn.work_artifact a ON a.artifact_id = l.artifact_id
		JOIN cairn.work_item i ON i.binding_id = l.binding_id AND i.plane_item_uuid = l.plane_item_uuid
		JOIN cairn.work_project p ON p.binding_id = l.binding_id
		WHERE l.binding_id = $1 AND l.plane_item_uuid = $2
	`
	args := []any{bindingID, planeItemUUID}
	argIdx := 3

	if !includeRemoved {
		query += " AND l.state = 'active'"
	}
	if roleFilter != "" {
		query += fmt.Sprintf(" AND l.role = $%d", argIdx)
		args = append(args, roleFilter)
		argIdx++
	}
	query += " ORDER BY l.asserted_at ASC, l.link_id ASC"

	rows, err := tx.Query(ctx, query, args...)
	if err != nil {
		return nil, err
	}

	var links []WorkArtifactLink
	for rows.Next() {
		var l WorkArtifactLink
		var rev WorkArtifactRevision
		var a WorkArtifact
		var metaRaw []byte
		var gitCommitOID, objFmt, branchHint, repDigest, repProv *string
		var repMut *bool
		var ciAtt *int
		var ciStat *string
		var ciRepAt *time.Time
		var cairnVer *int
		var authRejected bool

		if err := rows.Scan(
			&l.LinkID, &l.Revision, &l.Collection, &l.BindingID, &l.PlaneItemUUID, &l.ArtifactID, &l.RevisionID,
			&l.Role, &l.State, &l.Rationale, &l.Author, &l.AssertedAt, &l.CreatedAt, &l.RemovedAt, &l.RemovedBy,
			&rev.RevisionID, &rev.ArtifactID, &rev.Collection, &rev.RevisionLabel,
			&gitCommitOID, &objFmt, &branchHint, &repDigest, &repProv, &repMut,
			&ciAtt, &ciStat, &ciRepAt, &cairnVer, &metaRaw, &rev.CreatedBy, &rev.CreatedAt,
			&a.ArtifactID, &a.Collection, &a.Kind, &a.CanonicalKey, &a.DisplayName,
			&a.GitRepo, &a.ReportLocator, &a.CIProvider, &a.CIRepo, &a.CIRunID, &a.CairnRecordID, &a.CreatedAt, &a.UpdatedAt,
			&l.ItemIdentifier, &l.ItemTitle, &authRejected,
		); err != nil {
			rows.Close()
			return nil, err
		}

		if authRejected {
			l.ItemTitle = "[hidden: auth rejected]"
			l.ItemIdentifier = "[hidden]"
		}

		if gitCommitOID != nil {
			rev.GitCommitOID = *gitCommitOID
		}
		if objFmt != nil {
			rev.GitObjectFormat = *objFmt
		}
		if branchHint != nil {
			rev.GitBranchHint = *branchHint
		}
		if repDigest != nil {
			rev.ReportDigest = *repDigest
		}
		if repProv != nil {
			rev.ReportProvenance = *repProv
		}
		if repMut != nil {
			rev.ReportMutable = *repMut
		}
		if ciAtt != nil {
			rev.CIAttempt = *ciAtt
		}
		if ciStat != nil {
			rev.CIStatus = *ciStat
		}
		rev.CIReportedAt = ciRepAt
		rev.CairnVersion = cairnVer
		_ = json.Unmarshal(metaRaw, &rev.Metadata)
		rev.Artifact = &a

		l.ArtifactRevision = &rev
		links = append(links, l)
	}
	if err := rows.Err(); err != nil {
		rows.Close()
		return nil, err
	}
	rows.Close()

	// Perform availability / privacy checks after closing outer rows to prevent conn busy errors (ART-READ, ART-PRIVACY)
	for i := range links {
		rev := links[i].ArtifactRevision
		if rev == nil || rev.Artifact == nil {
			continue
		}
		a := rev.Artifact
		if a.Kind == "cairn_result" && a.CairnRecordID != nil && rev.CairnVersion != nil {
			eligible, reason, err := checkCairnResultEligibilityTx(ctx, tx, collection, *a.CairnRecordID, *rev.CairnVersion, false)
			if err != nil {
				return nil, err
			}
			if !eligible {
				rev.Unavailable = true
				rev.UnavailableReason = reason
				rev.Metadata = nil
				a.DisplayName = ""
				links[i].Rationale = ""
			}
		}
	}

	return links, nil
}

// ListWorkArtifactLinks lists all artifact links associated with a work item.
func (s *Store) ListWorkArtifactLinks(ctx context.Context, req ListWorkArtifactLinksRequest) ([]WorkArtifactLink, error) {
	if err := s.requireOperator(); err != nil {
		return nil, err
	}
	if req.Collection == "" {
		req.Collection = s.channel.Repo
		if req.Collection == "" {
			req.Collection = "default"
		}
	}
	if err := s.checkRepo(req.Collection); err != nil {
		return nil, err
	}
	if strings.TrimSpace(req.ProjectSlug) == "" {
		return nil, failure("INVALID_REQUEST", "project slug required")
	}
	if req.ItemUUID == uuid.Nil {
		return nil, failure("INVALID_REQUEST", "item UUID required")
	}

	tx, err := s.beginLevel(ctx, pgx.RepeatableRead)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback(ctx)
	if err := s.checkWorkReadableTx(ctx, tx); err != nil {
		return nil, err
	}

	var bindingID uuid.UUID
	err = tx.QueryRow(ctx, `SELECT binding_id FROM cairn.work_project WHERE collection = $1 AND local_slug = $2`, req.Collection, req.ProjectSlug).Scan(&bindingID)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, failure("NOT_FOUND", fmt.Sprintf("work project %q not found in collection %q", req.ProjectSlug, req.Collection))
	}
	if err != nil {
		return nil, err
	}

	var planeItemUUID uuid.UUID
	err = tx.QueryRow(ctx, `SELECT plane_item_uuid FROM cairn.work_item WHERE binding_id = $1 AND (item_id = $2 OR plane_item_uuid = $2)`, bindingID, req.ItemUUID).Scan(&planeItemUUID)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, failure("NOT_FOUND", fmt.Sprintf("work item %s not found in project %s", req.ItemUUID, req.ProjectSlug))
	}
	if err != nil {
		return nil, err
	}

	return s.listWorkArtifactLinksTx(ctx, tx, req.Collection, bindingID, planeItemUUID, req.Role, req.IncludeRemoved)
}

// ReverseImpact performs keyset-paginated reverse lookup of work items impacted by an artifact revision.
func (s *Store) ReverseImpact(ctx context.Context, req ReverseImpactRequest) (ReverseImpactPage, error) {
	out := ReverseImpactPage{Impacts: []WorkImpactItem{}}
	if err := s.requireOperator(); err != nil {
		return out, err
	}
	if req.Collection == "" {
		req.Collection = s.channel.Repo
		if req.Collection == "" {
			req.Collection = "default"
		}
	}
	if err := s.checkRepo(req.Collection); err != nil {
		return out, err
	}
	if req.RevisionID == uuid.Nil {
		return out, failure("INVALID_REQUEST", "artifact revision ID required")
	}

	limit := req.Limit
	if limit <= 0 {
		limit = 50
	} else if limit > 200 {
		limit = 200
	}

	tx, err := s.beginLevel(ctx, pgx.RepeatableRead)
	if err != nil {
		return out, err
	}
	defer tx.Rollback(ctx)
	if err := s.checkWorkReadableTx(ctx, tx); err != nil {
		return out, err
	}

	generation, err := retrievalGeneration(ctx, tx)
	if err != nil {
		return out, err
	}

	cursor := artifactImpactCursor{
		Schema:     "cairn.artifact-impact-cursor/1",
		Collection: req.Collection,
		RevisionID: req.RevisionID,
		Generation: generation,
	}

	if req.Cursor != "" {
		if len(req.Cursor) > 4096 {
			return out, failure("INVALID_REQUEST", "impact cursor exceeds limit")
		}
		raw, err := base64.RawURLEncoding.DecodeString(req.Cursor)
		var saved artifactImpactCursor
		if err != nil || json.Unmarshal(raw, &saved) != nil {
			return out, failure("INVALID_REQUEST", "invalid impact cursor")
		}
		if saved.Schema != cursor.Schema || saved.Collection != cursor.Collection || saved.RevisionID != cursor.RevisionID || saved.Generation != cursor.Generation {
			return out, failure("STALE_CURSOR", "cursor collection, revision or database generation differs; choose an explicit rescan")
		}
		cursor.AssertedAt = saved.AssertedAt
		cursor.LinkID = saved.LinkID
	}

	// 1. Fetch exact revision
	var rev WorkArtifactRevision
	var a WorkArtifact
	var metaRaw []byte
	var gitCommitOID, objFmt, branchHint, repDigest, repProv *string
	var repMut *bool
	var ciAtt *int
	var ciStat *string
	var ciRepAt *time.Time
	var cairnVer *int

	err = tx.QueryRow(ctx, `
		SELECT r.revision_id, r.artifact_id, r.collection, r.revision_label,
		       r.git_commit_oid, r.git_object_format, r.git_branch_hint,
		       r.report_digest, r.report_provenance, r.report_mutable,
		       r.ci_attempt, r.ci_status, r.ci_reported_at,
		       r.cairn_version, r.metadata, r.created_by, r.created_at,
		       a.artifact_id, a.collection, a.kind, a.canonical_key, a.display_name,
		       COALESCE(a.git_repo, ''), COALESCE(a.report_locator, ''),
		       COALESCE(a.ci_provider, ''), COALESCE(a.ci_repo, ''), COALESCE(a.ci_run_id, ''),
		       a.cairn_record_id, a.created_at, a.updated_at
		FROM cairn.work_artifact_revision r
		JOIN cairn.work_artifact a USING(artifact_id)
		WHERE r.collection = $1 AND r.revision_id = $2
	`, req.Collection, req.RevisionID).Scan(
		&rev.RevisionID, &rev.ArtifactID, &rev.Collection, &rev.RevisionLabel,
		&gitCommitOID, &objFmt, &branchHint, &repDigest, &repProv, &repMut,
		&ciAtt, &ciStat, &ciRepAt, &cairnVer, &metaRaw, &rev.CreatedBy, &rev.CreatedAt,
		&a.ArtifactID, &a.Collection, &a.Kind, &a.CanonicalKey, &a.DisplayName,
		&a.GitRepo, &a.ReportLocator, &a.CIProvider, &a.CIRepo, &a.CIRunID,
		&a.CairnRecordID, &a.CreatedAt, &a.UpdatedAt)
	if errors.Is(err, pgx.ErrNoRows) {
		return out, failure("NOT_FOUND", fmt.Sprintf("artifact revision %s not found in collection %q", req.RevisionID, req.Collection))
	}
	if err != nil {
		return out, err
	}

	if gitCommitOID != nil {
		rev.GitCommitOID = *gitCommitOID
	}
	if objFmt != nil {
		rev.GitObjectFormat = *objFmt
	}
	if branchHint != nil {
		rev.GitBranchHint = *branchHint
	}
	if repDigest != nil {
		rev.ReportDigest = *repDigest
	}
	if repProv != nil {
		rev.ReportProvenance = *repProv
	}
	if repMut != nil {
		rev.ReportMutable = *repMut
	}
	if ciAtt != nil {
		rev.CIAttempt = *ciAtt
	}
	if ciStat != nil {
		rev.CIStatus = *ciStat
	}
	rev.CIReportedAt = ciRepAt
	rev.CairnVersion = cairnVer
	_ = json.Unmarshal(metaRaw, &rev.Metadata)
	rev.Artifact = &a

	// Check eligibility if cairn_result (propagate real DB errors, ART-READ, ART-PRIVACY)
	if a.Kind == "cairn_result" && a.CairnRecordID != nil && rev.CairnVersion != nil {
		eligible, reason, err := checkCairnResultEligibilityTx(ctx, tx, req.Collection, *a.CairnRecordID, *rev.CairnVersion, false)
		if err != nil {
			return out, err
		}
		if !eligible {
			rev.Unavailable = true
			rev.UnavailableReason = reason
			rev.Metadata = nil
			if rev.Artifact != nil {
				rev.Artifact.DisplayName = ""
			}
		}
	}
	out.Revision = rev

	// 2. Keyset pagination query
	rows, err := tx.Query(ctx, `
		SELECT l.link_id, l.role, l.state, l.asserted_at, l.author,
		       p.binding_id, p.local_slug,
		       i.plane_item_uuid, i.identifier, i.title, COALESCE(i.state_name, ''), COALESCE(i.state_group, ''),
		       i.updated_at_source, COALESCE(i.source_url, ''), p.auth_rejected
		FROM cairn.work_artifact_link l
		JOIN cairn.work_project p USING(binding_id)
		JOIN cairn.work_item i ON i.binding_id = l.binding_id AND i.plane_item_uuid = l.plane_item_uuid
		WHERE l.collection = $1 AND l.revision_id = $2 AND l.state = 'active'
		  AND (l.asserted_at, l.link_id) > ($3, $4)
		ORDER BY l.asserted_at ASC, l.link_id ASC
		LIMIT $5
	`, req.Collection, req.RevisionID, cursor.AssertedAt, cursor.LinkID, limit+1)
	if err != nil {
		return out, err
	}

	var items []WorkImpactItem
	for rows.Next() {
		var it WorkImpactItem
		var authRejected bool
		if err := rows.Scan(
			&it.LinkID, &it.Role, &it.LinkState, &it.LinkAssertedAt, &it.LinkAuthor,
			&it.BindingID, &it.ProjectSlug,
			&it.PlaneItemUUID, &it.ItemIdentifier, &it.ItemTitle, &it.StateName, &it.StateGroup,
			&it.UpdatedAtSource, &it.SourceURL, &authRejected,
		); err != nil {
			rows.Close()
			return out, err
		}
		if authRejected {
			it.ItemTitle = "[hidden: auth rejected]"
			it.ItemIdentifier = "[hidden]"
			it.StateName = ""
			it.StateGroup = ""
			it.UpdatedAtSource = nil
			it.SourceURL = ""
		}
		it.LinkedSessions = []string{}
		it.OpenBlockers = []string{}
		items = append(items, it)
	}
	if err := rows.Err(); err != nil {
		rows.Close()
		return out, err
	}
	rows.Close()

	// 3. Batched join of sessions and blockers for page items (no unbounded nested tx queries, ART-READ)
	if len(items) > 0 {
		pageCount := len(items)
		if pageCount > limit {
			pageCount = limit
		}
		itemIndices := make(map[string][]int)
		for idx := 0; idx < pageCount; idx++ {
			key := fmt.Sprintf("%s:%s", items[idx].BindingID, items[idx].PlaneItemUUID)
			itemIndices[key] = append(itemIndices[key], idx)
		}

		uniqueIDs := getUniqueBindingIDs(items[:pageCount])
		if len(uniqueIDs) > 0 {
			// Batched session associations
			assocRows, err := tx.Query(ctx, `
				SELECT binding_id, plane_item_uuid, target_type, target_id
				FROM cairn.work_association
				WHERE binding_id = ANY($1) AND state = 'active' AND target_type IN ('session', 'execution')
			`, uniqueIDs)
			if err != nil {
				return out, err
			}
			for assocRows.Next() {
				var bID, pUUID uuid.UUID
				var tType, tID string
				if err := assocRows.Scan(&bID, &pUUID, &tType, &tID); err != nil {
					assocRows.Close()
					return out, err
				}
				key := fmt.Sprintf("%s:%s", bID, pUUID)
				for _, idx := range itemIndices[key] {
					items[idx].LinkedSessions = append(items[idx].LinkedSessions, tID)
				}
			}
			if err := assocRows.Err(); err != nil {
				assocRows.Close()
				return out, err
			}
			assocRows.Close()

			// Batched blocker annotations
			blockRows, err := tx.Query(ctx, `
				SELECT binding_id, plane_item_uuid, body
				FROM cairn.work_annotation
				WHERE binding_id = ANY($1) AND kind = 'blocker' AND state = 'open'
			`, uniqueIDs)
			if err != nil {
				return out, err
			}
			for blockRows.Next() {
				var bID, pUUID uuid.UUID
				var body string
				if err := blockRows.Scan(&bID, &pUUID, &body); err != nil {
					blockRows.Close()
					return out, err
				}
				key := fmt.Sprintf("%s:%s", bID, pUUID)
				for _, idx := range itemIndices[key] {
					items[idx].OpenBlockers = append(items[idx].OpenBlockers, body)
				}
			}
			if err := blockRows.Err(); err != nil {
				blockRows.Close()
				return out, err
			}
			blockRows.Close()
		}
	}

	if len(items) > limit {
		out.More = true
		last := items[limit-1]
		nextCur := artifactImpactCursor{
			Schema:     cursor.Schema,
			Collection: cursor.Collection,
			RevisionID: cursor.RevisionID,
			Generation: cursor.Generation,
			AssertedAt: last.LinkAssertedAt,
			LinkID:     last.LinkID,
		}
		raw, _ := json.Marshal(nextCur)
		out.NextCursor = base64.RawURLEncoding.EncodeToString(raw)
		out.Impacts = items[:limit]
	} else {
		out.Impacts = items
	}

	if err := tx.Commit(ctx); err != nil {
		return out, err
	}
	return out, nil
}

// GetWorkArtifactLinkHistoryRequest specifies parameters for inspecting a link's historical transitions.
type GetWorkArtifactLinkHistoryRequest struct {
	Collection  string    `json:"collection,omitempty"`
	ProjectSlug string    `json:"project"`
	ItemUUID    uuid.UUID `json:"item_uuid"`
	LinkID      uuid.UUID `json:"link_id"`
	Limit       int       `json:"limit,omitempty"`
	Cursor      string    `json:"cursor,omitempty"`
}

// WorkArtifactLinkHistoryPage contains the current attributed link and paginated historical rows.
type WorkArtifactLinkHistoryPage struct {
	Link       WorkArtifactLink          `json:"link"`
	History    []WorkArtifactLinkHistory `json:"history"`
	More       bool                      `json:"more"`
	NextCursor string                    `json:"next_cursor,omitempty"`
}

type artifactHistoryCursor struct {
	Schema     string    `json:"schema"`
	Collection string    `json:"collection"`
	LinkID     uuid.UUID `json:"link_id"`
	Generation int64     `json:"generation"`
	Revision   int64     `json:"revision"`
	HistoryID  uuid.UUID `json:"history_id"`
}

// GetWorkArtifactLinkHistory retrieves the current link and paginated history for a specific link.
func (s *Store) GetWorkArtifactLinkHistory(ctx context.Context, req GetWorkArtifactLinkHistoryRequest) (WorkArtifactLinkHistoryPage, error) {
	out := WorkArtifactLinkHistoryPage{History: []WorkArtifactLinkHistory{}}
	if err := s.requireOperator(); err != nil {
		return out, err
	}
	if req.Collection == "" {
		req.Collection = s.channel.Repo
		if req.Collection == "" {
			req.Collection = "default"
		}
	}
	if err := s.checkRepo(req.Collection); err != nil {
		return out, err
	}
	if strings.TrimSpace(req.ProjectSlug) == "" {
		return out, failure("INVALID_REQUEST", "project slug required")
	}
	if req.ItemUUID == uuid.Nil {
		return out, failure("INVALID_REQUEST", "item UUID required")
	}
	if req.LinkID == uuid.Nil {
		return out, failure("INVALID_REQUEST", "link ID required")
	}

	limit := req.Limit
	if limit <= 0 {
		limit = 50
	} else if limit > 200 {
		limit = 200
	}

	tx, err := s.beginLevel(ctx, pgx.RepeatableRead)
	if err != nil {
		return out, err
	}
	defer tx.Rollback(ctx)
	if err := s.checkWorkReadableTx(ctx, tx); err != nil {
		return out, err
	}

	generation, err := retrievalGeneration(ctx, tx)
	if err != nil {
		return out, err
	}

	cursor := artifactHistoryCursor{
		Schema:     "cairn.artifact-history-cursor/1",
		Collection: req.Collection,
		LinkID:     req.LinkID,
		Generation: generation,
	}

	if req.Cursor != "" {
		if len(req.Cursor) > 4096 {
			return out, failure("INVALID_REQUEST", "history cursor exceeds limit")
		}
		raw, err := base64.RawURLEncoding.DecodeString(req.Cursor)
		var saved artifactHistoryCursor
		if err != nil || json.Unmarshal(raw, &saved) != nil {
			return out, failure("INVALID_REQUEST", "invalid history cursor")
		}
		if saved.Schema != cursor.Schema || saved.Collection != cursor.Collection || saved.LinkID != cursor.LinkID || saved.Generation != cursor.Generation {
			return out, failure("STALE_CURSOR", "cursor collection, link or database generation differs; choose an explicit rescan")
		}
		cursor.Revision = saved.Revision
		cursor.HistoryID = saved.HistoryID
	}

	// 1. Resolve binding and item to verify exact binding
	var bindingID uuid.UUID
	err = tx.QueryRow(ctx, `SELECT binding_id FROM cairn.work_project WHERE collection = $1 AND local_slug = $2`, req.Collection, req.ProjectSlug).Scan(&bindingID)
	if errors.Is(err, pgx.ErrNoRows) {
		return out, failure("NOT_FOUND", fmt.Sprintf("work project %q not found in collection %q", req.ProjectSlug, req.Collection))
	}
	if err != nil {
		return out, err
	}

	var planeItemUUID uuid.UUID
	err = tx.QueryRow(ctx, `SELECT plane_item_uuid FROM cairn.work_item WHERE binding_id = $1 AND (item_id = $2 OR plane_item_uuid = $2)`, bindingID, req.ItemUUID).Scan(&planeItemUUID)
	if errors.Is(err, pgx.ErrNoRows) {
		return out, failure("NOT_FOUND", fmt.Sprintf("work item %s not found in project %s", req.ItemUUID, req.ProjectSlug))
	}
	if err != nil {
		return out, err
	}

	// 2. Read the current link and revision
	links, err := s.listWorkArtifactLinksTx(ctx, tx, req.Collection, bindingID, planeItemUUID, "", true)
	if err != nil {
		return out, err
	}
	var currentLink *WorkArtifactLink
	for _, l := range links {
		if l.LinkID == req.LinkID {
			currentLink = &l
			break
		}
	}
	if currentLink == nil {
		return out, failure("NOT_FOUND", fmt.Sprintf("link %s not found on item %s", req.LinkID, req.ItemUUID))
	}
	out.Link = *currentLink

	// Check if source record is unavailable to mask historical rationale
	sourceUnavailable := currentLink.ArtifactRevision != nil && currentLink.ArtifactRevision.Unavailable

	// 3. Query history with keyset pagination (ordered by revision, history_id)
	historyQuery := `
		SELECT history_id, link_id, revision, state, role, COALESCE(rationale, ''), author, created_at
		FROM cairn.work_artifact_link_history
		WHERE link_id = $1
		  AND ($2::bigint = 0 OR (revision, history_id) > ($2, $3))
		ORDER BY revision ASC, history_id ASC
		LIMIT $4
	`
	rows, err := tx.Query(ctx, historyQuery, req.LinkID, cursor.Revision, cursor.HistoryID, limit+1)
	if err != nil {
		return out, err
	}
	defer rows.Close()

	var historyList []WorkArtifactLinkHistory
	for rows.Next() {
		var h WorkArtifactLinkHistory
		if err := rows.Scan(&h.HistoryID, &h.LinkID, &h.Revision, &h.State, &h.Role, &h.Rationale, &h.Author, &h.CreatedAt); err != nil {
			return out, err
		}
		if sourceUnavailable {
			h.Rationale = ""
		}
		historyList = append(historyList, h)
	}
	if err := rows.Err(); err != nil {
		return out, err
	}

	if len(historyList) > limit {
		out.More = true
		last := historyList[limit-1]
		next := artifactHistoryCursor{
			Schema:     cursor.Schema,
			Collection: cursor.Collection,
			LinkID:     cursor.LinkID,
			Generation: cursor.Generation,
			Revision:   last.Revision,
			HistoryID:  last.HistoryID,
		}
		blob, _ := json.Marshal(next)
		out.NextCursor = base64.RawURLEncoding.EncodeToString(blob)
		out.History = historyList[:limit]
	} else {
		out.More = false
		if historyList == nil {
			out.History = []WorkArtifactLinkHistory{}
		} else {
			out.History = historyList
		}
	}

	return out, tx.Commit(ctx)
}

func getUniqueBindingIDs(items []WorkImpactItem) []uuid.UUID {
	seen := make(map[uuid.UUID]bool)
	var ids []uuid.UUID
	for _, it := range items {
		if !seen[it.BindingID] {
			seen[it.BindingID] = true
			ids = append(ids, it.BindingID)
		}
	}
	return ids
}

// PurgeWorkArtifact removes an artifact and its links from local projection without modifying external deliverables.
// It records a durable exclusion fence to prevent replays, purges mutation receipts, and remains available
// even when work-artifacts/1 admission is disabled.
func (s *Store) PurgeWorkArtifact(ctx context.Context, req PurgeWorkArtifactRequest) error {
	if err := s.requireOperator(); err != nil {
		return err
	}
	if req.Collection == "" {
		req.Collection = s.channel.Repo
		if req.Collection == "" {
			req.Collection = "default"
		}
	}
	if err := s.checkRepo(req.Collection); err != nil {
		return err
	}
	if req.ArtifactID == uuid.Nil {
		return failure("INVALID_REQUEST", "artifact ID required")
	}
	if req.RequestID == "" {
		req.RequestID = uuid.NewString()
	}

	_, err := mutate(ctx, s, "work-artifact-purge", req.RequestID, req, func(tx pgx.Tx) (any, error) {
		// 1. Check if already purged in fence (idempotent)
		var fenceExists bool
		err := tx.QueryRow(ctx, `
			SELECT TRUE FROM cairn.work_artifact_purge_fence
			WHERE collection = $1 AND artifact_id = $2
		`, req.Collection, req.ArtifactID).Scan(&fenceExists)
		if err == nil && fenceExists {
			return nil, nil
		}
		if err != nil && !errors.Is(err, pgx.ErrNoRows) {
			return nil, err
		}

		var canonicalKey string
		err = tx.QueryRow(ctx, `
			SELECT canonical_key
			FROM cairn.work_artifact
			WHERE collection = $1 AND artifact_id = $2
		`, req.Collection, req.ArtifactID).Scan(&canonicalKey)
		if errors.Is(err, pgx.ErrNoRows) {
			canonicalKey = fmt.Sprintf("purged:%s", req.ArtifactID)
		} else if err != nil {
			return nil, err
		}

		// Take deterministic advisory lock before purge
		lockKey := fmt.Sprintf("work-artifact:%s:%s", req.Collection, canonicalKey)
		if _, err := tx.Exec(ctx, `SELECT pg_advisory_xact_lock(hashtextextended($1, 0))`, lockKey); err != nil {
			return nil, err
		}

		// 2. Delete artifact (cascades to revisions and links)
		_, err = tx.Exec(ctx, `DELETE FROM cairn.work_artifact WHERE collection = $1 AND artifact_id = $2`, req.Collection, req.ArtifactID)
		if err != nil {
			return nil, err
		}

		// 3. Insert into durable purge fence
		_, err = tx.Exec(ctx, `
			INSERT INTO cairn.work_artifact_purge_fence (collection, artifact_id, canonical_key, purged_by, purged_at)
			VALUES ($1, $2, $3, $4, clock_timestamp())
			ON CONFLICT (collection, artifact_id) DO NOTHING
		`, req.Collection, req.ArtifactID, canonicalKey, s.channel.Principal)
		if err != nil {
			return nil, err
		}

		// 4. Purge cached mutation requests that reference this artifact ID or canonical key (preserve digest, mark ordinary_deleted)
		_, err = tx.Exec(ctx, `
			UPDATE cairn.mutation_request
			SET response = NULL, ordinary_deleted = true
			WHERE operation IN ('work-artifact-register', 'work-artifact-link', 'work-artifact-unlink')
			  AND response IS NOT NULL
			  AND (
			    jsonb_path_exists(response, '$.**.artifact_id ? (@ == $id)', jsonb_build_object('id', $1::text))
			    OR (
			      response->>'collection' = $3
			      AND jsonb_path_exists(response, '$.**.canonical_key ? (@ == $key)', jsonb_build_object('key', $2::text))
			    )
			  )
		`, req.ArtifactID, canonicalKey, req.Collection)
		if err != nil {
			return nil, err
		}

		return nil, nil
	})
	return err
}
