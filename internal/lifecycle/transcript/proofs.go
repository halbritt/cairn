package transcript

import (
	"bytes"
	"encoding/json"
	"math/big"
	"regexp"
	"strconv"
	"strings"

	"github.com/halbritt/cairn/internal/lifecycle/value"
	"github.com/halbritt/cairn/internal/lifecycle/wake"
)

func exactOrdinal(v any) (*big.Int, bool) {
	var token string
	switch n := v.(type) {
	case *Record:
		if n == nil || n.Kind != value.Int {
			return nil, false
		}
		token = n.Integer
	case json.Number:
		token = string(n)
	case int:
		token = strconv.Itoa(n)
	case int64:
		token = strconv.FormatInt(n, 10)
	default:
		return nil, false
	}
	if strings.ContainsAny(token, ".eE") {
		return nil, false
	}
	n, ok := new(big.Int).SetString(token, 10)
	return n, ok
}
func recordOrdinal(record *Record, ordinal *big.Int, index int, metadata bool) bool {
	n, ok := exactOrdinal(field(record, "ordinal"))
	shape := keys(record, "timestamp", "ordinal", "type", "payload")
	if metadata {
		shape = shape || keys(record, "timestamp", "ordinal", "type", "payload", "metadata")
	}
	return ordinal != nil && shape && ok && n.Cmp(new(big.Int).Add(ordinal, big.NewInt(int64(index)))) == 0 && object(field(record, "payload")) != nil
}
func recordOrdinals(records []*Record, metadata, nonnegative bool) bool {
	if len(records) == 0 {
		return false
	}
	ordinal, ok := exactOrdinal(field(records[0], "ordinal"))
	if !ok || nonnegative && ordinal.Sign() < 0 {
		return false
	}
	for i, record := range records {
		if !recordOrdinal(record, ordinal, i, metadata) {
			return false
		}
	}
	return true
}
func recordTimes(records []*Record) ([]float64, error) {
	times := make([]float64, len(records))
	for i, record := range records {
		t, e := NativeTime(field(record, "timestamp"))
		if e != nil {
			return nil, e
		}
		times[i] = epoch(t)
	}
	return times, nil
}
func proof(schema string, state SessionState, ls []Locator) map[string]any {
	return map[string]any{"schema": schema, "native_session_id": state.NativeSessionID, "records": ls}
}
func hasText(v *Record, key, want string) bool { return stringIs(field(v, key), want) }

