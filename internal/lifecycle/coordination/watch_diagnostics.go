package coordination

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"syscall"
	"time"

	"github.com/halbritt/cairn/internal/lifecycle/journal"
	"github.com/halbritt/cairn/internal/lifecycle/value"
)

const (
	codexErrorWakeMinDelay = 300
	codexErrorWakeMaxDelay = 3600
	agySubagentQuietSecs   = 3600
	siteLastJSONLRecord    = "transcript.last_jsonl_record"
)

var (
	codexErrorIdentifierRegex   = regexp.MustCompile(`^[a-z][a-z0-9_]{0,63}$`)
	codexTurnEndTimestampRegex  = regexp.MustCompile(`^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$`)
	codexTransientErrorKindsSet = map[string]bool{
		"server_overloaded":                 true,
		"internal_server_error":             true,
		"http_connection_failed":            true,
		"response_stream_connection_failed": true,
		"response_stream_disconnected":      true,
		"response_too_many_failed_attempts": true,
	}
)

func codexErrorClass(info *value.Value) (string, string) {
	var detail *value.Value
	if info != nil && info.Kind == value.Object && len(info.Members) == 1 {
		detail = info.Members[0].Value
		info = value.Text(string(info.Members[0].Key))
	}
	name := nativeText(info)
	if info == nil || info.Kind != value.String || !codexErrorIdentifierRegex.MatchString(name) {
		return "persistent", "unrecognized"
	}
	if !codexTransientErrorKindsSet[name] {
		return "persistent", name
	}
	if name == "http_connection_failed" && detail != nil && detail.Kind != value.Null {
		status := detail
		if detail.Kind == value.Object {
			status = detail.Get("http_status_code")
		}
		if status != nil && status.Kind != value.Null {
			if status.Kind == value.Int {
				if code, err := strconv.ParseInt(status.Integer, 10, 64); err == nil && code >= 500 && code < 600 {
					return "transient", name
				}
				return "persistent", fmt.Sprintf("http_connection_failed_%s", status.Integer)
			}
			return "persistent", "http_connection_failed"
		}
	}
	return "transient", name
}

func (c *CLI) latestCodexTurnEnd(ctx context.Context, config, state *value.Value) (*value.Value, *value.Value, error) {
	eng := c.engine()
	if eng == nil || eng.Evidence == nil {
		return nil, nil, &CoordinationError{Code: "INVALID_REQUEST", Message: "codex rollout evidence is not configured"}
	}
	res, err := eng.Evidence.Read(ctx, EvidenceCodexLatest, EvidenceRequest{Config: config, State: state})
	if err != nil || res == nil {
		return nil, nil, err
	}
	if res.Kind != value.Array || len(res.Items) != 2 {
		return nil, nil, &CoordinationError{Code: "INVALID_REQUEST", Message: "invalid codex latest rollout response"}
	}
	return res.Items[0], res.Items[1], nil
}

func codexErrorRolloutReason(err error) (string, bool) {
	var ce *CoordinationError
	if errors.As(err, &ce) {
		return fmt.Sprintf("rollout unavailable (%s)", ce.Code), true
	}
	var je *journal.Error
	if errors.As(err, &je) {
		return fmt.Sprintf("rollout unavailable (%s)", je.Code), true
	}
	if strings.HasPrefix(err.Error(), "INVALID_REQUEST: ") {
		return "rollout unavailable (INVALID_REQUEST)", true
	}
	if sourceOSError(err) || isOSPathErr(err) {
		return "rollout unavailable (OSError)", true
	}
	if sourceClass(err, "ValueError") {
		return "rollout unavailable (ValueError)", true
	}
	return "", false
}

func (c *CLI) codexErrorWake(ctx context.Context, config, state *value.Value, now float64, errOut io.Writer) (*value.Value, error) {
	agent, err := nativeRequired(state, "agent")
	if err != nil {
		return nil, err
	}
	if _, err := nativeObj(agent, "agent"); err != nil {
		return nil, err
	}
	nowSeconds := now
	if nowSeconds <= 0 {
		nowSeconds = float64(c.clock().UnixNano()) / 1e9
	}
	sessionRef, err := c.sessionRef(agent)
	if err != nil {
		return nil, err
	}
	prior := nativeMap()
	if raw := state.Get("codex_error_wake"); raw != nil && raw.Kind == value.Object {
		if ps := raw.Get("session"); nativeNone(ps) || nativeEqual(ps, sessionRef) {
			prior = raw
		}
	}
	decidedAt, err := utcTimestamp(&value.Value{Kind: value.Float, Number: nowSeconds})
	if err != nil {
		return nil, err
	}
	record := nativeMap("schema", "cairn.codex-error-wake/1", "session", sessionRef, "decided_at", decidedAt)
	for _, k := range []string{"wakes", "last_wake_at", "delivery_id"} {
		if v := prior.Get(k); v != nil {
			record.Set(k, nativeClone(v))
		}
	}
	return c.finishCodexErrorWake(ctx, config, state, agent, prior, record, nowSeconds, errOut)
}

