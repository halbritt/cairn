package localapi

import (
	"crypto/sha256"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/halbritt/cairn/core"
	"github.com/halbritt/cairn/internal/buildinfo"
)

// ---- helpers --------------------------------------------------------------

// fakeClock has no monotonic reading, so tests control "wall" time exactly.
type fakeClock struct{ now time.Time }

func newFakeClock() *fakeClock {
	return &fakeClock{now: time.Date(2026, 9, 30, 12, 0, 0, 0, time.UTC)}
}
func (c *fakeClock) Now() time.Time          { return c.now }
func (c *fakeClock) advance(d time.Duration) { c.now = c.now.Add(d) }

func testObserver(clock *fakeClock) *observer {
	var next int
	return newObserverWith(clock.Now, func() string { next++; return fmt.Sprintf("id-%04d", next) })
}

func testBuild() buildinfo.Info {
	return buildinfo.Info{Schema: "cairn.build/1", GoVersion: "go1.25.0", ModuleVersion: "(devel)"}
}

// declare encodes a valid declaration; revision i makes the descriptor distinct.
func declare(i int, mutate ...func(*ClientDiagnostics)) string {
	build := testBuild()
	build.VCS, build.Revision = "git", fmt.Sprintf("%040x", i)
	d := ClientDiagnostics{Surface: "cli", Harness: "unknown", TransportBuild: build}
	for _, change := range mutate {
		change(&d)
	}
	return EncodeClientDiagnostics(d)
}

func raw(object string) string { return base64.RawURLEncoding.EncodeToString([]byte(object)) }

const validBuildJSON = `{"schema":"cairn.build/1","go_version":"go1.25.0","vcs_modified":null}`

func view(t *testing.T, o *observer, principal, machine string, limit *int) ClientsResponse {
	t.Helper()
	response, err := o.view(principal, machine, ClientsRequest{Limit: limit})
	if err != nil {
		t.Fatal(err)
	}
	return response
}

func intp(v int) *int { return &v }

// ---- parsing --------------------------------------------------------------

func TestDeclarationRoundTripsWithinBounds(t *testing.T) {
	value := declare(7, func(d *ClientDiagnostics) {
		d.Surface, d.Harness = "mcp", "claude"
		d.RetrievalCapabilities = CurrentRetrievalCapabilities()
		d.Origin = &ClientOrigin{Component: "lifecycle-memory", ImplementationID: "memory-1.2", Basis: "reported"}
	})
	if len(value) == 0 || len(value) > maxDiagnosticsEncoded || strings.ContainsAny(value, "=+/") {
		t.Fatalf("not unpadded base64url within bounds: %d %q", len(value), value)
	}
	got, state := parseDiagnostics([]string{value})
	if state != MetadataPresent || got.Surface != "mcp" || got.Harness != "claude" || got.Origin == nil || got.Origin.ImplementationID != "memory-1.2" ||
		got.RetrievalCapabilities == nil || !got.RetrievalCapabilities.SearchMemoryBudgetBytes || got.TransportBuild.Revision != fmt.Sprintf("%040x", 7) {
		t.Fatalf("%v %+v", state, got)
	}
}

func TestEncodingIsBestEffortAndNeverSendsAnUnboundedOrInvalidDeclaration(t *testing.T) {
	if EncodeClientDiagnostics(ClientDiagnostics{Surface: "cli", TransportBuild: buildinfo.Info{Schema: "x"}}) != "" {
		t.Fatal("an invalid build must not be sent")
	}
	// Unknown enums are reported as unknown; an invalid origin is omitted, not sent.
	value := declare(1, func(d *ClientDiagnostics) {
		d.Surface, d.Harness = "toaster", "robot"
		d.Origin = &ClientOrigin{Component: "not-in-the-list", Basis: "reported"}
		d.RetrievalCapabilities = &RetrievalCapabilities{Schema: "other/1"}
	})
	got, state := parseDiagnostics([]string{value})
	if state != MetadataPresent || got.Surface != "unknown" || got.Harness != "unknown" || got.Origin != nil || got.RetrievalCapabilities != nil {
		t.Fatalf("%v %+v", state, got)
	}
}

