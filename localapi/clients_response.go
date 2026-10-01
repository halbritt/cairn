package localapi

import (
	"encoding/base64"
	"encoding/json"
	"strings"
	"time"
	"unicode"
	"unicode/utf8"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
	"github.com/halbritt/cairn/internal/jsontext"
)

// ParseClientsResponse validates peer diagnostics before a facade exposes them.
// Unknown additive fields are discarded by typed projection; unknown schemas,
// unsafe known values, and inconsistent accounting are refused without echoing.
func ParseClientsResponse(raw []byte, limit int) (ClientsResponse, error) {
	invalid := func() (ClientsResponse, error) {
		return ClientsResponse{}, &core.Error{Code: "INVALID_DIAGNOSTICS", Message: "API client observations are invalid or unsupported; observed clients are unknown"}
	}
	if len(raw) > 512*1024 || jsontext.CheckUnicode(raw) != nil || checkBoundedJSON(raw) != nil || limit < 1 || limit > ClientsMaxLimit {
		return invalid()
	}
	var fields map[string]json.RawMessage
	if json.Unmarshal(raw, &fields) != nil {
		return invalid()
	}
	for _, key := range []string{"schema", "storage", "observation_epoch", "started_at", "observed_at", "retention_seconds", "server", "exhaustive", "partial", "truncated", "wall_clock_regressed", "returned", "eligible_rows", "counters", "rows"} {
		if value, ok := fields[key]; !ok || string(value) == "null" {
			return invalid()
		}
	}
	var counters map[string]json.RawMessage
	if json.Unmarshal(fields["counters"], &counters) != nil {
		return invalid()
	}
	for _, key := range []string{"evicted_cohorts", "invalid_declarations"} {
		if value, ok := counters[key]; !ok || string(value) == "null" {
			return invalid()
		}
	}
	var view ClientsResponse
	if json.Unmarshal(raw, &view) != nil || view.Schema != ClientsSchema || view.Storage != "volatile" || !canonicalClientUUID(view.ObservationEpoch) || view.StartedAt.IsZero() || view.ObservedAt.IsZero() || view.RetentionSeconds != int(ObservationRetention/time.Second) || !view.Server.Valid() || view.Exhaustive {
		return invalid()
	}
	if view.Returned != len(view.Rows) || view.Returned > limit || view.EligibleRows < view.Returned || view.EligibleRows > ObservationCohortsPerPrincipal || view.Truncated != (view.EligibleRows > view.Returned) || view.Partial != (view.Truncated || view.Counters.EvictedCohorts > 0) || view.Counters.EvictedCohorts > counterCeiling || view.Counters.InvalidDeclarations > counterCeiling {
		return invalid()
	}
	var wire struct {
		Rows []struct {
			Reported json.RawMessage `json:"reported"`
		} `json:"rows"`
	}
	if json.Unmarshal(raw, &wire) != nil {
		return invalid()
	}
	seen := map[string]bool{}
	for i := range view.Rows {
		row := &view.Rows[i]
		if !canonicalClientUUID(row.CohortID) || seen[row.CohortID] || !safeClientPrincipal(row.Principal) || (row.MachineID != "" && !machineIDPattern.MatchString(row.MachineID)) || row.FirstObservedAt.IsZero() || row.LastObservedAt.IsZero() {
			return invalid()
		}
		seen[row.CohortID] = true
		if i > 0 && (row.Principal != view.Rows[0].Principal || row.MachineID != view.Rows[0].MachineID) {
			return invalid()
		}
		switch row.MetadataState {
		case MetadataPresent:
			if row.Reported == nil || row.Reported.Schema != ClientDiagnosticsSchema || !diagnosticSurfaces[row.Reported.Surface] || !diagnosticHarnesses[row.Reported.Harness] {
				return invalid()
			}
			descriptor, state := parseDiagnostics([]string{base64.RawURLEncoding.EncodeToString(wire.Rows[i].Reported)})
			if state != MetadataPresent {
				return invalid()
			}
			row.Reported = &descriptor
			origin := "unknown"
			if descriptor.Origin != nil {
				origin = "reported"
			}
			if row.OriginState != origin {
				return invalid()
			}
		case MetadataMissing, MetadataInvalid, MetadataUnrecognized:
			if row.Reported != nil || row.OriginState != "unknown" {
				return invalid()
			}
		default:
			return invalid()
		}
	}
	return view, nil
}

func canonicalClientUUID(value string) bool {
	id, err := uuid.Parse(value)
	return err == nil && id.String() == value
}

// Preserve the provisioning label contract while refusing diagnostic control text.
func safeClientPrincipal(value string) bool {
	if len(value) > 256 || strings.TrimSpace(value) == "" || !utf8.ValidString(value) {
		return false
	}
	for _, r := range value {
		if unicode.IsControl(r) {
			return false
		}
	}
	return true
}