func (c *CLI) finishCodexErrorWake(ctx context.Context, config, state, agent, prior, record *value.Value, nowSeconds float64, errOut io.Writer) (*value.Value, error) {
	health, err := nativeObj(state.Get("native_health"), "native_health")
	if err != nil {
		return nil, err
	}
	decide := func(decision, reason string) *value.Value {
		record.Set("decision", value.Text(decision))
		record.Set("reason", value.Text(reason))
		var priorErrRec, currErrRec *value.Value
		if pe := prior.Get("error"); pe != nil && pe.Kind == value.Object {
			priorErrRec = pe.Get("record")
		}
		if ce := record.Get("error"); ce != nil && ce.Kind == value.Object {
			currErrRec = ce.Get("record")
		}
		if (!nativeEqual(prior.Get("decision"), value.Text(decision)) || !nativeEqual(priorErrRec, currErrRec)) && errOut != nil {
			display := nativeOr(agent.Get("display_name"), agent.Get("agent_id"))
			_, _ = fmt.Fprintf(errOut, "Cairn presence %s: Codex systemError wake decision for %s (native %s): %s; %s\n",
				nativeText(config.Get("binding")), pythonRepr(display, false), pythonRepr(agent.Get("native_session_id"), false), decision, reason)
		}
		state.Set("codex_error_wake", record)
		return record
	}
	if value.Truth(state.Get("inbox_intent")) || value.Truth(state.Get("inbox_attempt")) || value.Truth(state.Get("inbox_close")) || value.Truth(state.Get("inbox_turn_ended")) {
		return decide("held", "a held inbox attempt remains; it is never replayed and needs its own settlement"), nil
	}
	if value.Truth(health.Get("queue_count")) || value.Truth(health.Get("queued_present")) {
		return decide("held", "the native Codex queue still holds items"), nil
	}
	end, locator, err := c.latestCodexTurnEnd(ctx, config, state)
	if err != nil {
		if reason, ok := codexErrorRolloutReason(err); ok {
			return decide("unverified", reason), nil
		}
		return nil, err
	}
	if end == nil {
		return decide("unverified", "no task_complete after the latest turn start in the bounded rollout tail"), nil
	}
	return evaluateCodexTurnEndForWake(end, locator, prior, record, nowSeconds, decide)
}

func evaluateCodexTurnEndForWake(end, locator, prior, record *value.Value, nowSeconds float64, decide func(string, string) *value.Value) (*value.Value, error) {
	payload, err := nativeRequired(end, "payload")
	if err != nil {
		return nil, err
	}
	if _, err := nativeObj(payload, "payload"); err != nil {
		return nil, err
	}
	errObj := payload.Get("error")
	if !nativeNone(payload.Get("last_agent_message")) || errObj == nil || errObj.Kind != value.Object {
		return decide("unverified", "the latest turn did not end on a provider error"), nil
	}
	tsVal := end.Get("timestamp")
	tsText := nativeText(tsVal)
	if tsVal == nil || tsVal.Kind != value.String || !codexTurnEndTimestampRegex.MatchString(tsText) {
		return decide("unverified", "the turn end record timestamp shape is unknown"), nil
	}
	parsed, err := time.Parse("2006-01-02T15:04:05.000Z", tsText)
	if err != nil {
		return decide("unverified", "the turn end record timestamp shape is unknown"), nil
	}
	ended := float64(parsed.UnixNano()) / 1e9
	kindClass, kind := codexErrorClass(errObj.Get("codex_error_info"))
	turnIDVal := payload.Get("turn_id")
	if turnIDVal == nil {
		turnIDVal = &value.Value{Kind: value.Null}
	}
	tsReq, err := nativeRequired(end, "timestamp")
	if err != nil {
		return nil, err
	}
	record.Set("error", nativeMap("kind", kind, "turn_id", turnIDVal, "ended_at", tsReq, "record", locator))
	if kindClass != "transient" {
		return decide("persistent", fmt.Sprintf("%s does not clear by itself; review the provider error and pending delivery before owner-authorized recovery", kind)), nil
	}
	return finishCodexErrorBackoff(prior, record, kind, tsReq, locator, ended, nowSeconds, decide)
}

