package core

import "encoding/json"

// MemoryBudget is an opt-in sealed limit on index delivery and its receipt's
// expansion allowance. AvailableTokens remains the context-room policy input.
// A nil extension preserves every historical schema and its encoded bytes.
type MemoryBudget struct {
	Schema string `json:"schema" cbor:"schema"`
	Bytes  int    `json:"bytes" cbor:"bytes"`
}

func memoryRoom(p SemanticPackage) int {
	if p.MemoryBudget != nil {
		return p.MemoryBudget.Bytes
	}
	return p.AvailableTokens
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
	if p.MemoryBudget != nil && (p.MemoryBudget.Schema != "cairn.memory-budget/1" || p.Mode != "index" || p.Purpose != "context" || p.MemoryBudget.Bytes < 256 || p.MemoryBudget.Bytes > p.AvailableTokens) {
		return failure("INTEGRITY_FAILURE", "historical memory budget contract is invalid")
	}
	return nil
}
