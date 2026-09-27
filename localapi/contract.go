package localapi

// RemoteOperation reports the remote allowlist decision for an operation and
// whether the operation is classified at all. The API contract generator and
// its conformance tests read the live map instead of copying it.
func RemoteOperation(operation string) (allowed, classified bool) {
	allowed, classified = remoteOperations[operation]
	return allowed, classified
}

// RemoteRoleOperations lists the only operations a remote profile whose role
// is not "agent" may call. serveHTTP enforces the same rule; conformance tests
// check that the two agree.
func RemoteRoleOperations() []string { return []string{"version"} }
