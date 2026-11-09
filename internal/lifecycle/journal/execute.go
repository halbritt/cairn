package journal

import (
	"context"
	"errors"
	"os"
	"os/exec"
	"syscall"
	"time"
	"unicode"
	"unicode/utf8"
)

// Execute journals a command before sending it. It deliberately recontacts the
// authenticated command even for a final slot; local success cannot mask fencing.
// Result streams pass through verbatim for single-slot commands. Error carries
// the public exit mapping, while NonObjectError remains separately inspectable.
func Execute(ctx context.Context, native Context, request Request, options Options) (Result, error) {
	return ExecuteRaw(ctx, native.raw(), request, options)
}

func ExecuteRaw(ctx context.Context, native RawContext, request Request, options Options) (Result, error) {
	result, err := execute(ctx, native, request, options)
	var public *Error
	var nonObject *NonObjectError
	if err == nil || errors.As(err, &public) || errors.As(err, &nonObject) || errors.Is(err, context.Canceled) || errors.Is(err, context.DeadlineExceeded) {
		return result, err
	}
	return Result{}, &Error{Code: "INVALID_HOST", ExitCode: 7, Message: err.Error(), Cause: err}
}

func invalid(message string) error {
	return &Error{Code: "INVALID_REQUEST", ExitCode: 2, Message: message}
}

func conflict(exit int, message string) error {
	return &Error{Code: "IDEMPOTENCY_CONFLICT", ExitCode: exit, Message: message}
}

func digits(s string) bool {
	if s == "" {
		return false
	}
	for _, r := range s {
		// Python str.isdigit includes Numeric_Type=Digit as well as decimal
		// digits. These non-Nd ranges include superscripts and circled digits.
		other := r == 0x00b9 || r >= 0x00b2 && r <= 0x00b3 || r >= 0x1369 && r <= 0x1371 || r == 0x19da ||
			r == 0x2070 || r >= 0x2074 && r <= 0x2079 || r >= 0x2080 && r <= 0x2089 ||
			r >= 0x2460 && r <= 0x2468 || r >= 0x2474 && r <= 0x247c || r >= 0x2488 && r <= 0x2490 ||
			r == 0x24ea || r >= 0x24f5 && r <= 0x24fd || r == 0x24ff || r >= 0x2776 && r <= 0x277e ||
			r >= 0x2780 && r <= 0x2788 || r >= 0x278a && r <= 0x2792 || r >= 0x10a40 && r <= 0x10a43 ||
			r >= 0x10e60 && r <= 0x10e68 || r >= 0x11052 && r <= 0x1105a || r >= 0x1f100 && r <= 0x1f10a
		other = other || r >= 0x10d40 && r <= 0x10d49 || r >= 0x116d0 && r <= 0x116e3 ||
			r >= 0x11bf0 && r <= 0x11bf9 || r >= 0x16130 && r <= 0x16139 || r >= 0x16d70 && r <= 0x16d79 ||
			r >= 0x1ccf0 && r <= 0x1ccf9 || r >= 0x1e5f1 && r <= 0x1e5fa
		if !unicode.IsDigit(r) && !other {
			return false
		}
	}
	return true
}

// ReplyVersion validates the pre-stdin reply guard shared with text frontends.
func ReplyVersion(version []string) (string, error) {
	if len(version) != 2 || !digits(version[0]) {
		return "", invalid("a reply requires --version RESULT_VERSION RESULT_RECORD_UUID")
	}
	return canonicalUUID(version[1])
}