func CodexRefusalProof(cfg CodexConfig, state SessionState, attempt Attempt, recorded map[string]any, ls []Locator, profile *value.SourceReaderProfile) (map[string]any, error) {
	return NewReader(profile).CodexRefusalProof(cfg, state, attempt, recorded, ls)
}
func (r Reader) CodexRefusalProof(cfg CodexConfig, state SessionState, attempt Attempt, recorded map[string]any, ls []Locator) (map[string]any, error) {
	if len(ls) != 3 || !contiguous(ls) {
		return nil, invalid("Codex refusal records are not contiguous")
	}
	p, before, records, e := r.records(cfg, state, ls)
	if e != nil {
		return nil, e
	}
	turn := attempt.NativeTurnID
	ordinal, ok := exactOrdinal(field(records[0], "ordinal"))
	if !ok || ordinal.Sign() < 0 {
		return nil, invalid("Codex record ordinal is unavailable")
	}
	if !recordOrdinals(records, false, true) {
		return nil, invalid("Codex native records do not identify the refused root turn")
	}
	for i, kind := range []string{"event_msg", "turn_context", "event_msg"} {
		payload := object(field(records[i], "payload"))
		if !hasText(records[i], "type", kind) || !hasText(payload, "turn_id", turn) || !hasText(payload, "root_turn_id", turn) {
			return nil, invalid("Codex native records do not identify the refused root turn")
		}
	}
	first, middle, last := object(field(records[0], "payload")), object(field(records[1], "payload")), object(field(records[2], "payload"))
	attr := object(field(first, "turn_attribution"))
	if !keys(first, "type", "turn_attribution", "turn_id", "root_turn_id", "started_at", "model_context_window", "collaboration_mode_kind") || !hasText(first, "type", "task_started") || !keys(attr, "turn_id", "turn_trigger", "parent_turn_id", "initiating_agent_path", "root_turn_id") || !hasText(attr, "turn_id", turn) || !hasText(attr, "turn_trigger", "queue") || !isNull(field(attr, "parent_turn_id")) || !isNull(field(attr, "initiating_agent_path")) || !hasText(attr, "root_turn_id", turn) || !keys(last, "type", "turn_id", "root_turn_id", "last_agent_message", "started_at", "completed_at", "duration_ms") || !hasText(last, "type", "task_complete") || !isNull(field(last, "last_agent_message")) || !hasText(middle, "cwd", state.Workspace) {
		return nil, invalid("Codex records do not prove an empty completed queue turn")
	}
	times, e := recordTimes(records)
	if e != nil {
		return nil, e
	}
	claimed, e := claimTime(attempt.CreatedAt)
	if e != nil {
		return nil, e
	}
	refused, numeric := numberSeconds(recorded["at"], false)
	started, si := exactInt(field(first, "started_at"))
	completed, ci := exactInt(field(last, "completed_at"))
	duration, di := exactInt(field(last, "duration_ms"))
	if !numeric || !(times[0] <= times[1] && times[1] <= claimed && claimed <= refused && refused <= times[2]) || times[2]-times[0] > NativeHookTimeoutSeconds || !si || started != int64(times[0]) || !sameRecord(field(last, "started_at"), field(first, "started_at")) || !ci || completed != int64(times[2]) || !di || duration < 0 || duration > NativeHookTimeoutSeconds*1000 {
		return nil, invalid("Codex refusal chronology does not bracket this claim")
	}
	if e = r.recheck(cfg, state, p, before); e != nil {
		return nil, e
	}
	result := proof("cairn.codex-refused-turn/1", state, ls)
	result["native_turn_id"] = turn
	return result, nil
}

var codexErrorPattern = regexp.MustCompile(`^[a-z][a-z0-9_]{0,63}$`)

