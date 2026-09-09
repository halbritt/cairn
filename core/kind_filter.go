package core

import "slices"

// NormalizeKinds validates optional selection labels and returns a sorted, deduplicated copy.
// Labels never establish authority or applicability.
func NormalizeKinds(kinds []string) ([]string, error) {
	if len(kinds) > 8 {
		return nil, failure("INVALID_REQUEST", "kinds accepts at most eight record labels")
	}
	for _, kind := range kinds {
		switch kind {
		case "note", "observation", "claim", "lesson", "procedure", "decision", "preference", "instruction":
		default:
			return nil, failure("INVALID_REQUEST", "unsupported retrieval kind")
		}
	}
	result := slices.Clone(kinds)
	slices.Sort(result)
	return slices.Compact(result), nil
}

func kindAllowed(kinds []string, selection Selection) bool {
	return selection.Mandatory || len(kinds) == 0 || slices.Contains(kinds, selection.Record.Kind)
}
