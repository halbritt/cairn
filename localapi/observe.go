package localapi

import (
	"bytes"
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"regexp"
	"sort"
	"strings"
	"sync"
	"time"
	"unicode/utf8"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
	"github.com/halbritt/cairn/internal/buildinfo"
)

// Client observations are a bounded, volatile, per-server-process record of the
// declared client implementations that contacted this API. A declaration is a
// reported claim: it never authenticates, selects a principal, machine,
// session or operation, and never changes whether a request is admitted or how
// it executes. Observations are grouped by the authenticated principal, the
// server-provisioned machine and a digest of the validated declaration.
const (
	// ClientDiagnosticsHeader carries one transport-only declaration:
	// unpadded base64url of one canonical JSON object. Servers that predate it
	// ignore it, and a legacy relay drops it; both read as a missing declaration.
	ClientDiagnosticsHeader = "Cairn-Client-Diagnostics"
	ClientDiagnosticsSchema = "cairn.client-diagnostics/1"
	ClientsSchema           = "cairn.clients/1"

	maxDiagnosticsEncoded = 4096
	maxDiagnosticsDecoded = 3072
	maxDiagnosticsDepth   = 6
	maxDiagnosticsString  = 256

	// ObservationRetention is the time after a cohort's last observation at which
	// it is forgotten; there is no persistent record.
	ObservationRetention = 24 * time.Hour
	// ObservationCohortsPerPrincipal and ObservationCohortsTotal bound memory.
	ObservationCohortsPerPrincipal = 128
	ObservationCohortsTotal        = 1024
	// ClientsDefaultLimit and ClientsMaxLimit bound the single response page.
	ClientsDefaultLimit  = 50
	ClientsMaxLimit      = 100
	maxCounterPrincipals = 32
	counterCeiling       = uint64(1) << 62
)

// Metadata states of an observed cohort. Missing, invalid and unrecognized
// declarations are never guessed at: they get one coarse cohort per
// principal, machine and state.
const (
	MetadataPresent      = "present"
	MetadataMissing      = "missing"
	MetadataInvalid      = "invalid"
	MetadataUnrecognized = "unrecognized"
)

var diagnosticSurfaces = map[string]bool{"cli": true, "mcp": true, "python-hook": true, "other": true, "unknown": true}
var diagnosticHarnesses = map[string]bool{"codex": true, "claude": true, "opencode": true, "hermes": true, "agy": true, "other": true, "unknown": true}