func prepare(native RawContext, request Request) ([]any, string, string, error) {
	commandKind, name := request.Kind, "completion"
	switch request.Kind {
	case "complete", "ack":
	case "respond":
		name = "response"
	case "finish-reply":
		commandKind = "complete"
	default:
		return nil, "", "", invalid("unknown inbox command")
	}
	commands, err := object(native.Values["commands"], "commands")
	if err != nil {
		return nil, "", "", err
	}
	if !truth(commands[commandKind]) {
		return nil, "", "", invalid("this inbox context has no " + request.Kind + " command")
	}
	command, err := native.Command(commandKind)
	if err != nil {
		return nil, "", "", err
	}
	if request.Kind == "finish-reply" && (!equal(native.Values["kind"], "request") || !truth(commands["respond"])) {
		return nil, "", "", invalid("finish-reply requires a request and its explicit reply command")
	}
	if request.Kind == "respond" {
		id, err := ReplyVersion(request.Version)
		if err != nil {
			return nil, "", "", err
		}
		command = append(command, "--version", request.Version[0], id)
	} else if len(request.Version) != 0 {
		return nil, "", "", invalid("--version applies only to a reply")
	}
	if request.Kind == "ack" {
		disposition := request.Disposition
		if disposition == "" {
			disposition = "handled"
		}
		if disposition != "handled" && disposition != "ignored" && disposition != "failed" {
			return nil, "", "", invalid("invalid acknowledgement disposition")
		}
		if disposition == "handled" && request.Code != "" || disposition != "handled" && request.Code == "" {
			return nil, "", "", invalid("ignored or failed acknowledgement needs a reason code")
		}
		if request.Code != "" && request.Code != "unsupported_kind" && request.Code != "source_unavailable" && request.Code != "processing_failed" {
			return nil, "", "", invalid("invalid acknowledgement reason")
		}
		if disposition != "handled" {
			tail := command[len(command)-1]
			command = append(command[:len(command)-1], "--disposition", disposition, "--code", request.Code, tail)
		}
	} else if request.Disposition != "" || request.Code != "" {
		return nil, "", "", invalid("disposition and code apply only to acknowledgement")
	}
	body := ""
	for _, arg := range command {
		if equal(arg, "--stdin") {
			body = request.Input
			break
		}
	}
	if !utf8.ValidString(body) {
		return nil, "", "", &ValueError{"inbox command input is not UTF-8"}
	}
	if utf8.RuneCountInString(body) > 1024*1024 {
		return nil, "", "", invalid("inbox command input exceeds limit")
	}
	return command, body, name, nil
}

func execute(ctx context.Context, native RawContext, request Request, options Options) (Result, error) {
	command, body, name, err := prepare(native, request)
	if err != nil {
		return Result{}, err
	}
	target := path(native.StateDir, native.AttemptID)
	locked, err := lock(ctx, target, executeWait)
	if err != nil {
		return Result{}, err
	}
	defer locked.Close()
	if err := ctx.Err(); err != nil {
		return Result{}, err
	}
	var v any
	if !exists(target) {
		created := map[string]any{"schema": Schema}
		for _, key := range []string{"attempt_id", "delivery_id", "kind"} {
			created[key], err = required(native.Values, key)
			if err != nil {
				return Result{}, err
			}
		}
		session := map[string]any{}
		for _, key := range []string{"agent_id", "execution_id"} {
			session[key], err = required(native.Values, key)
			if err != nil {
				return Result{}, err
			}
		}
		created["session"] = session
		v = created
	} else {
		v, err = load(target)
		if err != nil {
			return Result{}, err
		}
	}
	journal, err := object(v, "journal")
	if err != nil {
		return Result{}, err
	}
	if request.Kind == "finish-reply" {
		return executeFinish(ctx, native, journal, command, body, target, options)
	}
	if request.Kind == "respond" {
		if err := validateResponse(journal, request.Version[0], text(command[len(command)-1])); err != nil {
			return Result{}, err
		}
	}
	slot, err := indexedObject(journal[name], name)
	if err != nil {
		return Result{}, err
	}
	if len(slot) != 0 {
		matched, err := matchFields(slot, fieldMatch{"kind", request.Kind}, fieldMatch{"argv", command}, fieldMatch{"input", body})
		if err != nil {
			return Result{}, err
		}
		if !matched {
			if _, err := required(slot, "status"); err != nil {
				return Result{}, err
			}
			return Result{}, conflict(4, "this attempt already journaled a different "+name)
		}
	} else {
		id, err := native.requestID(name)
		if err != nil {
			return Result{}, err
		}
		slot = newSlot(request.Kind, id, command, body)
		journal[name] = slot
		if err := ctx.Err(); err != nil {
			return Result{}, err
		}
		if err := write(target, journal, options); err != nil {
			return Result{}, err
		}
	}
	result, err := send(ctx, slot, executeTimeout, options)
	if err != nil {
		return Result{}, err
	}
	if err := write(target, journal, options); err != nil {
		return Result{}, err
	}
	if result == nil {
		data, err := encode(map[string]any{"schema": "cairn.response/1", "ok": false, "status": "API_CONNECTION_FAILED",
			"message": "API_CONNECTION_FAILED: the command is journaled; retry it unchanged or let the host replay it"})
		return Result{Stdout: string(data), ExitCode: 7}, err
	}
	return *result, nil
}