func TestMalformedDeclarationsAreClassifiedAndNeverRetained(t *testing.T) {
	long := strings.Repeat("a", maxDiagnosticsString+1)
	header := func(extra string) string {
		return raw(`{"schema":"` + ClientDiagnosticsSchema + `","surface":"cli","harness":"unknown","transport_build":` + validBuildJSON + extra + `}`)
	}
	deep := strings.Repeat(`{"a":`, maxDiagnosticsDepth+1) + `1` + strings.Repeat(`}`, maxDiagnosticsDepth+1)
	for name, test := range map[string]struct {
		values []string
		want   string
	}{
		"absent":                      {nil, MetadataMissing},
		"valid":                       {[]string{header("")}, MetadataPresent},
		"unknown members ignored":     {[]string{header(`,"future":{"x":1}`)}, MetadataPresent},
		"duplicate header":            {[]string{header(""), header("")}, MetadataInvalid},
		"empty value":                 {[]string{""}, MetadataInvalid},
		"encoded too long":            {[]string{strings.Repeat("A", maxDiagnosticsEncoded+1)}, MetadataInvalid},
		"decoded too long":            {[]string{raw(`{"schema":"` + ClientDiagnosticsSchema + `","pad":"` + strings.Repeat("b", maxDiagnosticsString) + strings.Repeat(`","pad2":"`+strings.Repeat("c", 200), 12) + `"}`)}, MetadataInvalid},
		"not base64url":               {[]string{"not*base64"}, MetadataInvalid},
		"padded":                      {[]string{base64.URLEncoding.EncodeToString([]byte(`{"schema":"x"}`))}, MetadataInvalid},
		"standard alphabet":           {[]string{"+/+/"}, MetadataInvalid},
		"array":                       {[]string{raw(`[]`)}, MetadataInvalid},
		"scalar":                      {[]string{raw(`1`)}, MetadataInvalid},
		"truncated":                   {[]string{raw(`{"schema":"` + ClientDiagnosticsSchema + `"`)}, MetadataInvalid},
		"trailing value":              {[]string{raw(`{"schema":"` + ClientDiagnosticsSchema + `"} {}`)}, MetadataInvalid},
		"duplicate top key":           {[]string{header(`,"surface":"mcp"`)}, MetadataInvalid},
		"duplicate nested key":        {[]string{raw(`{"schema":"` + ClientDiagnosticsSchema + `","surface":"cli","harness":"unknown","transport_build":{"schema":"cairn.build/1","go_version":"go1.25.0","go_version":"go1.26.0"}}`)}, MetadataInvalid},
		"too deep":                    {[]string{raw(`{"schema":"` + ClientDiagnosticsSchema + `","x":` + deep + `}`)}, MetadataInvalid},
		"long string":                 {[]string{header(`,"x":"` + long + `"`)}, MetadataInvalid},
		"long key":                    {[]string{header(`,"` + long + `":1`)}, MetadataInvalid},
		"invalid utf8":                {[]string{base64.RawURLEncoding.EncodeToString([]byte("{\"schema\":\"\xff\"}"))}, MetadataInvalid},
		"newer schema":                {[]string{raw(`{"schema":"cairn.client-diagnostics/2","surface":"cli"}`)}, MetadataUnrecognized},
		"foreign schema":              {[]string{raw(`{"schema":"someone.else/1"}`)}, MetadataInvalid},
		"schema not a string":         {[]string{raw(`{"schema":1}`)}, MetadataInvalid},
		"missing schema":              {[]string{raw(`{"surface":"cli"}`)}, MetadataInvalid},
		"missing surface":             {[]string{raw(`{"schema":"` + ClientDiagnosticsSchema + `","harness":"unknown","transport_build":` + validBuildJSON + `}`)}, MetadataInvalid},
		"missing build":               {[]string{raw(`{"schema":"` + ClientDiagnosticsSchema + `","surface":"cli","harness":"unknown"}`)}, MetadataInvalid},
		"build of another schema":     {[]string{raw(`{"schema":"` + ClientDiagnosticsSchema + `","surface":"cli","harness":"unknown","transport_build":{"schema":"x","go_version":"go1.25.0"}}`)}, MetadataInvalid},
		"build with a bad go value":   {[]string{raw(`{"schema":"` + ClientDiagnosticsSchema + `","surface":"cli","harness":"unknown","transport_build":{"schema":"cairn.build/1","go_version":"<script>"}}`)}, MetadataInvalid},
		"build with a bad revision":   {[]string{raw(`{"schema":"` + ClientDiagnosticsSchema + `","surface":"cli","harness":"unknown","transport_build":{"schema":"cairn.build/1","go_version":"go1.25.0","vcs":"git","vcs_revision":"not-hex"}}`)}, MetadataInvalid},
		"origin component not listed": {[]string{header(`,"origin":{"component":"anything","basis":"reported"}`)}, MetadataInvalid},
		"origin basis not reported":   {[]string{header(`,"origin":{"component":"lifecycle-memory","basis":"verified"}`)}, MetadataInvalid},
		"origin id with a path":       {[]string{header(`,"origin":{"component":"lifecycle-memory","implementation_id":"/home/x/memory.py","basis":"reported"}`)}, MetadataInvalid},
		"capabilities unknown field":  {[]string{header(`,"retrieval_capabilities":{"schema":"cairn.retrieval-capabilities/1","search_memory_budget_bytes":true,"search_min_pull_bytes":true,"x":true}`)}, MetadataInvalid},
		"capabilities contradict":     {[]string{header(`,"retrieval_capabilities":{"schema":"cairn.retrieval-capabilities/1","search_memory_budget_bytes":false,"search_min_pull_bytes":true}`)}, MetadataInvalid},
	} {
		t.Run(name, func(t *testing.T) {
			got, state := parseDiagnostics(test.values)
			if state != test.want {
				t.Fatalf("state %q want %q", state, test.want)
			}
			if state != MetadataPresent && (got.Surface != "" || got.TransportBuild.GoVersion != "" || got.Origin != nil) {
				t.Fatalf("an unusable declaration left content behind: %+v", got)
			}
		})
	}
}

func TestUnknownMembersAreIgnoredAndNeverRetainedOrEchoed(t *testing.T) {
	canary := "private-prompt-canary-/home/user/secret"
	value := raw(`{"schema":"` + ClientDiagnosticsSchema + `","surface":"cli","harness":"unknown","transport_build":` + validBuildJSON + `,"prompt":"` + canary + `","path":{"x":"` + canary + `"}}`)
	clock := newFakeClock()
	o := testObserver(clock)
	o.observe("agent:a", "", []string{value})
	encoded, err := json.Marshal(view(t, o, "agent:a", "", nil))
	if err != nil || strings.Contains(string(encoded), canary) || strings.Contains(string(encoded), "prompt") {
		t.Fatalf("an ignored member was retained or echoed: %v %s", err, encoded)
	}
}

func TestCallerOriginIsAllowlistedAndBounded(t *testing.T) {
	if origin, ok := ParseCallerOrigin(`{"component":"lifecycle-memory","implementation_id":"memory.v3","basis":"reported"}`); !ok || origin.ImplementationID != "memory.v3" {
		t.Fatalf("%v %v", origin, ok)
	}
	for _, value := range []string{
		"", `{}`, `[]`, `{"component":"lifecycle-memory","basis":"reported","token":"x"}`, `{"component":"hermes","basis":"reported"}`,
		`{"component":"lifecycle-memory","basis":"inferred"}`, `{"component":"lifecycle-memory","basis":"reported","component":"lifecycle-memory"}`,
		`{"component":"lifecycle-memory","implementation_id":"` + strings.Repeat("a", 65) + `","basis":"reported"}`,
		`{"component":"lifecycle-memory","basis":"reported"}` + strings.Repeat(" ", 600),
	} {
		if origin, ok := ParseCallerOrigin(value); ok || origin != nil {
			t.Fatalf("accepted %q", value)
		}
	}
}

// The Python lifecycle memory producer reports the full lowercase SHA-256 of its observed source as
// implementation_id, or only its component when the source is unknown. Both exact forms must stay
// within the existing allowlist: a reported claim, never a build, path or identity.
func TestCallerOriginAcceptsTheLifecycleProducersSourceDigestAndUnknownForms(t *testing.T) {
	digest := strings.Repeat("0123456789abcdef", 4)
	known := `{"component":"lifecycle-memory","implementation_id":"` + digest + `","basis":"reported"}`
	unknown := `{"component":"lifecycle-memory","basis":"reported"}`
	if len(known) > 512 {
		t.Fatalf("the producer's declaration is %d bytes", len(known))
	}
	if origin, ok := ParseCallerOrigin(known); !ok || origin.ImplementationID != digest || origin.Component != "lifecycle-memory" || origin.Basis != "reported" {
		t.Fatalf("%v %v", origin, ok)
	}
	if origin, ok := ParseCallerOrigin(unknown); !ok || origin.ImplementationID != "" {
		t.Fatalf("%v %v", origin, ok)
	}
	origin, _ := ParseCallerOrigin(known)
	parsed, state := parseDiagnostics([]string{declare(1, func(d *ClientDiagnostics) { d.Origin = origin })})
	if state != MetadataPresent || parsed.Origin == nil || parsed.Origin.ImplementationID != digest {
		t.Fatalf("%v %+v", state, parsed)
	}
}

// ---- cohorts, retention, caps ----------------------------------------------