func CodexEndedTurnProof(cfg CodexConfig, state SessionState, attempt Attempt, ls []Locator, profile *value.SourceReaderProfile) (map[string]any, error) {
	return NewReader(profile).CodexEndedTurnProof(cfg, state, attempt, ls)
}
func (r Reader) CodexEndedTurnProof(cfg CodexConfig, state SessionState, attempt Attempt, ls []Locator) (map[string]any, error) {
	if len(ls) != 2 || !validLocator(ls[0]) || !validLocator(ls[1]) || ls[1].Offset < ls[0].Offset+ls[0].Length {
		return nil, invalid("Codex end record does not follow its start record")
	}
	begin, stop := ls[0].Offset, ls[1].Offset+ls[1].Length
	p, before, pins, e := r.records(cfg, state, ls)
	if e != nil {
		return nil, e
	}
	if stop-begin > CodexEndedTurnRegionLimit {
		return nil, invalid("Codex ended turn span exceeds its read limit")
	}
	f, info, e := openOwner(p, r.ExpectedUID)
	if e != nil {
		return nil, invalid("Codex rollout is not an owner-controlled regular file", e)
	}
	defer f.Close()
	if info.Size < stop {
		return nil, invalid("Codex ended turn span changed while reading")
	}
	if info.Size-stop > CodexEndedTurnTailLimit {
		return nil, invalid("Codex rollout tail exceeds its read limit")
	}
	region, e := readAt(f, begin, stop-begin)
	if e != nil || !bytes.HasSuffix(region, []byte{'\n'}) {
		return nil, invalid("Codex ended turn span changed while reading", e)
	}
	tail, e := readAt(f, stop, info.Size-stop)
	if e != nil {
		return nil, e
	}
	records := []*Record{}
	for _, line := range bytes.Split(region[:len(region)-1], []byte{'\n'}) {
		record, e := StrictNativeLine(line, r.Profile)
		if e != nil {
			return nil, e
		}
		records = append(records, record)
	}
	if len(records) < 2 || !sameRecord(records[0], pins[0]) || !sameRecord(records[len(records)-1], pins[1]) {
		return nil, invalid("Codex ended turn span does not start and end at its pins")
	}
	turn := attempt.NativeTurnID
	ordinal, _ := exactOrdinal(field(records[0], "ordinal"))
	for i, record := range records {
		payload := object(field(record, "payload"))
		id := field(payload, "turn_id")
		if !recordOrdinal(record, ordinal, i, true) || id != nil && !stringIs(id, turn) {
			return nil, invalid("Codex ended turn span is not one contiguous turn")
		}
		kind, inner := text(field(record, "type")), text(field(payload, "type"))
		edge := i == 0 || i == len(records)-1
		allowed := kind == "event_msg" && edge && (inner == "task_started" || inner == "task_complete") || kind == "event_msg" && !edge && inner == "token_count" || kind == "event_msg" && !edge && inner == "item_completed" && hasText(object(field(payload, "item")), "type", "UserMessage") || (kind == "turn_context" || kind == "world_state") && !edge || kind == "response_item" && !edge && inner == "message" && (hasText(payload, "role", "user") || hasText(payload, "role", "developer"))
		if !allowed {
			return nil, invalid("Codex turn shows tool, assistant or unknown activity")
		}
	}
	first, last := records[0], records[len(records)-1]
	started, finished := object(field(first, "payload")), object(field(last, "payload"))
	attr, providerError := object(field(started, "turn_attribution")), object(field(finished, "error"))
	if !hasText(first, "type", "event_msg") || !hasText(started, "type", "task_started") || !hasText(started, "turn_id", turn) || !hasText(started, "root_turn_id", turn) || attr == nil || !hasText(attr, "turn_id", turn) || !hasText(attr, "turn_trigger", "queue") || !hasText(last, "type", "event_msg") || !hasText(finished, "type", "task_complete") || !hasText(finished, "turn_id", turn) || !hasText(finished, "root_turn_id", turn) || !isNull(field(finished, "last_agent_message")) || providerError == nil || !codexErrorPattern.MatchString(text(field(providerError, "codex_error_info"))) {
		return nil, invalid("Codex records do not prove a queue turn ended on a provider error")
	}
	if bytes.Contains(tail, []byte(turn)) {
		return nil, invalid("Codex rollout records this turn again after its end")
	}
	claimed, e := claimTime(attempt.CreatedAt)
	if e != nil {
		return nil, e
	}
	a, e := NativeTime(field(first, "timestamp"))
	if e != nil {
		return nil, e
	}
	b, e := NativeTime(field(last, "timestamp"))
	if e != nil {
		return nil, e
	}
	if !(epoch(a) <= claimed && claimed <= epoch(b)) {
		return nil, invalid("Codex turn chronology does not bracket this claim")
	}
	if e = r.recheck(cfg, state, p, before); e != nil {
		return nil, e
	}
	result := proof("cairn.codex-ended-turn/1", state, ls)
	result["native_turn_id"] = turn
	result["codex_error_info"] = field(providerError, "codex_error_info")
	result["ended_at"] = field(last, "timestamp")
	return result, nil
}

