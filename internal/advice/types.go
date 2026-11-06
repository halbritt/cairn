package advice

import (
	"context"
	"fmt"
	"time"
)

// Adapter route names.
const (
	RouteCodexSubscription = "codex-subscription"
)

// Allowed action types.
const (
	ActionAnnotateProgress = "annotate_progress"
	ActionAnnotateBlocker  = "annotate_blocker"
	ActionLinkArtifact     = "link_artifact"
)

// Standard refusal and error codes for model advice.
const (
	ErrSubscriptionRequired = "SUBSCRIPTION_REQUIRED"
	ErrQuotaExhausted       = "QUOTA_EXHAUSTED"
	ErrModelTimeout         = "MODEL_TIMEOUT"
	ErrInvalidProposal      = "INVALID_PROPOSAL"
	ErrInputTooLarge        = "INPUT_TOO_LARGE"
	ErrSourceIneligible     = "SOURCE_INELIGIBLE"
	ErrOutputLimit          = "OUTPUT_LIMIT"
	ErrAuthorityDenied      = "AUTHORITY_DENIED"
	ErrModelExecutionFailed = "MODEL_EXECUTION_FAILED"
	ErrCleanupUnconfirmed   = "CLEANUP_UNCONFIRMED"
	ErrAuthUnavailable      = "AUTH_UNAVAILABLE"
	ErrAuthUnsupported      = "AUTH_UNSUPPORTED"
	ErrPolicyRefused        = "POLICY_REFUSED"
	ErrProviderError        = "PROVIDER_ERROR"
	ErrCancelled            = "CANCELLED"
)

// Provider defines the interface for an advice generation model route.
type Provider interface {
	Name() string
	VerifyCapabilities(ctx context.Context) (*CodexCapabilities, error)
	GenerateAdvice(ctx context.Context, snapshot *Snapshot, deadline time.Duration, onLaunch func(ProcessIdentity) error) (*Proposal, *ProcessResult, error)
}

// CodexCapabilities records verified native codex cairn-advice capabilities.
type CodexCapabilities struct {
	Schema         string `json:"schema"`
	RequestSchema  string `json:"request_schema"`
	ResultSchema   string `json:"result_schema"`
	Provider       string `json:"provider"`
	AuthMethod     string `json:"auth_method"`
	OneRequest     bool   `json:"one_request"`
	Tools          bool   `json:"tools"`
	Retries        bool   `json:"retries"`
	UpstreamCommit string `json:"upstream_commit"`
	PatchSHA256    string `json:"patch_sha256"`
}

// Architectural bounds per design spec.
const (
	MaxInputBytes              = 32 * 1024         // 32 KiB canonical input limit
	MaxRawStdoutBytes          = 1 * 1024 * 1024   // 1 MiB raw stdout limit
	MaxCLIErrOutputBytes       = 1 * 1024 * 1024   // 1 MiB raw stderr limit
	MaxOutputBytes             = MaxRawStdoutBytes // Backward-compatible alias
	MaxNormalizedProposalBytes = 16 * 1024         // 16 KiB canonicalized proposal JSON limit
	MaxSources                 = 32                // Maximum cited/included sources
	MaxCandidateArtifacts      = 20                // Maximum candidate artifact revisions
	MaxActions                 = 12                // Maximum proposed actions
	MaxAnnotationTextBytes     = 2048              // Annotation text at most 2 KiB
	MaxSummaryBytes            = 2048              // Proposal summary at most 2 KiB
	MaxRationaleBytes          = 1024              // Rationale at most 1 KiB per action

	DefaultDeadline = 90 * time.Second
	MinDeadline     = 10 * time.Second
	MaxDeadline     = 180 * time.Second
)

// AdviceError captures a domain-classified failure code and message.
type AdviceError struct {
	Code    string
	Message string
	Cause   error
}

func (e *AdviceError) Error() string {
	if e.Cause != nil {
		return fmt.Sprintf("%s: %s: %v", e.Code, e.Message, e.Cause)
	}
	return fmt.Sprintf("%s: %s", e.Code, e.Message)
}

func (e *AdviceError) Unwrap() error {
	return e.Cause
}

func NewAdviceError(code, message string, cause error) *AdviceError {
	return &AdviceError{Code: code, Message: message, Cause: cause}
}