func finishCodexErrorBackoff(prior, record *value.Value, kind string, tsVal, locator *value.Value, ended, nowSeconds float64, decide func(string, string) *value.Value) (*value.Value, error) {
	var priorLocator *value.Value
	if pe := prior.Get("error"); pe != nil && pe.Kind == value.Object {
		priorLocator = pe.Get("record")
	}
	sameError := nativeEqual(priorLocator, locator)
	lastVal := record.Get("last_wake_at")
	last, lastOK := 0.0, false
	if lastVal != nil && (lastVal.Kind == value.Int || lastVal.Kind == value.Float) {
		if lwf, err := sourceSeconds(lastVal); err == nil {
			last, lastOK = lwf, true
		}
	}
	if !sameError {
		if !(lastOK && last <= ended && ended <= last+float64(codexErrorWakeMaxDelay)) {
			record.Set("wakes", nativeInt64(0))
			lastOK = false
			nativePop(record, "last_wake_at", "delivery_id")
		}
	}
	var wakes int64
	if w := record.Get("wakes"); w != nil && w.Kind == value.Int {
		if n, err := strconv.ParseInt(w.Integer, 10, 64); err == nil && n >= 0 {
			wakes = n
		}
	}
	record.Set("wakes", nativeInt64(wakes))
	due := ended + float64(codexErrorWakeMinDelay)
	if wakes > 0 && lastOK {
		shift := wakes
		if shift > 16 {
			shift = 16
		}
		delay := int64(codexErrorWakeMinDelay) * (1 << shift)
		if delay > codexErrorWakeMaxDelay {
			delay = codexErrorWakeMaxDelay
		}
		if cand := last + float64(delay); cand > due {
			due = cand
		}
	}
	dueVal := &value.Value{Kind: value.Float, Number: due}
	dueAt, err := utcTimestamp(dueVal)
	if err != nil {
		return nil, err
	}
	record.Set("next_eligible_epoch", dueVal)
	record.Set("next_eligible_at", dueAt)
	if nowSeconds < due {
		return decide("backoff", fmt.Sprintf("transient %s at %s; next normal wake after %s (%d earlier)", kind, pythonRepr(tsVal, false), nativeText(dueAt), wakes)), nil
	}
	return decide("wake_allowed", fmt.Sprintf("transient %s at %s; backoff elapsed, normal idle wake allowed", kind, pythonRepr(tsVal, false))), nil
}

func (c *CLI) codexErrorWakeAllowed(config, state *value.Value) (bool, error) {
	if _, err := nativeObj(config, "config"); err != nil {
		return false, err
	}
	if _, err := nativeObj(state, "state"); err != nil {
		return false, err
	}
	rec := state.Get("codex_error_wake")
	if nativeText(config.Get("harness")) != "codex" || rec == nil || rec.Kind != value.Object {
		return false, nil
	}
	agent, err := nativeRequired(state, "agent")
	if err != nil {
		return false, err
	}
	sref, err := c.sessionRef(agent)
	if err != nil {
		return false, err
	}
	return nativeEqual(rec.Get("session"), sref) && nativeText(rec.Get("decision")) == "wake_allowed", nil
}

func (c *CLI) noteCodexErrorWake(config, state *value.Value, delivery string) error {
	allowed, err := c.codexErrorWakeAllowed(config, state)
	if err != nil || !allowed {
		return err
	}
	rec := state.Get("codex_error_wake")
	if value.Truth(state.Get("delivered_since_idle")) {
		state.Set("delivered_since_idle", value.Boolean(false))
		rec.Set("latch", value.Text("cleared"))
	}
	now := float64(c.clock().UnixNano()) / 1e9
	decidedAt, err := utcTimestamp(&value.Value{Kind: value.Float, Number: now})
	if err != nil {
		return err
	}
	wakes, err := nativeIncrement(rec.Get("wakes"))
	if err != nil {
		return err
	}
	reason, err := nativeRequired(rec, "reason")
	if err != nil {
		return err
	}
	if reason.Kind != value.String {
		return &value.Error{Class: "TypeError", Message: "reason must be a string"}
	}
	rec.Set("decision", value.Text("woken"))
	rec.Set("wakes", wakes)
	rec.Set("last_wake_at", &value.Value{Kind: value.Float, Number: now})
	rec.Set("delivery_id", value.Text(delivery))
	rec.Set("decided_at", decidedAt)
	rec.Set("reason", value.Text(nativeText(reason)+"; normal wake issued"))
	return nil
}