func TestCohortsGroupByPrincipalMachineAndValidatedDescriptor(t *testing.T) {
	clock := newFakeClock()
	o := testObserver(clock)
	o.observe("agent:a", "", []string{declare(1)})
	clock.advance(time.Minute)
	o.observe("agent:a", "", []string{declare(1)}) // identical descriptor: the same cohort
	clock.advance(time.Minute)
	o.observe("agent:a", "", []string{declare(2)})
	o.observe("agent:b", "", []string{declare(1)})
	o.observe("agent:a", "machine-9", []string{declare(1)})

	own := view(t, o, "agent:a", "", nil)
	if own.EligibleRows != 2 || own.Returned != 2 || own.Truncated || own.Partial {
		t.Fatalf("%+v", own)
	}
	first := own.Rows[1] // oldest observation order last
	if first.FirstObservedAt != clock.now.Add(-2*time.Minute) || first.LastObservedAt != clock.now.Add(-time.Minute) {
		t.Fatalf("repeat contact must move last, not first: %+v", first)
	}
	if view(t, o, "agent:b", "", nil).EligibleRows != 1 || view(t, o, "agent:a", "machine-9", nil).EligibleRows != 1 {
		t.Fatal("principal or machine did not isolate cohorts")
	}
	if view(t, o, "agent:c", "", nil).EligibleRows != 0 || view(t, o, "agent:a", "machine-other", nil).EligibleRows != 0 {
		t.Fatal("another scope saw rows")
	}
}

func TestMissingAndInvalidDeclarationsAreCoarseAndCounted(t *testing.T) {
	clock := newFakeClock()
	o := testObserver(clock)
	for i := 0; i < 5; i++ {
		o.observe("agent:a", "", nil)
		o.observe("agent:a", "", []string{fmt.Sprintf("garbage-%d", i)})
	}
	o.observe("agent:a", "", []string{raw(`{"schema":"cairn.client-diagnostics/9"}`)})
	got := view(t, o, "agent:a", "", nil)
	states := map[string]int{}
	for _, row := range got.Rows {
		states[row.MetadataState]++
		if row.Reported != nil || row.OriginState != "unknown" {
			t.Fatalf("an unusable declaration must not look reported: %+v", row)
		}
	}
	if len(got.Rows) != 3 || states[MetadataMissing] != 1 || states[MetadataInvalid] != 1 || states[MetadataUnrecognized] != 1 {
		t.Fatalf("one coarse row per state expected: %v", states)
	}
	if got.Counters.InvalidDeclarations != 5 {
		t.Fatalf("invalid count %d", got.Counters.InvalidDeclarations)
	}
	if view(t, o, "agent:b", "", nil).Counters.InvalidDeclarations != 0 {
		t.Fatal("counters leaked across principals")
	}
}

func TestRetentionIsExactDeterministicAndNotAGap(t *testing.T) {
	clock := newFakeClock()
	o := testObserver(clock)
	o.observe("agent:a", "", []string{declare(1)})
	clock.advance(ObservationRetention)
	if got := view(t, o, "agent:a", "", nil); got.EligibleRows != 1 {
		t.Fatalf("a row exactly at the window must remain: %+v", got)
	}
	clock.advance(time.Nanosecond)
	got := view(t, o, "agent:a", "", nil)
	if got.EligibleRows != 0 || got.Partial || got.Counters.EvictedCohorts != 0 || got.Rows == nil {
		t.Fatalf("expiry is the stated retention, not an eviction; the list is empty, not null: %+v", got)
	}
	// A returning client past the window is a fresh first observation.
	o.observe("agent:a", "", []string{declare(1)})
	again := view(t, o, "agent:a", "", nil)
	if again.EligibleRows != 1 || !again.Rows[0].FirstObservedAt.Equal(clock.now) {
		t.Fatalf("%+v", again)
	}
	// A row refreshed inside the window survives the window that would have expired it.
	clock.advance(ObservationRetention - time.Hour)
	o.observe("agent:a", "", []string{declare(1)})
	clock.advance(ObservationRetention - time.Hour)
	if view(t, o, "agent:a", "", nil).EligibleRows != 1 {
		t.Fatal("last contact, not first, starts the window")
	}
}

func TestACohortPastRetentionIsForgottenEvenIfNothingListedItSince(t *testing.T) {
	// Expiry is lazy: no listing or insertion ran between the two contacts, yet the
	// returning client is a fresh first observation, not a refresh of a stale row.
	clock := newFakeClock()
	o := testObserver(clock)
	o.observe("agent:a", "", []string{declare(1)})
	first := clock.now
	clock.advance(ObservationRetention + time.Second)
	o.observe("agent:a", "", []string{declare(1)})
	got := view(t, o, "agent:a", "", nil)
	if got.EligibleRows != 1 || got.Rows[0].FirstObservedAt.Equal(first) || !got.Rows[0].FirstObservedAt.Equal(clock.now) || got.Counters.EvictedCohorts != 0 {
		t.Fatalf("%+v", got)
	}
}

func TestPerPrincipalCapEvictsLeastRecentlyObservedDeterministically(t *testing.T) {
	run := func() ([]string, ClientsResponse) {
		clock := newFakeClock()
		o := testObserver(clock)
		for i := 0; i < ObservationCohortsPerPrincipal; i++ {
			o.observe("agent:a", "", []string{declare(i)})
			clock.advance(time.Second)
		}
		o.observe("agent:a", "", []string{declare(0)}) // cohort 0 becomes the most recent
		o.observe("agent:a", "", []string{declare(1000)})
		got := view(t, o, "agent:a", "", intp(ClientsMaxLimit))
		var revisions []string
		for _, row := range got.Rows {
			revisions = append(revisions, row.Reported.TransportBuild.Revision)
		}
		return revisions, got
	}
	first, got := run()
	second, _ := run()
	if strings.Join(first, ",") != strings.Join(second, ",") {
		t.Fatal("eviction is not deterministic")
	}
	if got.EligibleRows != ObservationCohortsPerPrincipal || got.Counters.EvictedCohorts != 1 || !got.Partial {
		t.Fatalf("%+v", got.Counters)
	}
	present := map[string]bool{}
	for _, revision := range first {
		present[revision] = true
	}
	if present[fmt.Sprintf("%040x", 1)] || !present[fmt.Sprintf("%040x", 0)] || !present[fmt.Sprintf("%040x", 1000)] {
		t.Fatal("the least recently observed cohort, and only it, must be evicted")
	}
}

func TestGlobalCapEvictsAcrossPrincipalsWithoutRevealingWhoCausedIt(t *testing.T) {
	clock := newFakeClock()
	o := testObserver(clock)
	principals := ObservationCohortsTotal / ObservationCohortsPerPrincipal
	for p := 0; p < principals; p++ {
		for i := 0; i < ObservationCohortsPerPrincipal; i++ {
			o.observe(fmt.Sprintf("agent:%d", p), "", []string{declare(i)})
			clock.advance(time.Millisecond)
		}
	}
	o.observe("agent:late", "", []string{declare(1)})
	o.mu.Lock()
	total := len(o.rows)
	o.mu.Unlock()
	if total != ObservationCohortsTotal {
		t.Fatalf("global cap %d", total)
	}
	victim, newcomer := view(t, o, "agent:0", "", nil), view(t, o, "agent:late", "", nil)
	if victim.Counters.EvictedCohorts != 1 || !victim.Partial {
		t.Fatalf("the evicted principal must see the gap: %+v", victim.Counters)
	}
	if newcomer.Counters.EvictedCohorts != 0 || newcomer.Partial || newcomer.EligibleRows != 1 {
		t.Fatalf("a scoped response must not reveal other principals' traffic: %+v", newcomer)
	}
	encoded, _ := json.Marshal(newcomer)
	if strings.Contains(string(encoded), "agent:0") {
		t.Fatal("another principal's label leaked")
	}
}

