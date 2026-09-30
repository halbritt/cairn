package core

import "encoding/json"

// MemoryBudget is an opt-in sealed limit on index delivery and its receipt's
// expansion allowance. AvailableTokens remains the context-room policy input.
// A nil extension preserves every historical schema and its encoded bytes.
type MemoryBudget struct {
	Schema       string `json:"schema" cbor:"schema"`
	Bytes        int    `json:"bytes" cbor:"bytes"`
	MinPullBytes int    `json:"min_pull_bytes,omitempty" cbor:"min_pull_bytes,omitempty"`
}

func memoryRoom(p SemanticPackage) int {
	if p.MemoryBudget != nil {
		return p.MemoryBudget.Bytes
	}
	return p.AvailableTokens
}

// The minimum reserves charged expansion room within the existing allowance.
// It is not a source-body size promise or an extra grant.
func minPullBytes(p SemanticPackage) int {
	if p.MemoryBudget != nil {
		return p.MemoryBudget.MinPullBytes
	}
	return 0
}

func indexMemoryRoom(p SemanticPackage) int {
	return memoryRoom(p) - minPullBytes(p)
}

// Index delivery reserves the rendered package, complete native pull handles,
// and response metadata. Explicit budgets also reserve JSON string escaping
// used by MCP text content; legacy receipts keep their original accounting.
func indexMemoryCost(p SemanticPackage) (int, error) {
	rendered, err := (Package{Semantic: p}).Render()
	if err != nil {
		return 0, err
	}
	if p.MemoryBudget == nil {
		return len(rendered) + 160*len(p.Index) + 512, nil
	}
	quoted, err := json.Marshal(rendered)
	return len(quoted) + 256*len(p.Index) + 512, err
}

// Expansion payloads are JSON before transport. Charging their JSON string
// representation covers MCP's second escaping pass as well as plain JSON.
func expansionMemoryCost(encoded []byte, budget *MemoryBudget) (int, error) {
	if budget == nil {
		return len(encoded) + 256, nil
	}
	quoted, err := json.Marshal(string(encoded))
	return len(quoted) + 256, err
}

func validateMemoryBudget(p SemanticPackage) error {
	b := p.MemoryBudget
	if b == nil {
		return nil
	}
	if p.Mode != "index" || p.Purpose != "context" || b.Bytes < 256 || b.Bytes > p.AvailableTokens {
		return failure("INTEGRITY_FAILURE", "historical memory budget contract is invalid")
	}
	switch b.Schema {
	case "cairn.memory-budget/1":
		if b.MinPullBytes == 0 {
			return nil
		}
	case "cairn.memory-budget/2":
		if b.MinPullBytes > 0 && b.MinPullBytes <= min(24000, b.Bytes) {
			return nil
		}
	}
	return failure("INTEGRITY_FAILURE", "historical memory budget reserve is invalid")
}