// SnapshotSource is an explicitly selected source reference.
type SnapshotSource struct {
	ID         string    `json:"id"`
	Version    int       `json:"version"`
	Kind       string    `json:"kind"`
	BodySHA256 string    `json:"body_sha256"`
	ObservedAt time.Time `json:"-"`
	Body       string    `json:"body"`
	Title      string    `json:"title,omitempty"`
}

// CandidateArtifact is an artifact revision available for linking.
type CandidateArtifact struct {
	RevisionUUID string `json:"revision_uuid"`
	Kind         string `json:"kind"`
	Locator      string `json:"locator"`
	Role         string `json:"role,omitempty"`
}

// ExistingBlocker represents an open blocker currently on the work item.
type ExistingBlocker struct {
	BlockerID string `json:"blocker_id"`
	Text      string `json:"text"`
	Revision  int64  `json:"revision"`
}

// Snapshot captures the bounded context for model advice generation.
type Snapshot struct {
	BindingID          string              `json:"binding_id"`
	PlaneItemUUID      string              `json:"plane_item_uuid"`
	Identifier         string              `json:"identifier"`
	Title              string              `json:"title"`
	CurrentState       string              `json:"current_state,omitempty"`
	ExistingBlockers   []ExistingBlocker   `json:"existing_blockers,omitempty"`
	CandidateArtifacts []CandidateArtifact `json:"candidate_artifacts,omitempty"`
	SourceReferences   []SnapshotSource    `json:"source_references,omitempty"`
	ObservedAt         time.Time           `json:"-"`
}

// ProgressPayload contains fields for annotate_progress.
type ProgressPayload struct {
	Text             string   `json:"text"`
	SourceReferences []string `json:"source_references,omitempty"`
}

// BlockerPayload contains fields for annotate_blocker.
type BlockerPayload struct {
	BlockerAction     string   `json:"blocker_action"` // "create" or "resolve"
	Text              string   `json:"text,omitempty"`
	ExistingBlockerID string   `json:"existing_blocker_id,omitempty"`
	ExpectedRevision  int64    `json:"expected_revision,omitempty"`
	SourceReferences  []string `json:"source_references,omitempty"`
}

// ArtifactPayload contains fields for link_artifact.
type ArtifactPayload struct {
	ArtifactRevisionID string `json:"artifact_revision_id"`
	Role               string `json:"role"` // "input", "output", "evidence"
}

// ProposedAction represents one discrete action proposed by the model.
type ProposedAction struct {
	ActionIndex int              `json:"action_index"`
	ActionType  string           `json:"action_type"`
	Progress    *ProgressPayload `json:"progress,omitempty"`
	Blocker     *BlockerPayload  `json:"blocker,omitempty"`
	Artifact    *ArtifactPayload `json:"artifact,omitempty"`
	Rationale   string           `json:"rationale,omitempty"`
}

// Proposal is the parsed, validated proposal object returned from the model.
type Proposal struct {
	Actions []ProposedAction `json:"actions"`
	Summary string           `json:"summary,omitempty"`
}

// SubscriptionStatus holds the result of verifying the CLI subscription profile.
type SubscriptionStatus struct {
	LoggedIn         bool   `json:"loggedIn"`
	AuthMethod       string `json:"authMethod"`
	APIProvider      string `json:"apiProvider"`
	SubscriptionType string `json:"subscriptionType"`
	Email            string `json:"email,omitempty"`
}

// ProcessIdentity records the precise process and system metadata captured at launch.
type ProcessIdentity struct {
	PID        int    `json:"pid"`
	PGID       int    `json:"pgid"`
	StartTicks uint64 `json:"start_ticks"`
	BootID     string `json:"boot_id"`
}

// CleanupUnconfirmedError indicates that process-group cleanup could not be confirmed.
type CleanupUnconfirmedError struct {
	Identity ProcessIdentity
	Result   *ProcessResult
	Cause    error
}

func (e *CleanupUnconfirmedError) Error() string {
	if e.Cause != nil {
		return fmt.Sprintf("CLEANUP_UNCONFIRMED: process group -%d cleanup unconfirmed: %v", e.Identity.PGID, e.Cause)
	}
	return fmt.Sprintf("CLEANUP_UNCONFIRMED: process group -%d cleanup unconfirmed", e.Identity.PGID)
}

func (e *CleanupUnconfirmedError) Unwrap() error {
	return e.Cause
}