func TestBookkeepingStaysBoundedUnderFloodsOfInvalidAndDistinctDeclarations(t *testing.T) {
	clock := newFakeClock()
	o := testObserver(clock)
	for i := 0; i < 5000; i++ {
		o.observe("agent:a", "", []string{fmt.Sprintf("garbage-%d", i)}) // distinct invalid values: one coarse row, never one each
		o.observe("agent:a", "", nil)
	}
	got := view(t, o, "agent:a", "", intp(ClientsMaxLimit))
	if got.EligibleRows != 2 || got.Counters.InvalidDeclarations != 5000 || got.Partial {
		t.Fatalf("invalid and missing summaries must stay fixed per scope: %d rows, %+v", got.EligibleRows, got.Counters)
	}
	for i := 0; i < 5000; i++ {
		o.observe("agent:a", "", []string{declare(i)}) // distinct valid descriptors from one principal
	}
	flood := view(t, o, "agent:a", "", intp(ClientsMaxLimit))
	if flood.EligibleRows != ObservationCohortsPerPrincipal || flood.Counters.EvictedCohorts != uint64(5002-ObservationCohortsPerPrincipal) || !flood.Partial || flood.Exhaustive {
		t.Fatalf("%d rows, %+v", flood.EligibleRows, flood.Counters)
	}
	o.mu.Lock()
	rows, counted, scopes := len(o.rows), len(o.count), len(o.counters)
	o.mu.Unlock()
	if rows > ObservationCohortsPerPrincipal || counted != 1 || scopes != 1 {
		t.Fatalf("unbounded bookkeeping: %d rows, %d principals counted, %d counter scopes", rows, counted, scopes)
	}
	// Listing neither erases gaps nor adds a cohort of its own.
	again := view(t, o, "agent:a", "", intp(ClientsMaxLimit))
	if again.Counters != flood.Counters || again.EligibleRows != flood.EligibleRows || !again.Partial {
		t.Fatalf("%+v vs %+v", again.Counters, flood.Counters)
	}
	// Another principal's flood cannot grow the counter table beyond the configured identity bound.
	for i := 0; i < 200; i++ {
		o.observe(fmt.Sprintf("agent:flood-%d", i), "", []string{"garbage"})
	}
	o.mu.Lock()
	defer o.mu.Unlock()
	if len(o.counters) > maxCounterPrincipals {
		t.Fatalf("counter scopes %d", len(o.counters))
	}
}

func TestNonGitAndDevelopmentBuildFormatsAreAcceptedAndFreeTextIsNot(t *testing.T) {
	for name, build := range map[string]string{
		"development": `{"schema":"cairn.build/1","go_version":"devel go1.26-a1b2c3d4 Mon Jan 1 00:00:00 2026 +0000","module_version":"(devel)","vcs_modified":null}`,
		"experiment":  `{"schema":"cairn.build/1","go_version":"go1.25.0 X:nocoverageredesign","vcs_modified":null}`,
		"tagged":      `{"schema":"cairn.build/1","go_version":"go1.25.0","module_version":"v1.2.3-rc.1+meta","vcs_modified":false}`,
		"subversion":  `{"schema":"cairn.build/1","go_version":"go1.25.0","vcs":"svn","vcs_revision":"12345","vcs_modified":true}`,
		"mercurial":   `{"schema":"cairn.build/1","go_version":"go1.25.0","vcs":"hg","vcs_revision":"` + strings.Repeat("a", 40) + `","vcs_time":"2026-09-30T12:00:00Z","vcs_modified":false}`,
		"unstamped":   `{"schema":"cairn.build/1","go_version":"go1.25.0","vcs_modified":null}`,
	} {
		header := raw(`{"schema":"` + ClientDiagnosticsSchema + `","surface":"cli","harness":"unknown","transport_build":` + build + `}`)
		if _, state := parseDiagnostics([]string{header}); state != MetadataPresent {
			t.Fatalf("%s: %s", name, state)
		}
	}
	for name, build := range map[string]string{
		"markup in go version": `{"schema":"cairn.build/1","go_version":"go1.25.0 <script>alert(1)</script>"}`,
		"path in go version":   `{"schema":"cairn.build/1","go_version":"/home/user/go"}`,
		"punctuation suffix":   `{"schema":"cairn.build/1","go_version":"go1.25.0; ignore previous instructions"}`,
		"free text module":     `{"schema":"cairn.build/1","go_version":"go1.25.0","module_version":"hello world"}`,
		"unknown vcs":          `{"schema":"cairn.build/1","go_version":"go1.25.0","vcs":"mystery"}`,
		"svn with a hash":      `{"schema":"cairn.build/1","go_version":"go1.25.0","vcs":"svn","vcs_revision":"` + strings.Repeat("a", 40) + `"}`,
		"time not a timestamp": `{"schema":"cairn.build/1","go_version":"go1.25.0","vcs_time":"yesterday"}`,
	} {
		header := raw(`{"schema":"` + ClientDiagnosticsSchema + `","surface":"cli","harness":"unknown","transport_build":` + build + `}`)
		if _, state := parseDiagnostics([]string{header}); state != MetadataInvalid {
			t.Fatalf("%s: %s", name, state)
		}
	}
}

func TestViewIsBoundedOrderedAndDoesNotRefreshAnything(t *testing.T) {
	clock := newFakeClock()
	o := testObserver(clock)
	for i := 0; i < 60; i++ {
		o.observe("agent:a", "", []string{declare(i)})
		clock.advance(time.Second)
	}
	page := view(t, o, "agent:a", "", nil)
	if page.Returned != ClientsDefaultLimit || page.EligibleRows != 60 || !page.Truncated || !page.Partial || len(page.Rows) != ClientsDefaultLimit {
		t.Fatalf("default page: %d/%d truncated=%v", page.Returned, page.EligibleRows, page.Truncated)
	}
	if page.Rows[0].Reported.TransportBuild.Revision != fmt.Sprintf("%040x", 59) || page.Rows[49].Reported.TransportBuild.Revision != fmt.Sprintf("%040x", 10) {
		t.Fatal("rows must be newest observation first")
	}
	before, _ := json.Marshal(view(t, o, "agent:a", "", intp(ClientsMaxLimit)).Rows)
	clock.advance(time.Hour)
	after, _ := json.Marshal(view(t, o, "agent:a", "", intp(ClientsMaxLimit)).Rows)
	if string(before) != string(after) {
		t.Fatal("listing refreshed an observation")
	}
	whole := view(t, o, "agent:a", "", intp(ClientsMaxLimit))
	if whole.Returned != 60 || whole.Truncated || whole.Partial {
		t.Fatalf("%+v", whole)
	}
	for _, limit := range []int{0, -1, ClientsMaxLimit + 1} {
		if _, err := o.view("agent:a", "", ClientsRequest{Limit: intp(limit)}); core.Code(err) != "INVALID_REQUEST" {
			t.Fatalf("limit %d: %v", limit, err)
		}
	}
	if got := view(t, o, "agent:a", "", intp(1)); got.Returned != 1 || !got.Truncated {
		t.Fatalf("%+v", got)
	}
}