func (c *CLI) deliveryObservation(config, state, prepared, cycle *value.Value) (*value.Value, error) {
	agent, err := nativeRequired(state, "agent")
	if err != nil {
		return nil, err
	}
	meta, err := nativeRequired(agent, "metadata")
	if err != nil {
		return nil, err
	}
	if _, err := nativeObj(meta, "metadata"); err != nil {
		return nil, err
	}
	if nativeText(meta.Get("delivery_mode")) != "existing-session" {
		return nil, nil
	}
	if _, err := nativeObj(config, "config"); err != nil {
		return nil, err
	}
	if !value.Truth(config.Get("native_delivery")) && !value.Truth(state.Get("delivery_observation")) {
		return nil, nil
	}
	if cycle == nil || !value.Truth(cycle) {
		cycle = nativeMap()
	}
	sref, err := c.sessionRef(agent)
	if err != nil {
		return nil, err
	}
	healthErrored := false
	if nativeText(config.Get("harness")) == "codex" {
		if h := state.Get("native_health"); h != nil && h.Kind == value.Object && nativeEqual(h.Get("session"), sref) && nativeText(h.Get("status_type")) == "systemError" {
			cew, err := nativeObj(state.Get("codex_error_wake"), "codex_error_wake")
			if err != nil {
				return nil, err
			}
			dec := nativeText(cew.Get("decision"))
			healthErrored = dec != "wake_allowed" && dec != "woken"
		}
	}
	return c.finishDeliveryObservation(config, state, meta, sref, prepared, cycle, healthErrored)
}

func (c *CLI) finishDeliveryObservation(config, state, meta, sref, prepared, cycle *value.Value, healthErrored bool) (*value.Value, error) {
	condition := "unknown"
	var deliveryID, wake *value.Value
	agentState := nativeText(meta.Get("state"))
	if !value.Truth(config.Get("native_delivery")) || value.Truth(state.Get("inbox_intent")) || value.Truth(state.Get("ending")) || value.Truth(state.Get("retired")) {
		condition = "unknown"
	} else if agentState == "busy" && !healthErrored {
		condition = "busy"
	} else if agentState != "idle" && !healthErrored {
		condition = "unknown"
	} else if !value.Truth(config.Get("idle_wakeup")) {
		condition = "owner_turn_required"
	} else if value.Truth(prepared) && value.Truth(prepared.Get("wake")) && !healthErrored && nativeEqual(cycle.Get("ready_delivery"), prepared.Get("wake").Get("delivery_id")) {
		selected := prepared.Get("wake")
		deliveryID = selected.Get("delivery_id")
		if nativeText(selected.Get("status")) == "queued" {
			condition = "wake_retained"
			wake = selected
		} else {
			condition = "automatic_available"
		}
	} else {
		var err error
		condition, deliveryID, wake, err = c.refusalDeliveryObservation(state, cycle)
		if err != nil {
			return nil, err
		}
	}
	return formatDeliveryObservation(sref, condition, deliveryID, wake)
}

func (c *CLI) refusalDeliveryObservation(state, cycle *value.Value) (string, *value.Value, *value.Value, error) {
	refusal, err := nativeObj(state.Get("wake_refusal"), "wake_refusal")
	if err != nil {
		return "", nil, nil, err
	}
	deliv := refusal.Get("delivery_id")
	if !value.Truth(deliv) || !nativeEqual(deliv, cycle.Get("ready_delivery")) || !nativeEqual(deliv, cycle.Get("refusal_delivery")) {
		return "unknown", nil, nil, nil
	}
	cond := nativeText(refusal.Get("condition"))
	if cond == "" {
		cond = "unknown"
	}
	if cond != "wake_retained" {
		return cond, deliv, nil, nil
	}
	selected, err := nativeObj(state.Get("idle_wake"), "idle_wake")
	if err != nil {
		return "", nil, nil, err
	}
	st := nativeText(selected.Get("status"))
	if nativeEqual(selected.Get("delivery_id"), deliv) && (st == "submitted" || st == "uncertain" || st == "queued" || st == "refused") {
		return cond, deliv, selected, nil
	}
	return "unknown", deliv, nil, nil
}

