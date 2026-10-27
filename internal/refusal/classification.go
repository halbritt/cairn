// Package refusal owns the transport classification of Cairn error codes.
// It does not decide whether an operation is authorized or should be retried.
package refusal

// Classification preserves the distinct CLI and HTTP failure contracts. An
// empty message override means that the adapter keeps the original error text.
type Classification struct {
	HTTPStatus  int
	ExitCode    int
	HTTPMessage string
	CLIMessage  string
}

const DefaultHTTPStatus = 422
const privateCLIMessage = "operation failed; inspect the local store and task runtime"

var classifications = map[string]Classification{
	"AGENT_BUSY":               {HTTPStatus: 409, ExitCode: 7, CLIMessage: privateCLIMessage},
	"API_CONNECTION_FAILED":    {HTTPStatus: 422, ExitCode: 7},
	"ARTIFACT_CHANGED":         {HTTPStatus: 422, ExitCode: 4},
	"ARTIFACT_MISMATCH":        {HTTPStatus: 422, ExitCode: 4},
	"ARTIFACT_UNSAFE":          {HTTPStatus: 422, ExitCode: 4},
	"ATTEMPT_TERMINAL":         {HTTPStatus: 409, ExitCode: 4},
	"ATTRIBUTION_CONTRADICTED": {HTTPStatus: 422, ExitCode: 2},
	"ATTRIBUTION_UNRECONCILED": {HTTPStatus: 422, ExitCode: 2},
	"AUTHORITY_DENIED":         {HTTPStatus: 403, ExitCode: 6},
	"AUTHORITY_INACTIVE":       {HTTPStatus: 422, ExitCode: 6},
	"BUDGET_REFUSED":           {HTTPStatus: 422, ExitCode: 2},
	"CHECKPOINT_MISMATCH":      {HTTPStatus: 422, ExitCode: 7},
	"CLIENT_SETUP_FAILED":      {HTTPStatus: 422, ExitCode: 7},
	"CUSTODY_CONFLICT":         {HTTPStatus: 422, ExitCode: 4},
	"DELIVERY_ACTIVE":          {HTTPStatus: 409, ExitCode: 7, CLIMessage: privateCLIMessage},
	"DEPENDENCY_CONFLICT":      {HTTPStatus: 422, ExitCode: 4},
	"DESTINATION_PROHIBITED":   {HTTPStatus: 422, ExitCode: 2},
	"EVIDENCE_UNAVAILABLE":     {HTTPStatus: 422, ExitCode: 2},
	"FORGET_REQUIRED":          {HTTPStatus: 409, ExitCode: 7, CLIMessage: privateCLIMessage},
	"HOLD_RELEASED":            {HTTPStatus: 422, ExitCode: 4},
	"IDEMPOTENCY_CONFLICT":     {HTTPStatus: 409, ExitCode: 4},
	"IMPACT_PREVIEW_REQUIRED":  {HTTPStatus: 422, ExitCode: 2},
	"INAPPLICABLE":             {HTTPStatus: 422, ExitCode: 2},
	"INSTALL_FAILED":           {HTTPStatus: 422, ExitCode: 7},
	"INTEGRITY_FAILURE":        {HTTPStatus: 422, ExitCode: 7},
	"INVALID_REQUEST":          {HTTPStatus: 400, ExitCode: 2},
	"NOT_FOUND":                {HTTPStatus: 404, ExitCode: 3},
	"OPEN_CONFLICT":            {HTTPStatus: 422, ExitCode: 2},
	"PAYLOAD_UNAVAILABLE":      {HTTPStatus: 409, ExitCode: 4},
	"POLICY_UNENFORCEABLE":     {HTTPStatus: 422, ExitCode: 2},
	"POOL_FULL":                {HTTPStatus: 409, ExitCode: 7, CLIMessage: privateCLIMessage},
	"POOL_PAUSED":              {HTTPStatus: 409, ExitCode: 7, CLIMessage: privateCLIMessage},
	"PROTOCOL_UNSUPPORTED":     {HTTPStatus: 426, ExitCode: 7},
	"PURGE_UNRECORDED":         {HTTPStatus: 422, ExitCode: 7},
	"REFUSAL_UNRECORDED":       {HTTPStatus: 500, ExitCode: 7, HTTPMessage: "store operation failed"},
	"REPLAY_INCOMPLETE":        {HTTPStatus: 422, ExitCode: 2},
	"RESTORE_INCOMPLETE":       {HTTPStatus: 422, ExitCode: 7},
	"RESTORE_IN_PROGRESS":      {HTTPStatus: 422, ExitCode: 4},
	"RESTORE_NOT_PAUSED":       {HTTPStatus: 422, ExitCode: 4},
	"RESTORE_PAUSED":           {HTTPStatus: 503, ExitCode: 7},
	"RUN_ALREADY_STARTED":      {HTTPStatus: 409, ExitCode: 4},
	"RUN_FAILED":               {HTTPStatus: 422, ExitCode: 1},
	"SCHEMA_MISMATCH":          {HTTPStatus: 422, ExitCode: 4},
	"SCOPE_AUTHORITY_INACTIVE": {HTTPStatus: 422, ExitCode: 6},
	"SELF_PROMOTION_DENIED":    {HTTPStatus: 403, ExitCode: 6},
	"SESSION_LIVE":             {HTTPStatus: 422, ExitCode: 4},
	"STALE_CURSOR":             {HTTPStatus: 422, ExitCode: 4},
	"STALE_HANDLE":             {HTTPStatus: 409, ExitCode: 4},
	"STALE_LEASE":              {HTTPStatus: 409, ExitCode: 4},
	"STALE_PACKAGE":            {HTTPStatus: 409, ExitCode: 4},
	"STALE_PREVIEW":            {HTTPStatus: 422, ExitCode: 4},
	"STALE_PROPOSAL":           {HTTPStatus: 422, ExitCode: 4},
	"STALE_RESOLUTION":         {HTTPStatus: 409, ExitCode: 7, CLIMessage: privateCLIMessage},
	"STALE_RESTORE":            {HTTPStatus: 422, ExitCode: 4},
	"STALE_SESSION":            {HTTPStatus: 409, ExitCode: 4},
	"STALE_WORKER":             {HTTPStatus: 409, ExitCode: 7, CLIMessage: privateCLIMessage},
	"STORE_ERROR":              {HTTPStatus: 500, ExitCode: 7, HTTPMessage: "store operation failed", CLIMessage: privateCLIMessage},
	"VERSION_CONFLICT":         {HTTPStatus: 409, ExitCode: 4},
	"WORKER_UNAVAILABLE":       {HTTPStatus: 409, ExitCode: 7, CLIMessage: privateCLIMessage},
}

// Classify retains the open-code contracts: HTTP passes unknown refusal text
// through with 422; the CLI suppresses unknown text that may contain store data.
func Classify(code string) Classification {
	if classification, ok := classifications[code]; ok {
		return classification
	}
	return Classification{HTTPStatus: DefaultHTTPStatus, ExitCode: 7, CLIMessage: privateCLIMessage}
}

// HTTPStatusOverrides supplies the same explicit mappings to contract generation.
// The returned map is a copy so callers cannot modify the runtime classifications.
func HTTPStatusOverrides() map[string]int {
	result := make(map[string]int)
	for code, classification := range classifications {
		if classification.HTTPStatus != DefaultHTTPStatus {
			result[code] = classification.HTTPStatus
		}
	}
	return result
}