func TestWallClockRegressionIsLabelledAndNeverOrdersOrExpiresRows(t *testing.T) {
	clock := newFakeClock()
	o := testObserver(clock)
	o.observe("agent:a", "", []string{declare(1)})
	o.observe("agent:a", "", []string{declare(2)})
	clock.advance(-48 * time.Hour) // the clock went backwards
	o.observe("agent:a", "", []string{declare(1)})
	got := view(t, o, "agent:a", "", nil)
	if !got.WallClockRegressed || got.EligibleRows != 2 {
		t.Fatalf("regression must be labelled and rows kept: %+v", got)
	}
	if got.Rows[0].Reported.TransportBuild.Revision != fmt.Sprintf("%040x", 1) {
		t.Fatal("order follows observation order, not the wall clock")
	}
	clean := newFakeClock()
	other := testObserver(clean)
	other.observe("agent:a", "", []string{declare(1)})
	if view(t, other, "agent:a", "", nil).WallClockRegressed {
		t.Fatal("a regression was claimed without one")
	}
}

func TestRealClockReadingsKeepMonotonicExpiry(t *testing.T) {
	base := time.Now() // carries a monotonic reading
	now := base
	o := newObserverWith(func() time.Time { return now }, func() string { return "id" })
	o.observe("agent:a", "", []string{declare(1)})
	now = base.Add(ObservationRetention + time.Second)
	if view(t, o, "agent:a", "", nil).EligibleRows != 0 {
		t.Fatal("monotonic age past the window must expire the row")
	}
}

func TestRestartStartsAnEmptyNewEpoch(t *testing.T) {
	first, second := &Server{}, &Server{}
	a, b := first.observation(), second.observation()
	a.observe("agent:a", "", []string{declare(1)})
	if view(t, a, "agent:a", "", nil).EligibleRows != 1 || view(t, b, "agent:a", "", nil).EligibleRows != 0 {
		t.Fatal("a new process must start empty")
	}
	one, two := view(t, a, "agent:a", "", nil), view(t, b, "agent:a", "", nil)
	if one.ObservationEpoch == "" || one.ObservationEpoch == two.ObservationEpoch || one.StartedAt.IsZero() || one.Storage != "volatile" {
		t.Fatalf("%+v %+v", one, two)
	}
}

func TestObserveAndViewAreRaceFreeAndStayWithinTheCaps(t *testing.T) {
	clock := newFakeClock()
	o := testObserver(clock)
	var group sync.WaitGroup
	for worker := 0; worker < 8; worker++ {
		group.Add(1)
		go func() {
			defer group.Done()
			for i := 0; i < 400; i++ {
				principal := fmt.Sprintf("agent:%d", (worker+i)%9)
				switch i % 4 {
				case 0:
					o.observe(principal, "", nil)
				case 1:
					o.observe(principal, "", []string{"garbage"})
				default:
					o.observe(principal, "", []string{declare(i)})
				}
				if i%25 == 0 {
					if _, err := o.view(principal, "", ClientsRequest{}); err != nil {
						t.Error(err)
					}
				}
			}
		}()
	}
	group.Wait()
	o.mu.Lock()
	defer o.mu.Unlock()
	if len(o.rows) > ObservationCohortsTotal {
		t.Fatalf("global cap exceeded: %d", len(o.rows))
	}
	for principal, count := range o.count {
		real := 0
		for key := range o.rows {
			if key.principal == principal {
				real++
			}
		}
		if count != real || count > ObservationCohortsPerPrincipal {
			t.Fatalf("%s: counted %d, actual %d", principal, count, real)
		}
	}
}

// ---- the HTTP boundary -------------------------------------------------------

const localToken, otherToken, remoteAgentToken, remoteObserverToken = "local-token", "other-token", "remote-agent-token", "remote-observer-token"

func observationServer(clock *fakeClock) *Server {
	key := func(token string) [32]byte { return sha256.Sum256([]byte(token)) }
	s := &Server{machines: map[string]string{}, clients: map[[32]byte]client{
		key(localToken):          {principal: "local-uid:1", role: "agent"},
		key(otherToken):          {principal: "local-uid:2", role: "agent"},
		key(remoteAgentToken):    {principal: "machine:m1/agent", machineID: "m1", remote: true, role: "agent"},
		key(remoteObserverToken): {principal: "machine:m1/observer", machineID: "m1", remote: true, role: "observer"},
	}}
	s.obs = testObserver(clock)
	return s
}

func post(handler http.Handler, path, token, body string, headers map[string][]string) *httptest.ResponseRecorder {
	request := httptest.NewRequest("POST", path, strings.NewReader(body))
	if token != "" {
		request.Header.Set("Authorization", "Bearer "+token)
	}
	for name, values := range headers {
		for _, value := range values {
			request.Header.Add(name, value)
		}
	}
	response := httptest.NewRecorder()
	handler.ServeHTTP(response, request)
	return response
}

func decodeClients(t *testing.T, response *httptest.ResponseRecorder) ClientsResponse {
	t.Helper()
	var reply struct {
		OK     bool            `json:"ok"`
		Status string          `json:"status"`
		Data   ClientsResponse `json:"data"`
	}
	if err := json.Unmarshal(response.Body.Bytes(), &reply); err != nil || response.Code != 200 || !reply.OK {
		t.Fatalf("%d %v: %s", response.Code, err, response.Body)
	}
	return reply.Data
}

func TestClientsRouteIsAuthenticatedReadOnlyAndPrincipalScoped(t *testing.T) {
	clock := newFakeClock()
	s := observationServer(clock)
	header := map[string][]string{ClientDiagnosticsHeader: {declare(1)}}
	post(s, "/v1/version", localToken, `{}`, header)
	post(s, "/v1/version", otherToken, `{}`, map[string][]string{ClientDiagnosticsHeader: {declare(2)}})

	if response := post(s, "/v1/clients", "", `{}`, nil); response.Code != 401 {
		t.Fatalf("unauthenticated: %d", response.Code)
	}
	if response := post(s, "/v1/clients", "wrong", `{}`, nil); response.Code != 401 {
		t.Fatalf("bad token: %d", response.Code)
	}
	request := httptest.NewRequest("GET", "/v1/clients", nil)
	request.Header.Set("Authorization", "Bearer "+localToken)
	if response := httptest.NewRecorder(); func() int { s.ServeHTTP(response, request); return response.Code }() != 405 {
		t.Fatal("clients must be POST")
	}
	mine := decodeClients(t, post(s, "/v1/clients", localToken, `{}`, nil))
	if mine.Schema != ClientsSchema || mine.Storage != "volatile" || mine.RetentionSeconds != 86400 || mine.Server.Schema != "cairn.build/1" || mine.EligibleRows != 1 ||
		mine.Rows[0].Principal != "local-uid:1" || mine.Rows[0].Reported.TransportBuild.Revision != fmt.Sprintf("%040x", 1) || mine.Rows[0].MetadataState != MetadataPresent {
		t.Fatalf("%+v", mine)
	}
	theirs := decodeClients(t, post(s, "/v1/clients", otherToken, `{}`, nil))
	if theirs.EligibleRows != 1 || theirs.Rows[0].Principal != "local-uid:2" || theirs.Rows[0].CohortID == mine.Rows[0].CohortID {
		t.Fatalf("%+v", theirs)
	}
}