func formatDeliveryObservation(sref *value.Value, condition string, deliveryID, wake *value.Value) (*value.Value, error) {
	if condition == "unknown" {
		deliveryID = nil
	}
	obs := nativeMap("session", sref, "condition", condition)
	if value.Truth(deliveryID) {
		obs.Set("delivery_id", deliveryID)
	}
	if wake != nil {
		status, err := nativeRequired(wake, "status")
		if err != nil {
			return nil, err
		}
		transport := nativeOr(wake.Get("transport"), value.Text("terminal"))
		detail := nativeMap("transport", transport, "status", status)
		if att := wake.Get("attempted_at"); att != nil && (att.Kind == value.Float || att.Kind == value.Int || att.Kind == value.Bool) {
			ts, err := utcTimestamp(att)
			if err != nil {
				return nil, err
			}
			detail.Set("attempted_at", ts)
		}
		obs.Set("wake", detail)
	}
	return obs, nil
}

func (c *CLI) reportDeliveryObservation(ctx context.Context, config, state *value.Value, path string, prepared, cycle *value.Value, errOut io.Writer) error {
	if eng := c.engine(); eng != nil {
		ns := c.newNativeSession(config, state, path)
		obs, err := eng.HeldAttemptRefusalObservation(ctx, ns, nil)
		var pu *value.ProfileUnavailable
		if errors.As(err, &pu) {
			return err
		}
		if err != nil || obs == nil || obs.Kind == value.Null {
			nativePop(state, "held_refusal_observation")
		} else {
			state.Set("held_refusal_observation", obs)
		}
	} else {
		nativePop(state, "held_refusal_observation")
	}
	desired, now, sent, err := c.sendDeliveryObservation(ctx, config, state, prepared, cycle)
	if err == nil {
		if sent {
			nativePop(state, "delivery_observation_error")
			state.Set("delivery_observation", nativeMap("value", desired, "sent_at", now))
		}
		return nil
	}
	diagErr, ok := deliveryObservationErrorName(err)
	if !ok {
		return err
	}
	if nativeText(state.Get("delivery_observation_error")) != diagErr {
		if _, gerr := nativeObj(config, "config"); gerr != nil {
			return gerr
		}
		if errOut != nil {
			_, _ = fmt.Fprintf(errOut, "Cairn presence %s: delivery diagnostics unavailable (%s); ordinary inbox delivery continues\n", nativeText(config.Get("binding")), diagErr)
		}
		state.Set("delivery_observation_error", value.Text(diagErr))
	}
	return nil
}

func (c *CLI) sendDeliveryObservation(ctx context.Context, config, state, prepared, cycle *value.Value) (*value.Value, float64, bool, error) {
	desired, err := c.deliveryObservation(config, state, prepared, cycle)
	if err != nil || desired == nil || desired.Kind == value.Null {
		return nil, 0, false, err
	}
	prev, err := nativeObj(state.Get("delivery_observation"), "delivery_observation")
	if err != nil {
		return nil, 0, false, err
	}
	prevVal := prev.Get("value")
	if nativeText(desired.Get("condition")) == "unknown" && desired.Get("delivery_id") == nil {
		if prevVal != nil && prevVal.Kind == value.Object && value.Truth(prevVal.Get("delivery_id")) {
			pc := nativeText(prevVal.Get("condition"))
			if pc != "none" && pc != "unknown" {
				desired = nativeMap("session", desired.Get("session"), "delivery_id", prevVal.Get("delivery_id"), "condition", "none")
			}
		}
	}
	now := float64(c.clock().UnixNano()) / 1e9
	sentAt := 0.0
	if sa := prev.Get("sent_at"); sa != nil && (sa.Kind == value.Int || sa.Kind == value.Float) {
		if f, ferr := sourceSeconds(sa); ferr == nil {
			sentAt = f
		}
	}
	if nativeEqual(desired, prevVal) && now-sentAt < 45 {
		return desired, now, false, nil
	}
	obsAt, err := utcTimestamp(&value.Value{Kind: value.Float, Number: now})
	if err != nil {
		return nil, 0, false, err
	}
	req := nativeClone(desired)
	req.Set("observed_at", obsAt)
	if _, err := c.call(ctx, config, "session-delivery-observe", req, NativeCallOptions{Timeout: 500 * time.Millisecond}); err != nil {
		return nil, 0, false, err
	}
	return desired, now, true, nil
}