func newSlot(kind string, requestID any, command []any, input string) map[string]any {
	return map[string]any{"kind": kind, "request_id": requestID, "argv": command, "input": input,
		"status": "pending", "recorded_at": float64(time.Now().UnixNano()) / 1e9}
}

func indexedObject(v any, field string) (map[string]any, error) {
	if !truth(v) {
		return map[string]any{}, nil
	}
	m, ok := value(v).(map[string]any)
	if !ok {
		return nil, &TypeError{field}
	}
	return m, nil
}

type fieldMatch struct {
	key      string
	expected any
}

func matchFields(slot map[string]any, fields ...fieldMatch) (bool, error) {
	for _, field := range fields {
		v, err := required(slot, field.key)
		if err != nil {
			return false, err
		}
		if !equal(v, field.expected) {
			return false, nil
		}
	}
	return true, nil
}

func validateResponse(journal map[string]any, version, id string) error {
	completion, err := orObject(journal["completion"], "completion")
	if err != nil {
		return err
	}
	v := completion["result"]
	if text(completion["status"]) != "committed" || !truth(v) {
		return invalid("a reply requires this attempt's committed completion and its exact result version")
	}
	result, ok := value(v).(map[string]any)
	if !ok {
		return &TypeError{"completion.result"}
	}
	rv, err := required(result, "version")
	if err != nil {
		return err
	}
	ri, err := required(result, "record_id")
	if err != nil {
		return err
	}
	if version != scalarString(rv) || !equal(id, ri) {
		return invalid("a reply requires this attempt's committed completion and its exact result version")
	}
	return nil
}

