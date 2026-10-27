package main

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"

	"github.com/halbritt/cairn/core"
)

// Attention is an opt-in presentation of one bounded operator review page.
// The evaluation lives in core (SummarizeAttention, DetectStarvation,
// NotifyStoppedPublishers); the CLI owns only the private state file and
// the review scope that keys it. It neither claims deliveries nor changes the
// host's recovery decisions.

// Optional state is local presentation memory, containing IDs and closed labels,
// never source bodies. Scope changes fail rather than silently clear old alerts.
func withAttentionCache(options core.AttentionOptions, scope string, run func(core.AttentionCache) (core.AttentionResult, core.AttentionCache, error)) (core.AttentionResult, error) {
	var result core.AttentionResult
	stateFile := LockedStateFile[core.AttentionCache]{
		Path:             options.StateFile,
		TempPattern:      ".cairn-attention-*",
		MaxBytes:         512 * 1024,
		CreateDir:        true,
		AbsPathError:     "attention state_file must be absolute",
		SizeLimitError:   "attention state exceeds size limit",
		ParseErrorPrefix: "invalid attention state",
		Default: func() core.AttentionCache {
			return core.AttentionCache{Schema: "cairn.delivery-attention/1", Scope: scope, Deliveries: map[string]core.AttentionEntry{}}
		},
		Validate: func(c core.AttentionCache) error {
			if c.Schema != "cairn.delivery-attention/1" || c.Scope != scope || c.Deliveries == nil || len(c.Deliveries) > 1000 {
				return invalid("attention state scope or schema mismatch; use a separate state file")
			}
			return nil
		},
	}
	err := stateFile.Update(func(current core.AttentionCache, _ func(core.AttentionCache) error) (core.AttentionCache, bool, error) {
		var next core.AttentionCache
		var runErr error
		result, next, runErr = run(current)
		return next, runErr == nil, runErr
	})
	return result, err
}

func attentionScope(source any) string {
	raw, _ := json.Marshal(source)
	sum := sha256.Sum256(raw)
	return hex.EncodeToString(sum[:])
}