func TestClientsRequestCannotSelectAnotherIdentityOrPage(t *testing.T) {
	s := observationServer(newFakeClock())
	post(s, "/v1/version", otherToken, `{}`, map[string][]string{ClientDiagnosticsHeader: {declare(2)}})
	for _, body := range []string{
		`{"principal":"local-uid:2"}`, `{"all":true}`, `{"machine_id":"m1"}`, `{"cursor":"x"}`, `{"after":"x"}`, `{"limit":0}`, `{"limit":129}`,
		`{"limit":-1}`, `{"limit":"5"}`, `{"limit":1.5}`, `[]`, `{"limit":5}{}`, `x`,
	} {
		response := post(s, "/v1/clients", localToken, body, nil)
		if response.Code != 400 || strings.Contains(response.Body.String(), "local-uid:2") {
			t.Fatalf("%s: %d %s", body, response.Code, response.Body)
		}
	}
	if got := decodeClients(t, post(s, "/v1/clients", localToken, `{"limit":3}`, nil)); got.EligibleRows != 0 {
		t.Fatalf("another principal's rows reached this caller: %+v", got)
	}
}

func TestInspectionAndUnauthenticatedTrafficAreNeverObserved(t *testing.T) {
	clock := newFakeClock()
	s := observationServer(clock)
	declared := map[string][]string{ClientDiagnosticsHeader: {declare(1)}}
	post(s, "/v1/clients", localToken, `{}`, declared) // inspection itself
	post(s, "/v1/version", "wrong", `{}`, declared)    // unauthenticated
	post(s, "/v1/version", "", `{}`, declared)         // no credentials
	post(s, "/v1/version", localToken, `{}`, nil)      // authenticated contact without a declaration
	got := decodeClients(t, post(s, "/v1/clients", localToken, `{}`, nil))
	if got.EligibleRows != 1 || got.Rows[0].MetadataState != MetadataMissing {
		t.Fatalf("only the authenticated version call counts: %+v", got)
	}
	first := got.Rows[0].LastObservedAt
	clock.advance(time.Hour)
	post(s, "/v1/clients", localToken, `{}`, declared)
	if again := decodeClients(t, post(s, "/v1/clients", localToken, `{}`, nil)); !again.Rows[0].LastObservedAt.Equal(first) {
		t.Fatal("polling the view refreshed an observation")
	}
}

func TestADeclarationNeverChangesHowARequestIsServed(t *testing.T) {
	good := declare(1)
	oversized := strings.Repeat("A", 8192)
	variants := map[string]map[string][]string{
		"none":       nil,
		"valid":      {ClientDiagnosticsHeader: {good}},
		"duplicate":  {ClientDiagnosticsHeader: {good, good}},
		"garbage":    {ClientDiagnosticsHeader: {"!!not-base64!!"}},
		"oversized":  {ClientDiagnosticsHeader: {oversized}},
		"newer":      {ClientDiagnosticsHeader: {raw(`{"schema":"cairn.client-diagnostics/2"}`)}},
		"other case": {"cairn-client-diagnostics": {good}},
	}
	reference := ""
	for name, headers := range variants {
		s := observationServer(newFakeClock())
		response := post(s, "/v1/version", localToken, `{}`, headers)
		body := response.Body.String()
		if response.Code != 200 {
			t.Fatalf("%s: %d %s", name, response.Code, body)
		}
		if reference == "" {
			reference = body
		} else if body != reference {
			t.Fatalf("%s changed the response:\n%s\n%s", name, body, reference)
		}
		// Refusals are unchanged too: protocol and body validation come after observation.
		for _, test := range []struct {
			path, body string
			headers    map[string][]string
			status     int
		}{
			{"/v1/version", `{"unknown":1}`, nil, 400},
			{"/v1/version", `{}`, map[string][]string{ProtocolHeader: {"99"}}, 426},
			{"/v1/version", `{}`, map[string][]string{ProtocolHeader: {"01"}}, 400},
		} {
			merged := map[string][]string{}
			for k, v := range headers {
				merged[k] = v
			}
			for k, v := range test.headers {
				merged[k] = v
			}
			refused := post(s, test.path, localToken, test.body, merged)
			plain := post(observationServer(newFakeClock()), test.path, localToken, test.body, test.headers)
			if refused.Code != test.status || refused.Code != plain.Code || refused.Body.String() != plain.Body.String() {
				t.Fatalf("%s %s: %d vs %d\n%s\n%s", name, test.body, refused.Code, plain.Code, refused.Body, plain.Body)
			}
		}
	}
}

func TestRefusedRequestsAreStillObservedAfterAuthentication(t *testing.T) {
	s := observationServer(newFakeClock())
	header := map[string][]string{ClientDiagnosticsHeader: {declare(1)}, ProtocolHeader: {"99"}}
	if response := post(s, "/v1/version", localToken, `{}`, header); response.Code != 426 {
		t.Fatalf("%d", response.Code)
	}
	if got := decodeClients(t, post(s, "/v1/clients", localToken, `{}`, nil)); got.EligibleRows != 1 || got.Rows[0].MetadataState != MetadataPresent {
		t.Fatalf("a protocol-refused request should still diagnose the client: %+v", got)
	}
}

func TestInvalidDeclarationsAreCountedWithoutEchoOrAdmissionEffects(t *testing.T) {
	s := observationServer(newFakeClock())
	canary := "TOKEN-canary-/home/halbritt/secret-prompt"
	for _, value := range []string{canary, raw(`{"schema":"` + canary + `"}`), strings.Repeat("Z", 5000)} {
		if response := post(s, "/v1/version", localToken, `{}`, map[string][]string{ClientDiagnosticsHeader: {value}}); response.Code != 200 {
			t.Fatalf("an invalid declaration changed admission: %d", response.Code)
		}
	}
	response := post(s, "/v1/clients", localToken, `{}`, nil)
	got := decodeClients(t, response)
	if got.Counters.InvalidDeclarations != 3 || got.EligibleRows != 1 || got.Rows[0].MetadataState != MetadataInvalid {
		t.Fatalf("%+v", got)
	}
	for _, secret := range []string{canary, "secret-prompt", localToken, "Bearer"} {
		if strings.Contains(response.Body.String(), secret) {
			t.Fatalf("diagnostic view leaked %q", secret)
		}
	}
}

