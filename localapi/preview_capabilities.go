package localapi

import (
	"bytes"
	"context"
	"encoding/json"
	"sync"
)

const PreviewCapabilitiesSchema = "cairn.preview-capabilities/1"

// PreviewCapabilities declares a presentation contract, not access or relevance.
// Keep it separate from retrieval capabilities so existing closed readers work.
type PreviewCapabilities struct {
	Schema          string `json:"schema"`
	EntitiesOmitted bool   `json:"entities_omitted"`
}

// RecognizesCompactPreviews accepts only an explicit, bounded declaration.
// Missing, duplicate, malformed and future fields never opt a client in.
func RecognizesCompactPreviews(raw json.RawMessage) bool {
	if len(raw) == 0 || len(raw) > 256 {
		return false
	}
	members, tail, ok := topLevelMembers(raw)
	if !ok || len(bytes.TrimSpace(tail)) != 0 || len(members) != 2 {
		return false
	}
	seen := map[string]bool{}
	for _, member := range members {
		if seen[member.key] {
			return false
		}
		seen[member.key] = true
	}
	if !seen["schema"] || !seen["entities_omitted"] {
		return false
	}
	var fields map[string]json.RawMessage
	if json.Unmarshal(raw, &fields) != nil {
		return false
	}
	var schema string
	return json.Unmarshal(fields["schema"], &schema) == nil && schema == PreviewCapabilitiesSchema && bytes.Equal(bytes.TrimSpace(fields["entities_omitted"]), []byte("true"))
}

// PreviewCapabilityChoice belongs to a marker-preserving adapter, never Client
// globally. A facade latches its first successful version response (including
// unsupported/unknown) so later requests/pages/retries don't silently change
// presentation. Errors do not choose a policy; a chosen request is never retried
// with a downgraded policy after an API rollback.
type PreviewCapabilityChoice struct {
	mu      sync.Mutex
	chosen  bool
	compact bool
}

// Observe reuses a version response the adapter already obtained.
func (c *PreviewCapabilityChoice) Observe(raw json.RawMessage) {
	c.mu.Lock()
	defer c.mu.Unlock()
	if !c.chosen {
		c.compact, c.chosen = RecognizesCompactPreviews(raw), true
	}
}

func (c *PreviewCapabilityChoice) Resolve(ctx context.Context, client *Client) (bool, error) {
	c.mu.Lock()
	defer c.mu.Unlock()
	if !c.chosen {
		var version struct {
			PreviewCapabilities json.RawMessage `json:"preview_capabilities"`
		}
		if err := client.Call(ctx, "version", struct{}{}, &version); err != nil {
			return false, err
		}
		c.compact, c.chosen = RecognizesCompactPreviews(version.PreviewCapabilities), true
	}
	return c.compact, nil
}
