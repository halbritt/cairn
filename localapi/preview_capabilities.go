package localapi

import (
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

func ParsePreviewCapabilities(raw []byte) (PreviewCapabilities, bool) {
	return decodeCapability(capabilityPreview, raw, func(schema string, flags map[string]bool) PreviewCapabilities {
		return PreviewCapabilities{Schema: schema, EntitiesOmitted: flags["entities_omitted"]}
	})
}

// RecognizesCompactPreviews accepts only an explicit, bounded declaration.
// Missing, duplicate, malformed and future fields never opt a client in.
func RecognizesCompactPreviews(raw json.RawMessage) bool {
	cap, ok := ParsePreviewCapabilities(raw)
	return ok && cap.EntitiesOmitted
}

// PreviewCapabilityChoice belongs to a marker-preserving adapter, never Client
// globally. A facade latches its first successful version response (including
// unsupported/unknown) so later requests/pages/retries don't silently change
// presentation. Errors do not choose a policy; a chosen request is never retried
// with a downgraded policy after an API rollback.
type PreviewCapabilityChoice struct {
	mu              sync.Mutex
	chosen          bool
	compact         bool
	distinctCompile bool
	distinctReplay  bool
}

// Observe reuses a version response the adapter already obtained.
func (c *PreviewCapabilityChoice) Observe(raw json.RawMessage) {
	c.ObserveVersion(raw, nil)
}

// ObserveVersion latches both independent contracts from one version response.
func (c *PreviewCapabilityChoice) ObserveVersion(preview, query json.RawMessage) {
	c.mu.Lock()
	defer c.mu.Unlock()
	if !c.chosen {
		c.choose(preview, query)
	}
}

func (c *PreviewCapabilityChoice) Resolve(ctx context.Context, client *Client) (bool, error) {
	choice, err := c.ResolveInputs(ctx, client)
	return choice.CompactPreview, err
}

func (c *PreviewCapabilityChoice) choose(preview, query json.RawMessage) {
	c.compact, c.chosen = RecognizesCompactPreviews(preview), true
	capability, known := ParseQueryInputCapabilities(query)
	c.distinctCompile = known && capability.IndexedSemanticQueryV1
	c.distinctReplay = known
}

func (c *PreviewCapabilityChoice) ResolveInputs(ctx context.Context, client *Client) (InputCapabilityChoice, error) {
	c.mu.Lock()
	defer c.mu.Unlock()
	if !c.chosen {
		declarations, err := probeVersionDeclarations(ctx, client, capabilityPreview, capabilityQueryInput)
		if err != nil {
			return InputCapabilityChoice{}, err
		}
		c.choose(declarations[capabilityPreview], declarations[capabilityQueryInput])
	}
	return InputCapabilityChoice{c.compact, c.distinctCompile, c.distinctReplay}, nil
}