func TestRemoteProfilesSeeOnlyTheirOwnViewAndObserversStayDenied(t *testing.T) {
	s := observationServer(newFakeClock())
	remote := s.RemoteHandler()
	header := map[string][]string{ClientDiagnosticsHeader: {declare(5)}}
	if response := post(remote, "/v1/version", remoteAgentToken, `{}`, header); response.Code != 200 {
		t.Fatalf("%d %s", response.Code, response.Body)
	}
	got := decodeClients(t, post(remote, "/v1/clients", remoteAgentToken, `{}`, nil))
	if got.EligibleRows != 1 || got.Rows[0].Principal != "machine:m1/agent" || got.Rows[0].MachineID != "m1" {
		t.Fatalf("%+v", got)
	}
	// The observer profile of the same machine keeps its existing denial and cannot see the agent's rows.
	for _, handler := range []http.Handler{remote, s} {
		response := post(handler, "/v1/clients", remoteObserverToken, `{}`, nil)
		if response.Code != 403 || !strings.Contains(response.Body.String(), "AUTHORITY_DENIED") || strings.Contains(response.Body.String(), "machine:m1/agent") {
			t.Fatalf("remote observer reached the view: %d %s", response.Code, response.Body)
		}
	}
	// A local profile reaching the network handler is refused as before, and sees no remote rows locally.
	if response := post(remote, "/v1/clients", localToken, `{}`, nil); response.Code != 403 {
		t.Fatalf("%d", response.Code)
	}
	if local := decodeClients(t, post(s, "/v1/clients", localToken, `{}`, nil)); local.EligibleRows != 0 {
		t.Fatalf("%+v", local)
	}
	// The remote agent calling through the private socket handler is the same principal and machine.
	if again := decodeClients(t, post(s, "/v1/clients", remoteAgentToken, `{}`, nil)); again.EligibleRows != 1 {
		t.Fatalf("%+v", again)
	}
}

func TestTwoPrincipalsOnTheSameMachineNeverSeeEachOthersCohorts(t *testing.T) {
	clock := newFakeClock()
	s := observationServer(clock)
	if err := s.SetLocalMachineID("central"); err != nil { // every local profile shares this machine
		t.Fatal(err)
	}
	forged := func(machine, principal string) map[string][]string {
		return map[string][]string{ClientDiagnosticsHeader: {raw(`{"schema":"` + ClientDiagnosticsSchema + `","surface":"cli","harness":"unknown","machine_id":"` + machine + `","principal":"` + principal + `","transport_build":` + validBuildJSON + `}`)}}
	}
	post(s, "/v1/version", localToken, `{}`, map[string][]string{ClientDiagnosticsHeader: {declare(1)}})
	post(s, "/v1/version", otherToken, `{}`, forged("central", "local-uid:1")) // forged labels must not select the other's scope
	one, two := decodeClients(t, post(s, "/v1/clients", localToken, `{}`, nil)), decodeClients(t, post(s, "/v1/clients", otherToken, `{}`, nil))
	if one.EligibleRows != 1 || two.EligibleRows != 1 || one.Rows[0].MachineID != "central" || two.Rows[0].MachineID != "central" {
		t.Fatalf("%+v %+v", one, two)
	}
	if one.Rows[0].Principal != "local-uid:1" || two.Rows[0].Principal != "local-uid:2" || one.Rows[0].CohortID == two.Rows[0].CohortID {
		t.Fatalf("a machine match must not union principals: %+v %+v", one.Rows[0], two.Rows[0])
	}
	// Two remote machines are isolated from each other and from the shared local machine.
	key := sha256.Sum256([]byte("remote-b-token"))
	s.clients[key] = client{principal: "machine:m2/agent", machineID: "m2", remote: true, role: "agent"}
	post(s.RemoteHandler(), "/v1/version", remoteAgentToken, `{}`, forged("m2", "machine:m2/agent"))
	post(s.RemoteHandler(), "/v1/version", "remote-b-token", `{}`, map[string][]string{ClientDiagnosticsHeader: {declare(9)}})
	a, b := decodeClients(t, post(s.RemoteHandler(), "/v1/clients", remoteAgentToken, `{}`, nil)), decodeClients(t, post(s.RemoteHandler(), "/v1/clients", "remote-b-token", `{}`, nil))
	if a.EligibleRows != 1 || b.EligibleRows != 1 || a.Rows[0].Principal != "machine:m1/agent" || b.Rows[0].Principal != "machine:m2/agent" || b.Rows[0].Reported.TransportBuild.Revision != fmt.Sprintf("%040x", 9) {
		t.Fatalf("%+v %+v", a, b)
	}
}

func TestSessionSelectorsAndDeclaredLabelsNeverChangeTheObservationScope(t *testing.T) {
	s := observationServer(newFakeClock())
	session := map[string][]string{
		"Cairn-Agent-ID": {"11111111-1111-4111-8111-111111111111"}, "Cairn-Execution-ID": {"22222222-2222-4222-8222-222222222222"},
		ClientDiagnosticsHeader: {declare(3)},
	}
	// Session directory operations refuse session headers before any store use; the contact is still the
	// authenticated principal's, whatever session the caller names.
	if response := post(s, "/v1/agent-directory", localToken, `{}`, session); response.Code != 400 {
		t.Fatalf("%d %s", response.Code, response.Body)
	}
	if got := decodeClients(t, post(s, "/v1/clients", localToken, `{}`, nil)); got.EligibleRows != 1 || got.Rows[0].Principal != "local-uid:1" {
		t.Fatalf("%+v", got)
	}
	if got := decodeClients(t, post(s, "/v1/clients", otherToken, `{}`, nil)); got.EligibleRows != 0 {
		t.Fatalf("a named session selected another scope: %+v", got)
	}
}

func TestMachineBindingComesFromProvisioningNotFromTheDeclaration(t *testing.T) {
	s := observationServer(newFakeClock())
	forged := raw(`{"schema":"` + ClientDiagnosticsSchema + `","surface":"cli","harness":"unknown","machine_id":"m1","principal":"machine:m1/agent","transport_build":` + validBuildJSON + `}`)
	post(s, "/v1/version", localToken, `{}`, map[string][]string{ClientDiagnosticsHeader: {forged}})
	if got := decodeClients(t, post(s, "/v1/clients", remoteAgentToken, `{}`, nil)); got.EligibleRows != 0 {
		t.Fatalf("a declared machine or principal selected another view: %+v", got)
	}
	mine := decodeClients(t, post(s, "/v1/clients", localToken, `{}`, nil))
	if mine.EligibleRows != 1 || mine.Rows[0].Principal != "local-uid:1" || mine.Rows[0].MachineID != "" {
		t.Fatalf("%+v", mine)
	}
	encoded, _ := json.Marshal(mine)
	if strings.Contains(string(encoded), "machine:m1") {
		t.Fatal("forged labels were echoed")
	}
}

// ---- the relay -----------------------------------------------------------------