func send(ctx context.Context, slot map[string]any, timeout time.Duration, options Options) (*Result, error) {
	argv, err := required(slot, "argv")
	if err != nil {
		return nil, err
	}
	command, err := stringList(argv, "slot.argv")
	if err != nil {
		return nil, err
	}
	input, err := required(slot, "input")
	if err != nil {
		return nil, err
	}
	inputString, ok := input.(string)
	if !ok {
		return nil, &TypeError{"slot.input"}
	}
	runner := options.Runner
	if runner == nil {
		runner = CommandRunner{}
	}
	bounded, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()
	result, runErr := runner.Run(bounded, command, inputString)
	if runErr != nil {
		var path *os.PathError
		var executable *exec.Error
		var errno syscall.Errno
		if !errors.As(runErr, &path) && !errors.As(runErr, &executable) && !errors.As(runErr, &errno) &&
			!errors.Is(runErr, context.Canceled) && !errors.Is(runErr, context.DeadlineExceeded) && !errors.Is(runErr, exec.ErrWaitDelay) {
			return nil, runErr
		}
	}
	var response map[string]any
	var returned *Result
	if runErr == nil {
		returned = &result
		parsed, err := decode([]byte(result.Stdout))
		if err == nil {
			response, _ = value(parsed).(map[string]any)
		}
	}
	status, code := "pending", "API_UNAVAILABLE"
	okResponse, booleanOK := value(response["ok"]).(bool)
	if response != nil && booleanOK {
		candidate := value(response["status"])
		if okResponse && result.ExitCode == 0 {
			status, code = "committed", "OK"
		} else {
			switch candidate.(type) {
			case map[string]any, []any:
				return nil, &TypeError{"response.status is unhashable"}
			}
			if FinalRefusal(text(candidate)) {
				status, code = "refused", text(candidate)
			} else if truth(candidate) {
				runes := []rune(scalarString(candidate))
				code = string(runes[:min(64, len(runes))])
			}
		}
	}
	if status == "committed" && truth(slot["expected_ref"]) {
		data, _ := value(response["data"]).(map[string]any)
		id, err := canonicalUUID(data["event_id"])
		if err != nil || !equal(id, data["event_id"]) || !equal(data["ref"], slot["expected_ref"]) {
			status, code = "pending", "SCHEMA_MISMATCH"
		}
	}
	previous, err := required(slot, "status")
	if err != nil {
		return nil, err
	}
	data, _ := value(response["data"]).(map[string]any)
	if previous != "pending" {
		slot["replay_code"] = code
		if text(slot["kind"]) == "complete" && value(slot["result"]) == nil && status == "committed" && data != nil {
			slot["result"] = data["result"]
		}
		return returned, nil
	}
	slot["status"], slot["code"] = status, code
	if status == "committed" {
		slot["result"] = nil
		if text(slot["kind"]) != "respond" {
			slot["result"] = data["result"]
		}
	}
	return returned, nil
}

func finishRef(completion map[string]any) map[string]any {
	if text(completion["status"]) != "committed" {
		return nil
	}
	result, ok := value(completion["result"]).(map[string]any)
	if !ok || len(result) != 2 {
		return nil
	}
	version, ok := integer(result["version"])
	if !ok || version.Sign() < 1 {
		return nil
	}
	id, err := canonicalUUID(result["record_id"])
	if err != nil {
		return nil
	}
	return map[string]any{"record_id": id, "version": value(result["version"])}
}

func materialize(journal map[string]any, target string, options Options) (map[string]any, error) {
	if !truth(journal["finish_reply"]) {
		return nil, nil
	}
	completion, err := orObject(journal["completion"], "completion")
	if err != nil {
		return nil, err
	}
	ref := finishRef(completion)
	if ref == nil {
		return nil, nil
	}
	intent, ok := value(journal["finish_reply"]).(map[string]any)
	if !ok {
		return nil, &TypeError{"finish_reply"}
	}
	v, err := required(intent, "argv")
	if err != nil {
		return nil, err
	}
	command, err := rawList(v)
	if err != nil {
		return nil, err
	}
	requestID, err := required(intent, "request_id")
	if err != nil {
		return nil, err
	}
	command = append(command, "--version", scalarString(ref["version"]), text(ref["record_id"]))
	if truth(journal["response"]) {
		response, err := indexedObject(journal["response"], "response")
		if err != nil {
			return nil, err
		}
		matched, err := matchFields(response, fieldMatch{"kind", "respond"}, fieldMatch{"request_id", requestID}, fieldMatch{"argv", command}, fieldMatch{"input", ""})
		if err != nil {
			return nil, err
		}
		if !matched || !equal(response["expected_ref"], ref) {
			return nil, conflict(7, "journaled reply differs from this attempt result")
		}
		return response, nil
	}
	response := newSlot("respond", requestID, command, "")
	response["expected_ref"] = ref
	journal["response"] = response
	return response, write(target, journal, options)
}

