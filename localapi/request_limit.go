package localapi

// RequestBodyLimit bounds encoded JSON, not decoded source content. Evidence
// needs room for its existing 1 MiB source limit with sixfold JSON escaping.
// The store still validates decoded size; other operations retain their cap.
func RequestBodyLimit(operation string) int64 {
	if operation == "evidence" {
		return 8 * 1024 * 1024
	}
	return 128 * 1024
}