func TestRelayForwardsOneBoundedDeclarationVerbatimAndDropsTheRestWithoutRefusing(t *testing.T) {
	type seen struct {
		values []string
		body   string
		relay  string
	}
	var last seen
	var attempts atomic.Int32
	upstream := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		attempts.Add(1)
		body := new(strings.Builder)
		buffer := make([]byte, 512)
		for {
			n, err := r.Body.Read(buffer)
			body.Write(buffer[:n])
			if err != nil {
				break
			}
		}
		last = seen{values: r.Header.Values(ClientDiagnosticsHeader), body: body.String(), relay: r.Header.Get(RelayProtocolHeader)}
		_, _ = w.Write([]byte(`{"schema":"cairn.response/1","ok":true,"status":"OK","data":{}}`))
	}))
	defer upstream.Close()
	relay := trustedRelay(t, upstream)
	good := declare(1)
	for name, test := range map[string]struct {
		values []string
		want   []string
	}{
		"single":    {[]string{good}, []string{good}},
		"absent":    {nil, nil},
		"duplicate": {[]string{good, good}, nil},
		"oversized": {[]string{strings.Repeat("A", maxDiagnosticsEncoded+1)}, nil},
		"at limit":  {[]string{strings.Repeat("A", maxDiagnosticsEncoded)}, []string{strings.Repeat("A", maxDiagnosticsEncoded)}},
	} {
		t.Run(name, func(t *testing.T) {
			before := attempts.Load()
			response := post(relay, "/v1/version", "token", `{"request_id":"fixed","x":[1,2]}`, map[string][]string{ClientDiagnosticsHeader: test.values})
			if response.Code != 200 {
				t.Fatalf("a diagnostic must never make the relay refuse: %d %s", response.Code, response.Body)
			}
			if attempts.Load() != before+1 {
				t.Fatalf("exactly one upstream business attempt expected, got %d", attempts.Load()-before)
			}
			if strings.Contains(response.Body.String(), "AAAA") || strings.Contains(response.Header().Get("Content-Type")+response.Body.String(), "Cairn-Client-Diagnostics") {
				t.Fatal("header text leaked into the reply")
			}
			if strings.Join(last.values, "|") != strings.Join(test.want, "|") {
				t.Fatalf("forwarded %v want %v", last.values, test.want)
			}
			if last.body != `{"request_id":"fixed","x":[1,2]}` || last.relay == "" {
				t.Fatalf("body or relay protocol changed: %q %q", last.body, last.relay)
			}
		})
	}
}

// Sampling and updating must share one ordering: overlapping authenticated
// contacts must not manufacture clock regression or expire a newer contact.
func TestConcurrentContactsKeepLatestObservation(t *testing.T) {
	base := time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC)
	sampled, release := make(chan struct{}), make(chan struct{})
	firstDone, secondDone := make(chan struct{}), make(chan struct{})
	var calls atomic.Int32
	o := newObserverWith(func() time.Time {
		switch calls.Add(1) {
		case 1:
			return base
		case 2:
			close(sampled)
			<-release
			return base
		default:
			return base.Add(time.Hour)
		}
	}, func() string { return "cohort" })
	go func() { defer close(firstDone); o.observe("agent:a", "", nil) }()
	<-sampled
	go func() { defer close(secondDone); o.observe("agent:a", "", nil) }()
	// Release independently of the second completion: a correct implementation
	// holds the observation lock during the first clock sample.
	select {
	case <-secondDone:
	case <-time.After(100 * time.Millisecond):
	}
	close(release)
	<-firstDone
	<-secondDone
	got := view(t, o, "agent:a", "", nil)
	if len(got.Rows) != 1 {
		t.Fatalf("rows: %+v", got)
	}
	row := got.Rows[0]
	if !row.FirstObservedAt.Equal(base) || !row.LastObservedAt.Equal(base.Add(time.Hour)) || got.WallClockRegressed {
		t.Fatalf("ordered contacts lost: first=%s last=%s regressed=%t", row.FirstObservedAt, row.LastObservedAt, got.WallClockRegressed)
	}
	// A view just before the latest contact's retention boundary must retain it.
	o.now = func() time.Time { return base.Add(time.Hour + ObservationRetention - time.Second) }
	if got := view(t, o, "agent:a", "", nil); len(got.Rows) != 1 {
		t.Fatal("latest contact expired early")
	}
}

func TestViewSamplesTimeWithItsSnapshot(t *testing.T) {
	clock := newFakeClock()
	o := testObserver(clock)
	o.observe("agent:a", "", nil)
	// A snapshot timestamp must be sampled while the same mutex protects rows;
	// otherwise a later contact can enter the returned snapshot after sampling.
	o.now = func() time.Time {
		if o.mu.TryLock() {
			o.mu.Unlock()
			t.Error("snapshot clock sampled outside observation lock")
		}
		return clock.Now()
	}
	got := view(t, o, "agent:a", "", nil)
	if len(got.Rows) != 1 || got.ObservedAt.Before(got.Rows[0].LastObservedAt) {
		t.Fatalf("snapshot predates included contact: %+v", got)
	}
}

func TestRelayStillRejectsDuplicateAuthenticationAndProtocolHeaders(t *testing.T) {
	var attempts atomic.Int32
	upstream := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { attempts.Add(1) }))
	defer upstream.Close()
	relay := trustedRelay(t, upstream)
	for _, headers := range []map[string][]string{
		{ProtocolHeader: {"2", "2"}},
		{"Authorization": {"Bearer a", "Bearer b"}},
		{"Cairn-Agent-ID": {"x", "y"}},
		{ProtocolHeader: {"2", "2"}, ClientDiagnosticsHeader: {declare(1)}}, // a valid declaration does not excuse them
	} {
		response := post(relay, "/v1/version", "token", `{}`, headers)
		if response.Code != 400 || !strings.Contains(response.Body.String(), "duplicate protocol header") {
			t.Fatalf("%v: %d %s", headers, response.Code, response.Body)
		}
	}
	if attempts.Load() != 0 {
		t.Fatal("a refused request reached the upstream")
	}
}

func TestClientsWireExplicitlyDeniesExhaustivenessWithoutKnownGaps(t *testing.T) {
	s := observationServer(newFakeClock())
	for _, contacts := range []int{0, 1} {
		if contacts == 1 {
			post(s, "/v1/version", localToken, `{}`, map[string][]string{ClientDiagnosticsHeader: {declare(1)}})
		}
		response := post(s, "/v1/clients", localToken, `{}`, nil)
		got := decodeClients(t, response)
		if got.Partial || got.Truncated || got.EligibleRows != contacts {
			t.Fatalf("unexpected known gaps or rows: %+v", got)
		}
		var wire struct {
			Data map[string]json.RawMessage `json:"data"`
		}
		if err := json.Unmarshal(response.Body.Bytes(), &wire); err != nil {
			t.Fatal(err)
		}
		if value, present := wire.Data["exhaustive"]; !present || string(value) != "false" {
			t.Fatalf("%d contacts: wire must explicitly deny exhaustive inventory: %s", contacts, response.Body)
		}
	}
}
