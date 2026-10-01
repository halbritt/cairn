package localapi

import (
	"bytes"
	"encoding/json"
	"io"
)

// ParseRetrievalCapabilities reads a retrieval capability declaration that crossed a trust boundary.
// Deliberately closed and bounded. Unknown schema/fields, duplicate members,
// missing flags and contradictory declarations remain unknown, not unsupported.
func ParseRetrievalCapabilities(raw []byte) (RetrievalCapabilities, bool) {
	var result RetrievalCapabilities
	if len(raw) == 0 || len(raw) > 256 {
		return result, false
	}
	decoder := json.NewDecoder(bytes.NewReader(raw))
	token, err := decoder.Token()
	if err != nil || token != json.Delim('{') {
		return result, false
	}
	seen := map[string]bool{}
	for decoder.More() {
		token, err = decoder.Token()
		if err != nil {
			return result, false
		}
		key, ok := token.(string)
		if !ok || seen[key] {
			return result, false
		}
		seen[key] = true
		var value json.RawMessage
		if err = decoder.Decode(&value); err != nil {
			return result, false
		}
		switch key {
		case "schema":
			if err = json.Unmarshal(value, &result.Schema); err != nil {
				return result, false
			}
		case "search_memory_budget_bytes", "search_min_pull_bytes":
			if !bytes.Equal(value, []byte("true")) && !bytes.Equal(value, []byte("false")) {
				return result, false
			}
			flag := bytes.Equal(value, []byte("true"))
			if key == "search_memory_budget_bytes" {
				result.SearchMemoryBudgetBytes = flag
			} else {
				result.SearchMinPullBytes = flag
			}
		default:
			return result, false
		}
	}
	token, err = decoder.Token()
	if err != nil || token != json.Delim('}') {
		return result, false
	}
	if _, err = decoder.Token(); err != io.EOF {
		return result, false
	}
	return result, len(seen) == 3 && result.Schema == RetrievalCapabilitiesSchema && (!result.SearchMinPullBytes || result.SearchMemoryBudgetBytes)
}