func executeFinish(ctx context.Context, native RawContext, journal map[string]any, command []any, body, target string, options Options) (Result, error) {
	reply, err := native.Command("respond")
	if err != nil {
		return Result{}, err
	}
	id, err := native.requestID("response")
	if err != nil {
		return Result{}, err
	}
	intent := map[string]any{"argv": reply, "request_id": id}
	old := journal["finish_reply"]
	if truth(old) && !equal(old, intent) {
		return Result{}, conflict(4, "this attempt already owns a different completion or reply intent")
	}
	completion, err := indexedObject(journal["completion"], "completion")
	if err != nil {
		return Result{}, err
	}
	if len(completion) != 0 {
		matched, err := matchFields(completion, fieldMatch{"kind", "complete"}, fieldMatch{"argv", command})
		if err != nil {
			return Result{}, err
		}
		if matched {
			completionID, e := native.requestID("completion")
			if e != nil {
				return Result{}, e
			}
			matched, err = matchFields(completion, fieldMatch{"request_id", completionID}, fieldMatch{"input", body})
			if err != nil {
				return Result{}, err
			}
		}
		if !matched {
			return Result{}, conflict(4, "this attempt already owns a different completion or reply intent")
		}
	}
	if truth(journal["response"]) && !truth(old) {
		return Result{}, conflict(4, "this attempt already owns a different completion or reply intent")
	}
	if !truth(old) {
		journal["finish_reply"] = intent
	}
	if len(completion) == 0 {
		completionID, err := native.requestID("completion")
		if err != nil {
			return Result{}, err
		}
		completion = newSlot("complete", completionID, command, body)
		journal["completion"] = completion
	}
	if err := ctx.Err(); err != nil {
		return Result{}, err
	}
	if err := write(target, journal, options); err != nil {
		return Result{}, err
	}
	var replay any
	status, err := required(completion, "status")
	if err != nil {
		return Result{}, err
	}
	if status == "pending" || finishRef(completion) == nil {
		if _, err := send(ctx, completion, executeTimeout, options); err != nil {
			return Result{}, err
		}
		replay = completion["replay_code"]
		if err := write(target, journal, options); err != nil {
			return Result{}, err
		}
	}
	response, err := materialize(journal, target, options)
	if err != nil {
		return Result{}, err
	}
	if len(response) != 0 && text(completion["status"]) == "committed" {
		if _, err := send(ctx, response, executeTimeout, options); err != nil {
			return Result{}, err
		}
		if truth(response["replay_code"]) {
			replay = response["replay_code"]
		}
		if err := write(target, journal, options); err != nil {
			return Result{}, err
		}
	}
	return finishOutput(journal, replay)
}

func finishOutput(journal map[string]any, replay any) (Result, error) {
	completion, err := object(journal["completion"], "completion")
	if err != nil {
		return Result{}, err
	}
	response, err := orObject(journal["response"], "response")
	if err != nil {
		return Result{}, err
	}
	committed, replied := text(completion["status"]) == "committed", text(response["status"]) == "committed"
	code := completion["code"]
	if truth(replay) && text(replay) != "OK" {
		code = replay
	} else if committed {
		code = "RESULT_UNCONFIRMED"
		if len(response) != 0 {
			code = response["code"]
		}
	}
	if committed && replied && !truth(replay) {
		code = "OK"
	}
	ok := committed && replied && text(code) == "OK"
	message := ""
	if !ok {
		message = "finish-reply is partial, refused, or uncertain; retry pending stages unchanged or reconcile a refusal"
	}
	data := map[string]any{"schema": "cairn.finish-reply/1", "completion_committed": committed,
		"reply_pending":   text(completion["status"]) != "refused" && !replied && text(response["status"]) != "refused",
		"reply_committed": replied, "reply_refused": text(response["status"]) == "refused", "result": finishRef(completion)}
	encoded, err := encode(map[string]any{"schema": "cairn.response/1", "ok": ok, "status": code, "data": data, "message": message})
	exit := 0
	if !ok {
		exit = 7
		if FinalRefusal(text(code)) {
			exit = 4
		}
	}
	return Result{Stdout: string(encoded), ExitCode: exit}, err
}