func deliveryObservationErrorName(err error) (string, bool) {
	var ce *CoordinationError
	if errors.As(err, &ce) {
		return ce.Code, true
	}
	var je *journal.Error
	if errors.As(err, &je) {
		return je.Code, true
	}
	if sourceClass(err, "OSError", "ValueError", "KeyError", "TypeError", "OverflowError", "JSONDecodeError", "UnicodeDecodeError", "UnicodeEncodeError") {
		return sourceErrorName(err), true
	}
	if sourceOSError(err) || isOSPathErr(err) {
		return "OSError", true
	}
	return "", false
}

func (c *CLI) agyHome(config *value.Value) (string, error) {
	if configured := config.Get("agy_home"); value.Truth(configured) {
		return c.pathText(configured)
	}
	if home, ok := c.lookupEnv("HOME"); ok && home != "" {
		return filepath.Join(home, ".gemini", "antigravity-cli"), nil
	}
	home, err := os.UserHomeDir()
	if err != nil {
		return "", err
	}
	return filepath.Join(home, ".gemini", "antigravity-cli"), nil
}

func (c *CLI) readJSONObject(path string) (*value.Value, error) {
	v, err := c.read(path, SiteSessionRead)
	if err != nil {
		var pu *value.ProfileUnavailable
		if errors.As(err, &pu) {
			return nil, err
		}
		return nil, nil
	}
	if v == nil || v.Kind != value.Object {
		return nil, nil
	}
	return v, nil
}

func (c *CLI) lastJSONLRecord(path string) (*value.Value, error) {
	prof := c.profile()
	if prof == nil {
		return nil, &value.ProfileUnavailable{}
	}
	fd, err := syscall.Open(path, syscall.O_RDONLY|syscall.O_NOFOLLOW|syscall.O_NONBLOCK|syscall.O_CLOEXEC, 0)
	if err != nil {
		return nil, nil
	}
	defer syscall.Close(fd)
	var st syscall.Stat_t
	if err := syscall.Fstat(fd, &st); err != nil || st.Mode&syscall.S_IFMT != syscall.S_IFREG || st.Uid != c.uid() {
		return nil, nil
	}
	base := int64(0)
	if st.Size > 65536 {
		base = st.Size - 65536
	}
	buf := make([]byte, int(st.Size-base))
	n, err := syscall.Pread(fd, buf, base)
	if err != nil && !errors.Is(err, io.EOF) {
		return nil, nil
	}
	return decodeLastJSONLLine(prof, buf[:n], st.Size > 65536)
}

func decodeLastJSONLLine(prof *value.SourceReaderProfile, raw []byte, truncated bool) (*value.Value, error) {
	var lines [][]byte
	for _, line := range bytes.Split(raw, []byte{'\n'}) {
		if len(bytes.Trim(line, " \t\n\r\v\f")) > 0 {
			lines = append(lines, line)
		}
	}
	if len(lines) == 0 || (truncated && len(lines) == 1) {
		return nil, nil
	}
	v, err := decodeCompatibilityJSON(prof, siteLastJSONLRecord, lines[len(lines)-1])
	if err != nil {
		var pu *value.ProfileUnavailable
		if errors.As(err, &pu) {
			return nil, err
		}
		return nil, nil
	}
	if v == nil || v.Kind != value.Object {
		return nil, nil
	}
	return v, nil
}