// originComponents is the fixed list of caller components allowed to report an
// origin identity. Extending it needs a producer that actually executes in that
// component, because origin is self-reported by that code and never inferred.
var originComponents = map[string]bool{"lifecycle-memory": true}
var implementationIDPattern = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._:+-]{0,63}$`)

// ClientOrigin is optional, self-reported identity of a caller that spawned
// the executable (for example, lifecycle code that invokes the CLI). Absence
// means unknown origin; the executable's own build is never substituted.
type ClientOrigin struct {
	Component        string `json:"component"`
	ImplementationID string `json:"implementation_id,omitempty"`
	Basis            string `json:"basis"`
}

// ClientDiagnostics is the declaration. TransportBuild is the build of the
// executing Go process, constructed by that process, never copied from a caller.
type ClientDiagnostics struct {
	Schema                string                 `json:"schema"`
	Surface               string                 `json:"surface"`
	Harness               string                 `json:"harness"`
	TransportBuild        buildinfo.Info         `json:"transport_build"`
	Origin                *ClientOrigin          `json:"origin,omitempty"`
	RetrievalCapabilities *RetrievalCapabilities `json:"retrieval_capabilities,omitempty"`
}

// EncodeClientDiagnostics returns the header value for a declaration, or "" when
// it cannot be sent within bounds. Unknown surface or harness values are
// reported as unknown, and an invalid build or origin is omitted entirely:
// reporting is best-effort and must never fail an operation.
func EncodeClientDiagnostics(d ClientDiagnostics) string {
	d.Schema = ClientDiagnosticsSchema
	if !diagnosticSurfaces[d.Surface] {
		d.Surface = "unknown"
	}
	if !diagnosticHarnesses[d.Harness] {
		d.Harness = "unknown"
	}
	if !d.TransportBuild.Valid() {
		return ""
	}
	if d.Origin != nil && !validOrigin(*d.Origin) {
		d.Origin = nil
	}
	if d.RetrievalCapabilities != nil {
		if raw, err := json.Marshal(*d.RetrievalCapabilities); err != nil {
			return ""
		} else if _, ok := ParseRetrievalCapabilities(raw); !ok {
			d.RetrievalCapabilities = nil
		}
	}
	raw, err := json.Marshal(d)
	if err != nil || len(raw) > maxDiagnosticsDecoded {
		return ""
	}
	encoded := base64.RawURLEncoding.EncodeToString(raw)
	if len(encoded) > maxDiagnosticsEncoded {
		return ""
	}
	return encoded
}

func validOrigin(o ClientOrigin) bool {
	return originComponents[o.Component] && o.Basis == "reported" && (o.ImplementationID == "" || implementationIDPattern.MatchString(o.ImplementationID))
}

// ParseCallerOrigin reads an origin reported to a process through a dedicated
// bounded value (the lifecycle library supplies one JSON object). Anything not
// exactly an allowlisted origin is dropped, so a caller cannot smuggle other
// fields, a build, a path or a credential into the declaration.
func ParseCallerOrigin(value string) (*ClientOrigin, bool) {
	if value == "" || len(value) > 512 || checkBoundedJSON([]byte(value)) != nil {
		return nil, false
	}
	var origin ClientOrigin
	decoder := json.NewDecoder(strings.NewReader(value))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&origin); err != nil || !validOrigin(origin) {
		return nil, false
	}
	return &origin, true
}

// checkBoundedJSON accepts exactly one JSON object with bounded depth, no
// duplicate member names at any level, bounded strings and valid Unicode.
func checkBoundedJSON(data []byte) error {
	if !utf8.Valid(data) {
		return errInvalidDiagnostics
	}
	type frame struct {
		object    bool
		keys      map[string]bool
		expectKey bool
	}
	var stack []frame
	started := false
	// completed marks a finished value: inside an object, the next token is a key.
	completed := func() {
		if n := len(stack); n > 0 && stack[n-1].object {
			stack[n-1].expectKey = true
		}
	}
	decoder := json.NewDecoder(bytes.NewReader(data))
	for {
		token, err := decoder.Token()
		if err == io.EOF {
			break
		}
		if err != nil {
			return errInvalidDiagnostics
		}
		if len(stack) == 0 {
			if started || token != json.Delim('{') {
				return errInvalidDiagnostics // a non-object or a second top-level value
			}
			started = true
		} else if top := &stack[len(stack)-1]; top.object && top.expectKey {
			if token == json.Delim('}') {
				stack = stack[:len(stack)-1]
				completed()
				continue
			}
			key, ok := token.(string)
			if !ok || len(key) > maxDiagnosticsString || top.keys[key] {
				return errInvalidDiagnostics
			}
			top.keys[key], top.expectKey = true, false
			continue
		}
		switch value := token.(type) {
		case json.Delim:
			if value == '{' || value == '[' {
				if len(stack) >= maxDiagnosticsDepth {
					return errInvalidDiagnostics
				}
				stack = append(stack, frame{object: value == '{', keys: map[string]bool{}, expectKey: value == '{'})
				continue
			}
			stack = stack[:len(stack)-1] // the closing delimiter of an array
			completed()
		case string:
			if len(value) > maxDiagnosticsString {
				return errInvalidDiagnostics
			}
			completed()
		default:
			completed()
		}
	}
	if !started || len(stack) != 0 {
		return errInvalidDiagnostics
	}
	return nil
}

var errInvalidDiagnostics = errors.New("invalid client diagnostics")

var errInvalidLimit = &core.Error{Code: "INVALID_REQUEST", Message: "limit must be an integer from 1 to 100"}

// parseDiagnostics classifies the header values of one request. It retains and
// echoes nothing from an unusable declaration. A declaration with a newer
// schema is unrecognized, never decoded as the current one.
func parseDiagnostics(values []string) (ClientDiagnostics, string) {
	switch {
	case len(values) == 0:
		return ClientDiagnostics{}, MetadataMissing
	case len(values) > 1 || len(values[0]) == 0 || len(values[0]) > maxDiagnosticsEncoded:
		return ClientDiagnostics{}, MetadataInvalid
	}
	decoded, err := base64.RawURLEncoding.Strict().DecodeString(values[0])
	if err != nil || len(decoded) > maxDiagnosticsDecoded || checkBoundedJSON(decoded) != nil {
		return ClientDiagnostics{}, MetadataInvalid
	}
	var head struct {
		Schema *string `json:"schema"`
	}
	if json.Unmarshal(decoded, &head) != nil || head.Schema == nil {
		return ClientDiagnostics{}, MetadataInvalid
	}
	if *head.Schema != ClientDiagnosticsSchema {
		if strings.HasPrefix(*head.Schema, "cairn.client-diagnostics/") && len(*head.Schema) <= 64 {
			return ClientDiagnostics{}, MetadataUnrecognized
		}
		return ClientDiagnostics{}, MetadataInvalid
	}
	// Unknown members of the recognized schema are ignored, never retained.
	var wire struct {
		Surface               *string         `json:"surface"`
		Harness               *string         `json:"harness"`
		TransportBuild        *buildinfo.Info `json:"transport_build"`
		Origin                *ClientOrigin   `json:"origin"`
		RetrievalCapabilities json.RawMessage `json:"retrieval_capabilities"`
	}
	if json.Unmarshal(decoded, &wire) != nil || wire.Surface == nil || wire.Harness == nil || wire.TransportBuild == nil || !wire.TransportBuild.Valid() {
		return ClientDiagnostics{}, MetadataInvalid
	}
	result := ClientDiagnostics{Schema: ClientDiagnosticsSchema, Surface: *wire.Surface, Harness: *wire.Harness, TransportBuild: *wire.TransportBuild}
	if !diagnosticSurfaces[result.Surface] {
		result.Surface = "unknown"
	}
	if !diagnosticHarnesses[result.Harness] {
		result.Harness = "unknown"
	}
	if wire.Origin != nil {
		if !validOrigin(*wire.Origin) {
			return ClientDiagnostics{}, MetadataInvalid
		}
		origin := *wire.Origin
		result.Origin = &origin
	}
	if len(wire.RetrievalCapabilities) > 0 {
		capabilities, ok := ParseRetrievalCapabilities(wire.RetrievalCapabilities)
		if !ok {
			return ClientDiagnostics{}, MetadataInvalid
		}
		result.RetrievalCapabilities = &capabilities
	}
	return result, MetadataPresent
}

type cohortKey struct{ principal, machine, digest string }

type cohort struct {
	id         string
	key        cohortKey
	state      string
	reported   *ClientDiagnostics
	firstWall  time.Time
	lastWall   time.Time
	last       time.Time // carries the monotonic reading when the clock provides one
	seq        uint64    // observation order: ordering and eviction never depend on wall time
	regression bool
}

type counters struct{ evicted, invalid uint64 }

// observer is the volatile registry. It is guarded by one mutex, has no
// background work (expiry runs lazily, bounded by the global cap), writes no
// store, and cannot fail or delay a business operation beyond that lock.
type observer struct {
	mu       sync.Mutex
	now      func() time.Time
	newID    func() string
	epoch    string
	started  time.Time
	rows     map[cohortKey]*cohort
	count    map[string]int
	counters map[string]*counters
	seq      uint64
}

func newObserver() *observer {
	return newObserverWith(time.Now, uuid.NewString)
}

func newObserverWith(now func() time.Time, newID func() string) *observer {
	return &observer{now: now, newID: newID, epoch: newID(), started: now().UTC(), rows: map[cohortKey]*cohort{}, count: map[string]int{}, counters: map[string]*counters{}}
}

func (o *observer) scopeCounters(principal string) *counters {
	c := o.counters[principal]
	if c == nil && len(o.counters) < maxCounterPrincipals {
		c = &counters{}
		o.counters[principal] = c
	}
	return c
}

func saturatingAdd(value *uint64) {
	if *value < counterCeiling {
		*value++
	}
}

func (o *observer) remove(c *cohort) {
	delete(o.rows, c.key)
	if o.count[c.key.principal]--; o.count[c.key.principal] <= 0 {
		delete(o.count, c.key.principal)
	}
}

// expireLocked forgets cohorts whose last observation is older than the
// retention window. The monotonic reading decides; a clock that moved backwards
// never expires a row early. Expiry is the stated retention, not a gap.
func (o *observer) expireLocked(now time.Time) {
	for _, c := range o.rows {
		if now.Sub(c.last) > ObservationRetention {
			o.remove(c)
		}
	}
}

// evictOldestLocked removes the least recently observed cohort of one
// principal, or of all principals when principal is empty. Order is the
// observation sequence, so eviction is deterministic.
func (o *observer) evictOldestLocked(principal string) bool {
	var oldest *cohort
	for _, c := range o.rows {
		if (principal == "" || c.key.principal == principal) && (oldest == nil || c.seq < oldest.seq) {
			oldest = c
		}
	}
	if oldest == nil {
		return false
	}
	o.remove(oldest)
	if scope := o.scopeCounters(oldest.key.principal); scope != nil {
		saturatingAdd(&scope.evicted)
	}
	return true
}

// observe records one authenticated contact. It is called only after
// authentication, never for the clients view itself, and never fails.
func (o *observer) observe(principal, machine string, headers []string) {
	reported, state := parseDiagnostics(headers)
	digest := state
	var descriptor *ClientDiagnostics
	if state == MetadataPresent {
		canonical, err := json.Marshal(reported)
		if err != nil {
			state, digest = MetadataInvalid, MetadataInvalid
		} else {
			sum := sha256.Sum256(canonical)
			digest, descriptor = hex.EncodeToString(sum[:]), &reported
		}
	}
	o.mu.Lock()
	defer o.mu.Unlock()
	now := o.now()
	if state == MetadataInvalid {
		if scope := o.scopeCounters(principal); scope != nil {
			saturatingAdd(&scope.invalid)
		}
	}
	key := cohortKey{principal: principal, machine: machine, digest: digest}
	o.seq++
	if existing := o.rows[key]; existing != nil {
		if now.Sub(existing.last) <= ObservationRetention {
			existing.regression = existing.regression || now.UTC().Before(existing.lastWall)
			existing.last, existing.lastWall, existing.seq = now, now.UTC(), o.seq
			return
		}
		o.remove(existing) // Past retention: forgotten, so this is a fresh first observation.
	}
	o.expireLocked(now)
	for o.count[principal] >= ObservationCohortsPerPrincipal && o.evictOldestLocked(principal) {
	}
	for len(o.rows) >= ObservationCohortsTotal && o.evictOldestLocked("") {
	}
	o.rows[key] = &cohort{id: o.newID(), key: key, state: state, reported: descriptor, firstWall: now.UTC(), lastWall: now.UTC(), last: now, seq: o.seq}
	o.count[principal]++
}

// ClientsRequest is the whole request. There is no cursor, principal or
// "all" selector: a caller can only ever see its own authorization scope.
type ClientsRequest struct {
	Limit *int `json:"limit,omitempty"`
}

// ClientsCounters are scoped to the caller's principal. They never reveal
// another principal's traffic.
type ClientsCounters struct {
	EvictedCohorts      uint64 `json:"evicted_cohorts"`
	InvalidDeclarations uint64 `json:"invalid_declarations"`
}

// ObservedClient is one cohort: reported descriptors that were observed under
// the same authenticated principal and machine. It does not count processes.
type ObservedClient struct {
	CohortID        string             `json:"cohort_id"`
	Principal       string             `json:"principal"`
	MachineID       string             `json:"machine_id,omitempty"`
	MetadataState   string             `json:"metadata_state"`
	OriginState     string             `json:"origin_state"`
	FirstObservedAt time.Time          `json:"first_observed_at"`
	LastObservedAt  time.Time          `json:"last_observed_at"`
	Reported        *ClientDiagnostics `json:"reported,omitempty"`
}

// ClientsResponse is one bounded, partial-by-construction page. An empty list
// means no retained observations in the caller's scope, not that every client
// is current. Timestamps are observations within this server process.
type ClientsResponse struct {
	Schema           string         `json:"schema"`
	Storage          string         `json:"storage"`
	ObservationEpoch string         `json:"observation_epoch"`
	StartedAt        time.Time      `json:"started_at"`
	ObservedAt       time.Time      `json:"observed_at"`
	RetentionSeconds int            `json:"retention_seconds"`
	Server           buildinfo.Info `json:"server"`
	// Exhaustive is always false: observations are volatile, cover one process and one
	// window, and cannot enumerate silent, stripped or older reporters.
	Exhaustive         bool             `json:"exhaustive"`
	Partial            bool             `json:"partial"`
	Truncated          bool             `json:"truncated"`
	WallClockRegressed bool             `json:"wall_clock_regressed"`
	Returned           int              `json:"returned"`
	EligibleRows       int              `json:"eligible_rows"`
	Counters           ClientsCounters  `json:"counters"`
	Rows               []ObservedClient `json:"rows"`
}

// view lists the caller's own cohorts, most recently observed first. It does
// not record the inspection and does not refresh any cohort.
func (o *observer) view(principal, machine string, request ClientsRequest) (ClientsResponse, error) {
	limit := ClientsDefaultLimit
	if request.Limit != nil {
		limit = *request.Limit
		if limit < 1 || limit > ClientsMaxLimit {
			return ClientsResponse{}, errInvalidLimit
		}
	}
	o.mu.Lock()
	defer o.mu.Unlock()
	now := o.now()
	o.expireLocked(now)
	var own []*cohort
	for _, c := range o.rows {
		if c.key.principal == principal && c.key.machine == machine {
			own = append(own, c)
		}
	}
	sort.Slice(own, func(i, j int) bool {
		if own[i].seq != own[j].seq {
			return own[i].seq > own[j].seq
		}
		return own[i].id < own[j].id
	})
	response := ClientsResponse{Schema: ClientsSchema, Storage: "volatile", ObservationEpoch: o.epoch, StartedAt: o.started, ObservedAt: now.UTC(),
		RetentionSeconds: int(ObservationRetention / time.Second), Server: buildinfo.Read(), EligibleRows: len(own), Rows: []ObservedClient{}}
	if scope := o.counters[principal]; scope != nil {
		response.Counters = ClientsCounters{EvictedCohorts: scope.evicted, InvalidDeclarations: scope.invalid}
	}
	if len(own) > limit {
		own, response.Truncated = own[:limit], true
	}
	for _, c := range own {
		originState := "unknown"
		if c.reported != nil && c.reported.Origin != nil {
			originState = "reported"
		}
		response.WallClockRegressed = response.WallClockRegressed || c.regression
		response.Rows = append(response.Rows, ObservedClient{CohortID: c.id, Principal: c.key.principal, MachineID: c.key.machine, MetadataState: c.state,
			OriginState: originState, FirstObservedAt: c.firstWall, LastObservedAt: c.lastWall, Reported: c.reported})
	}
	response.Returned = len(response.Rows)
	response.Partial = response.Truncated || response.Counters.EvictedCohorts > 0
	return response, nil
}