func CodexQueueRolloutProof(cfg CodexConfig, state SessionState, w Wake, ls []Locator, profile *value.SourceReaderProfile) (map[string]any, error) {
	return NewReader(profile).CodexQueueRolloutProof(cfg, state, w, ls)
}
func (r Reader) CodexQueueRolloutProof(cfg CodexConfig, state SessionState, w Wake, ls []Locator) (map[string]any, error) {
	if len(ls) == 0 {
		return nil, invalid("Codex rollout proof requires at least one record locator")
	}
	if !contiguous(ls) {
		return nil, invalid("Codex rollout records are not contiguous")
	}
	p, before, records, e := r.records(cfg, state, ls)
	if e != nil {
		return nil, e
	}
	ordinal, ok := exactOrdinal(field(records[0], "ordinal"))
	if !ok || ordinal.Sign() < 0 {
		return nil, invalid("Codex record ordinal is unavailable")
	}
	if !recordOrdinals(records, false, true) {
		return nil, invalid("Codex rollout record shape is invalid")
	}
	times, e := recordTimes(records)
	if e != nil {
		return nil, e
	}
	for i := 1; i < len(times); i++ {
		if times[i-1] > times[i] {
			return nil, invalid("Codex rollout record timestamps are not monotonic")
		}
	}
	attempted, ok := numberSeconds(w.AttemptedAt, true)
	if !ok {
		return nil, invalid("retained wake has no attempt timestamp")
	}
	if e = r.recheck(cfg, state, p, before); e != nil {
		return nil, e
	}
	result := proof("cairn.codex-queue-rollout/1", state, ls)
	if len(records) == 3 && hasText(records[0], "type", "event_msg") && hasText(records[1], "type", "turn_context") && hasText(records[2], "type", "event_msg") {
		first, middle, last := object(field(records[0], "payload")), object(field(records[1], "payload")), object(field(records[2], "payload"))
		turn := field(first, "turn_id")
		attr := object(field(first, "turn_attribution"))
		if nonemptyString(turn) && hasText(first, "type", "task_started") && hasText(attr, "turn_trigger", "queue") && sameRecord(field(middle, "turn_id"), turn) && hasText(middle, "cwd", state.Workspace) && sameRecord(field(last, "turn_id"), turn) && times[0] >= attempted {
			if hasText(last, "type", "task_complete") && nonblankString(field(last, "last_agent_message")) {
				result["outcome"] = "completed"
				result["native_turn_id"] = turn
				return result, nil
			}
			if hasText(last, "type", "task_complete") && isNull(field(last, "last_agent_message")) || hasText(last, "type", "turn_aborted") {
				result["outcome"] = "cancelled"
				result["native_turn_id"] = turn
				return result, nil
			}
		}
	}
	first, last := object(field(records[0], "payload")), object(field(records[len(records)-1], "payload"))
	active := hasText(first, "type", "task_started") && hasText(object(field(first, "turn_attribution")), "turn_trigger", "queue") && times[0] >= attempted
	for _, record := range records {
		payload := object(field(record, "payload"))
		if hasText(payload, "type", "task_complete") || hasText(payload, "type", "turn_aborted") {
			active = false
		}
	}
	if active {
		result["outcome"] = "admitted_active"
		result["native_turn_id"] = field(first, "turn_id")
		return result, nil
	}
	markers, e := r.queueMarkerPresent(records, w)
	if e != nil {
		return nil, e
	}
	queueTurn := false
	for i, record := range records {
		payload := object(field(record, "payload"))
		if hasText(payload, "type", "task_started") && hasText(object(field(payload, "turn_attribution")), "turn_trigger", "queue") && times[i] >= attempted {
			queueTurn = true
		}
	}
	if ls[len(ls)-1].Offset+ls[len(ls)-1].Length == before.Size && times[0] <= attempted && attempted <= times[len(times)-1] && !queueTurn && !markers && hasText(last, "type", "task_complete") {
		result["outcome"] = "never_admitted"
		return result, nil
	}
	return nil, invalid("Codex rollout records do not prove a supported queue outcome")
}
func (r Reader) queueMarkerPresent(records []*Record, w Wake) (bool, error) {
	var serialized bytes.Buffer
	if e := r.Profile.Require(SiteQueueMarker); e != nil {
		return false, e
	}
	route, ok := r.Profile.Definition.Writers[SiteQueueMarker]
	if !ok || route.Route != "dumps" || route.Compact == nil || *route.Compact || route.EnsureASCII == nil || !*route.EnsureASCII || route.TrailingLF == nil || *route.TrailingLF {
		return false, &value.ProfileUnavailable{}
	}
	if e := r.Profile.Write(SiteQueueMarker, &serialized, &value.Value{Kind: value.Array, Items: records}); e != nil {
		return false, e
	}
	markers := []string{w.DeliveryID, w.QueuedSubmissionID}
	messages := wake.WakeMessages(w.Session, w.DeliveryID)
	markers = append(markers, messages[:]...)
	for _, marker := range markers {
		if marker != "" && bytes.Contains(serialized.Bytes(), []byte(marker)) {
			return true, nil
		}
	}
	return false, nil
}
