package advice

import (
	"fmt"
	"time"
)

// Profile represents a registered immutable advice provider configuration stored in PostgreSQL.
type Profile struct {
	Name                   string    `json:"name"`
	Route                  string    `json:"route"`
	ExecutablePath         string    `json:"executable_path"`
	BinarySHA256           string    `json:"binary_sha256"`
	ExpectedUpstreamCommit string    `json:"expected_upstream_commit"`
	ExpectedPatchSHA256    string    `json:"expected_patch_sha256"`
	CodexHome              string    `json:"codex_home"`
	Model                  string    `json:"model"`
	TimeoutMs              int64     `json:"timeout_ms,omitempty"`
	MaxOutputBytes         int64     `json:"max_output_bytes,omitempty"`
	CreatedAt              time.Time `json:"created_at,omitempty"`
}

// CreateAdapter builds a CodexSubscriptionAdapter configured from this registered profile.
func (p *Profile) CreateAdapter() (*CodexSubscriptionAdapter, error) {
	if p == nil {
		return nil, NewAdviceError(ErrSubscriptionRequired, "nil profile", nil)
	}
	if p.Route != RouteCodexSubscription {
		return nil, NewAdviceError(ErrSubscriptionRequired, fmt.Sprintf("unsupported route %q", p.Route), nil)
	}
	if p.ExecutablePath == "" || p.BinarySHA256 == "" || p.Model == "" || p.CodexHome == "" {
		return nil, NewAdviceError(ErrSubscriptionRequired, fmt.Sprintf("registered profile %q is incomplete: executable_path, binary_sha256, model, and codex_home required", p.Name), nil)
	}
	adapter := NewCodexSubscriptionAdapter(p.ExecutablePath, p.BinarySHA256, p.Model, p.CodexHome, p.ExpectedPatchSHA256)
	if p.ExpectedUpstreamCommit != "" {
		adapter.ExpectedUpstreamCommit = p.ExpectedUpstreamCommit
	}
	if p.TimeoutMs > 0 {
		adapter.TimeoutMs = p.TimeoutMs
	}
	if p.MaxOutputBytes > 0 {
		adapter.MaxOutputBytes = p.MaxOutputBytes
	}
	return adapter, nil
}
