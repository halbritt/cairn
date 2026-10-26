package localapi

// RemoteOperation reports the remote allowlist decision for an operation and
// whether the operation is classified at all. The API contract generator and
// its conformance tests read the live catalog instead of copying it.
func RemoteOperation(operation string) (allowed, classified bool) {
	spec, ok := LookupOperation(operation)
	if !ok {
		return false, false
	}
	return spec.RemoteAllowed, true
}

// RemoteRoleOperations lists the only operations a remote profile whose role
// is not "agent" may call. serveHTTP enforces the same rule; conformance tests
// check that the two agree.
func RemoteRoleOperations() []string {
	var out []string
	for _, spec := range operationCatalog {
		if spec.RemoteObserverAllowed {
			out = append(out, spec.Name)
		}
	}
	return out
}