func (c *CLI) agySubagentFinished(config, state *value.Value, path string) (*value.Value, error) {
	if _, err := nativeObj(config, "config"); err != nil {
		return nil, err
	}
	if _, err := nativeObj(state, "state"); err != nil {
		return nil, err
	}
	agent := state.Get("agent")
	var native string
	if agent != nil && agent.Kind == value.Object && agent.Get("native_session_id") != nil && agent.Get("native_session_id").Kind == value.String {
		native = nativeText(agent.Get("native_session_id"))
	}
	process := state.Get("process")
	if nativeText(config.Get("harness")) != "agy" || native == "" || value.Truth(state.Get("ending")) || value.Truth(state.Get("retired")) ||
		value.Truth(state.Get("inbox_intent")) || value.Truth(state.Get("inbox_attempt")) || value.Truth(state.Get("inbox_journals")) ||
		process == nil || process.Kind != value.Object || strings.Contains(native, "/") || native == "." || native == ".." {
		return nil, nil
	}
	now := float64(c.clock().UnixNano()) / 1e9
	home, err := c.agyHome(config)
	if err != nil {
		return nil, err
	}
	stateDir, err := c.pathText(config.Get("state_dir"))
	if err != nil {
		return nil, err
	}
	matches, _ := filepath.Glob(filepath.Join(stateDir, "*.json"))
	sort.Strings(matches)
	for _, otherPath := range matches {
		if otherPath == path {
			continue
		}
		ev, stop, err := c.inspectAgyParentSubagent(otherPath, home, native, agent, process, now)
		if err != nil || stop {
			return ev, err
		}
	}
	return nil, nil
}

func (c *CLI) inspectAgyParentSubagent(otherPath, home, native string, agent, process *value.Value, now float64) (*value.Value, bool, error) {
	other, err := c.readJSONObject(otherPath)
	if err != nil || other == nil {
		return nil, false, err
	}
	parent := other.Get("agent")
	var parentNative string
	if parent != nil && parent.Kind == value.Object && parent.Get("native_session_id") != nil && parent.Get("native_session_id").Kind == value.String {
		parentNative = nativeText(parent.Get("native_session_id"))
	}
	otherProc := other.Get("process")
	if parentNative == "" || parentNative == native || strings.Contains(parentNative, "/") || parentNative == "." || parentNative == ".." ||
		value.Truth(other.Get("retired")) || value.Truth(other.Get("ending")) || otherProc == nil || otherProc.Kind != value.Object {
		return nil, false, nil
	}
	if same, err := c.sameProcess(otherProc, process); err != nil || !same {
		return nil, false, err
	}
	rec, err := c.readJSONObject(filepath.Join(home, "brain", parentNative, ".system_generated", "subagents", native+".json"))
	if err != nil || rec == nil || nativeText(rec.Get("conversationId")) != native {
		return nil, false, err
	}
	regState := nativeText(rec.Get("state"))
	if regState != "SUBAGENT_STATE_ALIVE" && regState != "SUBAGENT_STATE_KILLED" {
		return nil, false, nil
	}
	ev := nativeMap("parent", parent, "parent_native_session_id", parentNative, "registry_state", rec.Get("state"))
	if regState == "SUBAGENT_STATE_KILLED" {
		return ev, true, nil
	}
	meta, err := nativeObj(agent.Get("metadata"), "metadata")
	if err != nil || nativeText(meta.Get("state")) != "idle" {
		return nil, true, err
	}
	own := filepath.Join(home, "brain", native, ".system_generated")
	transcriptPath := filepath.Join(own, "logs", "transcript.jsonl")
	last, err := c.lastJSONLRecord(transcriptPath)
	if err != nil || last == nil || nativeText(last.Get("source")) != "MODEL" || nativeText(last.Get("type")) != "PLANNER_RESPONSE" || nativeText(last.Get("status")) != "DONE" {
		return nil, true, err
	}
	created := pythonRepr(last.Get("created_at"), false)
	parsed, err := time.Parse(time.RFC3339Nano, strings.Replace(created, "Z", "+00:00", 1))
	if err != nil {
		return nil, true, nil
	}
	st, err := os.Stat(transcriptPath)
	if err != nil {
		return nil, true, nil
	}
	undeliv := filepath.Join(own, "messages", "undelivered")
	if entries, err := os.ReadDir(undeliv); err == nil && len(entries) > 0 {
		return nil, true, nil
	} else if err != nil && !os.IsNotExist(err) {
		return nil, true, nil
	}
	ended := float64(parsed.UnixNano()) / 1e9
	modified := float64(st.ModTime().UnixNano()) / 1e9
	latest := ended
	if modified > latest {
		latest = modified
	}
	if now-latest < agySubagentQuietSecs {
		return nil, true, nil
	}
	ev.Set("last_turn_ended_at", last.Get("created_at"))
	return ev, true, nil
}
