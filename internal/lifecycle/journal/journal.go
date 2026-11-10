// Package journal owns durable inbox intents. It does not authenticate contexts,
// claim deliveries, or reconcile session state; its caller supplies a validated
// native context and retains the Python readers' distinct error policies.
package journal

import (
	"bytes"
	"context"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math"
	"math/big"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"strconv"
	"strings"
	"syscall"
	"time"
	"unicode/utf16"
	"unicode/utf8"
)

const Schema = "cairn.inbox-intent/1"

const (
	executeWait    = 30 * time.Second
	executeTimeout = 30 * time.Second
	recoverTimeout = 10 * time.Second
)

// FinalRefusal reports the closed set of outcomes that cannot commit on retry.
func FinalRefusal(code string) bool {
	switch code {
	case "STALE_LEASE", "STALE_SESSION", "HOLD_RELEASED", "REQUEST_CANCELLED",
		"DEADLINE_EXCEEDED", "IDEMPOTENCY_CONFLICT", "INVALID_REQUEST", "NOT_FOUND",
		"AUTHORITY_DENIED", "PAYLOAD_UNAVAILABLE", "DESTINATION_PROHIBITED", "STALE_RESOLUTION":
		return true
	}
	return false
}

// Error carries the public inbox mapping. Cause retains classified source errors.
type Error struct {
	Code     string
	ExitCode int
	Message  string
	Cause    error
}

func (e *Error) Error() string { return e.Code + ": " + e.Message }
func (e *Error) Unwrap() error { return e.Cause }

// NonObjectError is the escaping AttributeError counterpart. It must not be
// folded into the OSError/ValueError/KeyError/TypeError containment family.
type NonObjectError struct{ Field string }

func (e *NonObjectError) Error() string { return e.Field + " is not an object" }

type KeyError struct{ Key string }

func (e *KeyError) Error() string { return "missing key: " + e.Key }

type TypeError struct{ Field string }

func (e *TypeError) Error() string { return "invalid type: " + e.Field }

type ValueError struct{ Message string }

func (e *ValueError) Error() string { return e.Message }

type UnicodeDecodeError struct{ Message string }

func (e *UnicodeDecodeError) Error() string { return e.Message }
func (e *UnicodeDecodeError) Unwrap() error { return &ValueError{e.Message} }

// Classified identifies errors caught by the legacy session/hook boundaries.
func Classified(err error) bool {
	var path *os.PathError
	var process *exec.Error
	var errno syscall.Errno
	var syntax *json.SyntaxError
	var key *KeyError
	var typ *TypeError
	var value *ValueError
	return errors.As(err, &path) || errors.As(err, &process) || errors.As(err, &errno) || errors.As(err, &syntax) || errors.As(err, &key) ||
		errors.As(err, &typ) || errors.As(err, &value)
}

type Session struct {
	AgentID     string `json:"agent_id"`
	ExecutionID string `json:"execution_id"`
}

// Context is selected by the native host, never by an inbox command's caller.
// Commands contain token-file selectors, never token contents.
type Context struct {
	StateDir   string
	AttemptID  string
	DeliveryID string
	Kind       string
	Session    Session
	Commands   map[string][]string
	RequestIDs map[string]string
}

// RawContext keeps Source values untyped until their use. Command preserves the
// frontend's ordered iterable semantics; it must not validate argv item types.
type RawContext struct {
	StateDir  string
	AttemptID string
	Values    map[string]any
	Command   func(string) ([]any, error)
}

func (n Context) raw() RawContext {
	commands := make(map[string]any, len(n.Commands))
	for k, args := range n.Commands {
		commands[k] = args
	}
	ids := make(map[string]any, len(n.RequestIDs))
	for k, id := range n.RequestIDs {
		ids[k] = id
	}
	return RawContext{StateDir: n.StateDir, AttemptID: n.AttemptID,
		Values: map[string]any{"state_dir": n.StateDir, "attempt_id": n.AttemptID, "delivery_id": n.DeliveryID,
			"kind": n.Kind, "agent_id": n.Session.AgentID, "execution_id": n.Session.ExecutionID, "commands": commands, "request_ids": ids},
		Command: func(name string) ([]any, error) { return rawList(n.Commands[name]) }}
}

func rawList(v any) ([]any, error) {
	switch v := value(v).(type) {
	case []any:
		return append([]any{}, v...), nil
	case []string:
		out := make([]any, len(v))
		for i, s := range v {
			out[i] = s
		}
		return out, nil
	}
	return nil, &TypeError{"command"}
}

func (n RawContext) requestID(name string) (any, error) {
	v, err := required(n.Values, "request_ids")
	if err != nil {
		return nil, err
	}
	ids, ok := v.(map[string]any)
	if !ok {
		return nil, &TypeError{"request_ids"}
	}
	return required(ids, name)
}

type Request struct {
	Kind        string
	Input       string
	Version     []string
	Disposition string
	Code        string
}

type Result struct {
	Stdout   string
	Stderr   string
	ExitCode int
}

// Runner returns a child exit, including nonzero exits, as Result with nil error.
// OS/exec errors and context cancellation/deadline mean uncertain transport;
// other errors propagate to the caller instead of being treated as retries.
type Runner interface {
	Run(context.Context, []string, string) (Result, error)
}

type RunnerFunc func(context.Context, []string, string) (Result, error)

func (f RunnerFunc) Run(ctx context.Context, argv []string, input string) (Result, error) {
	return f(ctx, argv, input)
}

// CommandRunner is the default when the library is wired to real native commands.
type CommandRunner struct{}

func (CommandRunner) Run(ctx context.Context, argv []string, input string) (Result, error) {
	if len(argv) == 0 {
		return Result{}, &ValueError{"empty command"}
	}
	cmd := exec.CommandContext(ctx, argv[0], argv[1:]...)
	cmd.WaitDelay = 100 * time.Millisecond
	cmd.Stdin = strings.NewReader(input)
	var stdout, stderr bytes.Buffer
	cmd.Stdout, cmd.Stderr = &stdout, &stderr
	err := cmd.Run()
	if ctx.Err() != nil {
		return Result{}, ctx.Err()
	}
	if errors.Is(err, exec.ErrWaitDelay) {
		return Result{}, err
	}
	return commandResult(stdout.Bytes(), stderr.Bytes(), err)
}

func commandResult(stdout, stderr []byte, err error) (Result, error) {
	if !utf8.Valid(stdout) || !utf8.Valid(stderr) {
		return Result{}, &UnicodeDecodeError{"command output is not UTF-8"}
	}
	newlines := func(s string) string { return strings.ReplaceAll(strings.ReplaceAll(s, "\r\n", "\n"), "\r", "\n") }
	result := Result{Stdout: newlines(string(stdout)), Stderr: newlines(string(stderr))}
	var exit *exec.ExitError
	if errors.As(err, &exit) {
		result.ExitCode = exit.ExitCode()
		return result, nil
	}
	return result, err
}

// IO exposes only durable publication boundaries for per-invocation fault tests.
// Nil functions use the operating system. Sync and Close include the directory.
type IO struct {
	Write  func(*os.File, []byte) (int, error)
	Sync   func(*os.File) error
	Close  func(*os.File) error
	Rename func(string, string) error
}

type Options struct {
	Runner Runner
	IO     IO
}

func (o Options) io() IO {
	c := o.IO
	if c.Write == nil {
		c.Write = (*os.File).Write
	}
	if c.Sync == nil {
		c.Sync = (*os.File).Sync
	}
	if c.Close == nil {
		c.Close = (*os.File).Close
	}
	if c.Rename == nil {
		c.Rename = os.Rename
	}
	return c
}

// jsonAtom keeps untouched leaves in their original representation, including
// large integers and decimal exponents in extensions.
// Accessors use the decoded value; publishing an unrelated update retains the raw leaf.
type jsonAtom struct {
	raw   json.RawMessage
	value any
}

func (a jsonAtom) MarshalJSON() ([]byte, error) {
	// Python reads an escaped lone surrogate but cannot write it as UTF-8 with
	// ensure_ascii=False. Keep that ValueError boundary rather than replacing it.
	if _, ok := a.value.(string); ok {
		for i := 0; i < len(a.raw); i++ {
			if a.raw[i] != '\\' || i+1 == len(a.raw) {
				continue
			}
			if a.raw[i+1] != 'u' {
				i++
				continue
			}
			unit, _ := strconv.ParseUint(string(a.raw[i+2:i+6]), 16, 16) // Already validated JSON escape.
			if unit >= 0xd800 && unit <= 0xdbff {
				if i+12 > len(a.raw) || string(a.raw[i+6:i+8]) != `\u` {
					return nil, &ValueError{"lone surrogate cannot be encoded as UTF-8"}
				}
				low, err := strconv.ParseUint(string(a.raw[i+8:i+12]), 16, 16)
				if err != nil || low < 0xdc00 || low > 0xdfff {
					return nil, &ValueError{"lone surrogate cannot be encoded as UTF-8"}
				}
				i += 11
				continue
			}
			if unit >= 0xdc00 && unit <= 0xdfff {
				return nil, &ValueError{"lone surrogate cannot be encoded as UTF-8"}
			}
			i += 5
		}
	}
	return a.raw, nil
}

func value(v any) any {
	if a, ok := v.(jsonAtom); ok {
		return a.value
	}
	return v
}

func decode(data []byte) (any, error) {
	if !utf8.Valid(data) {
		return nil, &UnicodeDecodeError{"journal is not UTF-8"}
	}
	var raw json.RawMessage
	if err := json.Unmarshal(maskConstants(data), &raw); err != nil {
		return nil, err
	}
	return decodeNode(bytes.TrimSpace(data))
}

func decodeNode(raw json.RawMessage) (any, error) {
	raw = bytes.TrimSpace(raw)
	if constant := defaultConstants[string(raw)]; constant != nil {
		return jsonAtom{raw, constant}, nil
	}
	switch raw[0] {
	case '{':
		d := json.NewDecoder(bytes.NewReader(maskConstants(raw)))
		if _, err := d.Token(); err != nil {
			return nil, err
		}
		object := map[string]any{}
		for d.More() {
			start := int(d.InputOffset())
			if _, err := d.Token(); err != nil {
				return nil, err
			}
			keyToken := raw[start:int(d.InputOffset())]
			key := decodeKey(keyToken[bytes.IndexByte(keyToken, '"'):])
			startValue := jsonValueStart(raw, int(d.InputOffset()))
			var item json.RawMessage
			if err := d.Decode(&item); err != nil {
				return nil, err
			}
			v, err := decodeNode(raw[startValue:int(d.InputOffset())])
			if err != nil {
				return nil, err
			}
			object[key] = v
		}
		return object, nil
	case '[':
		d := json.NewDecoder(bytes.NewReader(maskConstants(raw)))
		if _, err := d.Token(); err != nil {
			return nil, err
		}
		array := []any{}
		for d.More() {
			start := jsonValueStart(raw, int(d.InputOffset()))
			var item json.RawMessage
			if err := d.Decode(&item); err != nil {
				return nil, err
			}
			v, err := decodeNode(raw[start:int(d.InputOffset())])
			if err != nil {
				return nil, err
			}
			array = append(array, v)
		}
		return array, nil
	default:
		var v any
		d := json.NewDecoder(bytes.NewReader(raw))
		d.UseNumber()
		if err := d.Decode(&v); err != nil {
			return nil, err
		}
		return jsonAtom{raw, v}, nil
	}
}

// Unpaired key surrogates use WTF-8 internally, preserving their identity until
// a write fails. encoding/json would replace them and silently change the key.
// The complete JSON syntax has already been validated before this decoder runs.
func decodeKey(raw []byte) string {
	var out []byte
	for i := 1; i < len(raw)-1; i++ {
		if raw[i] != '\\' {
			out = append(out, raw[i])
			continue
		}
		i++
		switch raw[i] {
		case '"', '\\', '/':
			out = append(out, raw[i])
		case 'b':
			out = append(out, '\b')
		case 'f':
			out = append(out, '\f')
		case 'n':
			out = append(out, '\n')
		case 'r':
			out = append(out, '\r')
		case 't':
			out = append(out, '\t')
		case 'u':
			unit, _ := strconv.ParseUint(string(raw[i+1:i+5]), 16, 16)
			i += 4
			if unit >= 0xd800 && unit <= 0xdbff && i+7 < len(raw) && string(raw[i+1:i+3]) == `\u` {
				low, err := strconv.ParseUint(string(raw[i+3:i+7]), 16, 16)
				if err == nil && low >= 0xdc00 && low <= 0xdfff {
					out = utf8.AppendRune(out, utf16.DecodeRune(rune(unit), rune(low)))
					i += 6
					continue
				}
			}
			if unit >= 0xd800 && unit <= 0xdfff {
				out = append(out, 0xe0|byte(unit>>12), 0x80|byte(unit>>6)&0x3f, 0x80|byte(unit)&0x3f)
			} else {
				out = utf8.AppendRune(out, rune(unit))
			}
		}
	}
	return string(out)
}

func object(v any, field string) (map[string]any, error) {
	m, ok := value(v).(map[string]any)
	if !ok {
		return nil, &NonObjectError{field}
	}
	return m, nil
}

func orObject(v any, field string) (map[string]any, error) {
	if !truth(v) {
		return map[string]any{}, nil
	}
	return object(v, field)
}

func text(v any) string {
	s, _ := value(v).(string)
	return s
}

func truth(v any) bool {
	switch v := value(v).(type) {
	case nil:
		return false
	case bool:
		return v
	case string:
		return v != ""
	case json.Number:
		if i, ok := integer(v); ok {
			return i.Sign() != 0
		}
		f, _ := strconv.ParseFloat(string(v), 64) // JSON syntax was validated; range overflow is +/-Inf.
		return f != 0
	case map[string]any:
		return len(v) != 0
	case []any:
		return len(v) != 0
	case []string:
		return len(v) != 0
	}
	return true
}

func required(m map[string]any, key string) (any, error) {
	v, ok := m[key]
	if !ok {
		return nil, &KeyError{key}
	}
	return value(v), nil
}

func stringList(v any, field string) ([]string, error) {
	switch a := value(v).(type) {
	case []string:
		return append([]string(nil), a...), nil
	case []any:
		result := make([]string, len(a))
		for i, v := range a {
			s, ok := value(v).(string)
			if !ok {
				return nil, &TypeError{field}
			}
			result[i] = s
		}
		return result, nil
	}
	return nil, &TypeError{field}
}

func path(stateDir, attemptID string) string {
	return filepath.Join(stateDir, "intents", attemptID+".json")
}

func exists(target string) bool {
	_, err := os.Stat(target)
	return err == nil
}

func readBytes(target string) ([]byte, error) {
	if strings.IndexByte(target, 0) >= 0 {
		return nil, &ValueError{"embedded null byte"}
	}
	return os.ReadFile(target)
}

func load(target string) (any, error) {
	data, err := readBytes(target)
	if err != nil {
		return nil, err
	}
	return decode(data)
}

func encode(v any) ([]byte, error) {
	if err := validateKeys(v); err != nil {
		return nil, err
	}
	var b bytes.Buffer
	if err := writeJSON(&b, v); err != nil {
		return nil, err
	}
	b.WriteByte('\n')
	// Encoder still escapes the two JavaScript line separators. Skip escaped
	// backslashes so a literal "\\u2028" extension remains a literal string.
	source := b.Bytes()
	result := make([]byte, 0, len(source))
	for i := 0; i < len(source); i++ {
		if source[i] == '\\' && i+1 < len(source) {
			if i+6 <= len(source) {
				var r rune
				switch strings.ToLower(string(source[i : i+6])) {
				case `\u2028`:
					r = '\u2028'
				case `\u2029`:
					r = '\u2029'
				case `\u003c`:
					r = '<'
				case `\u003e`:
					r = '>'
				case `\u0026`:
					r = '&'
				}
				if r != 0 {
					result = utf8.AppendRune(result, r)
					i += 5
					continue
				}
			}
			result = append(result, source[i], source[i+1])
			i++
			continue
		}
		result = append(result, source[i])
	}
	return result, nil
}

func validateKeys(v any) error {
	switch v := value(v).(type) {
	case map[string]any:
		for key, child := range v {
			if !utf8.ValidString(key) {
				return &ValueError{"lone surrogate key cannot be encoded as UTF-8"}
			}
			if err := validateKeys(child); err != nil {
				return err
			}
		}
	case []any:
		for _, child := range v {
			if err := validateKeys(child); err != nil {
				return err
			}
		}
	}
	return nil
}

func write(target string, journal map[string]any, options Options) (err error) {
	data, err := encode(journal)
	if err != nil {
		return err
	}
	defer func() {
		var filesystem *os.PathError
		if err != nil && !errors.As(err, &filesystem) {
			err = &os.PathError{Op: "publish journal", Path: target, Err: err}
		}
	}()
	dir := filepath.Dir(target)
	if err := os.MkdirAll(dir, 0700); err != nil {
		return err
	}
	file, err := os.CreateTemp(dir, ".intent-*")
	if err != nil {
		return err
	}
	defer func() {
		// Close also covers failures before the explicit close. Removal is only
		// for our temporary file, never the published journal or existing lock.
		_ = file.Close()
		if removeErr := os.Remove(file.Name()); removeErr != nil && !errors.Is(removeErr, os.ErrNotExist) {
			err = errors.Join(err, removeErr)
		}
	}()
	calls := options.io()
	n, err := calls.Write(file, data)
	if err != nil {
		return err
	}
	if n != len(data) {
		return io.ErrShortWrite
	}
	if err := calls.Sync(file); err != nil {
		return err
	}
	if err := calls.Close(file); err != nil {
		return err
	}
	if err := calls.Rename(file.Name(), target); err != nil {
		return err
	}
	directory, err := os.Open(dir)
	if err != nil {
		return err
	}
	syncErr := calls.Sync(directory)
	closeErr := calls.Close(directory)
	if closeErr != nil {
		_ = directory.Close()
	}
	return errors.Join(syncErr, closeErr)
}

func lock(ctx context.Context, target string, wait time.Duration) (*os.File, error) {
	if err := os.MkdirAll(filepath.Dir(target), 0700); err != nil {
		return nil, err
	}
	// Follow Python locks' symlinks and preserve an existing file's mode/inode.
	file, err := os.OpenFile(strings.TrimSuffix(target, ".json")+".lock", os.O_CREATE|os.O_RDWR, 0600)
	if err != nil {
		return nil, err
	}
	deadline := time.Now().Add(wait)
	for {
		err := syscall.Flock(int(file.Fd()), syscall.LOCK_EX|syscall.LOCK_NB)
		if err == nil {
			return file, nil
		}
		if !errors.Is(err, syscall.EWOULDBLOCK) && !errors.Is(err, syscall.EAGAIN) {
			file.Close()
			return nil, err
		}
		remaining := time.Until(deadline)
		if remaining <= 0 {
			file.Close()
			return nil, &Error{Code: "SESSION_BUSY", ExitCode: 7, Message: "another inbox command for this attempt is active"}
		}
		timer := time.NewTimer(min(remaining, 100*time.Millisecond))
		select {
		case <-ctx.Done():
			timer.Stop()
			file.Close()
			return nil, ctx.Err()
		case <-timer.C:
		}
	}
}

func canonicalUUID(v any) (string, error) {
	s, ok := value(v).(string)
	if !ok {
		return "", &ValueError{"UUID is not a string"}
	}
	s = strings.ReplaceAll(strings.ReplaceAll(s, "urn:", ""), "uuid:", "")
	s = strings.ReplaceAll(strings.Trim(s, "{}"), "-", "")
	if len(s) != 32 {
		return "", &ValueError{"badly formed hexadecimal UUID string"}
	}
	b, err := hex.DecodeString(s)
	if err != nil {
		return "", &ValueError{"invalid hexadecimal UUID string"}
	}
	s = hex.EncodeToString(b)
	return s[:8] + "-" + s[8:12] + "-" + s[12:16] + "-" + s[16:20] + "-" + s[20:], nil
}

func integer(v any) (*big.Int, bool) {
	n, ok := value(v).(json.Number)
	if !ok || strings.ContainsAny(string(n), ".eE") {
		return nil, false
	}
	i, ok := new(big.Int).SetString(string(n), 10)
	return i, ok
}

func scalarString(v any) string {
	switch v := value(v).(type) {
	case *jsonConstant:
		if v.token == "NaN" {
			return "nan"
		}
		if v.token == "Infinity" {
			return "inf"
		}
		return "-inf"
	case string:
		return v
	case nil:
		return "None"
	case bool:
		if v {
			return "True"
		}
		return "False"
	case json.Number:
		if i, ok := integer(v); ok {
			return i.String()
		}
		f, err := strconv.ParseFloat(string(v), 64)
		if math.IsNaN(f) {
			return "nan"
		}
		if math.IsInf(f, 0) {
			if f < 0 {
				return "-inf"
			}
			return "inf"
		}
		if err == nil || errors.Is(err, strconv.ErrRange) {
			s := strconv.FormatFloat(f, 'g', -1, 64)
			e := strconv.FormatFloat(f, 'e', -1, 64)
			at := strings.LastIndexByte(e, 'e')
			exponent, _ := strconv.Atoi(e[at+1:]) // FormatFloat always emits an exponent here.
			if exponent >= -4 && exponent < 16 {
				s = strconv.FormatFloat(f, 'f', -1, 64)
			}
			if !strings.ContainsAny(s, ".eE") {
				s += ".0"
			}
			return s
		}
		return string(v)
	}
	return fmt.Sprint(value(v))
}

func numeric(v any) (*big.Rat, bool) {
	switch v := value(v).(type) {
	case bool:
		if v {
			return big.NewRat(1, 1), true
		}
		return new(big.Rat), true
	case json.Number:
		if i, ok := integer(v); ok {
			return new(big.Rat).SetInt(i), true
		}
		f, err := strconv.ParseFloat(string(v), 64)
		if err != nil {
			return nil, false
		}
		r := new(big.Rat).SetFloat64(f)
		return r, r != nil
	}
	return nil, false
}

func equal(a, b any) bool {
	a, b = value(a), value(b)
	if ac, bc := nonFinite(a), nonFinite(b); ac != "" || bc != "" {
		return ac != "NaN" && bc != "NaN" && ac == bc
	}
	if an, ok := numeric(a); ok {
		bn, ok := numeric(b)
		return ok && an.Cmp(bn) == 0
	}
	if am, ok := a.(map[string]any); ok {
		bm, ok := b.(map[string]any)
		if !ok || len(am) != len(bm) {
			return false
		}
		for key, v := range am {
			bv, ok := bm[key]
			if !ok || !containerEqual(v, bv) {
				return false
			}
		}
		return true
	}
	if aa, err := rawList(a); err == nil {
		ba, err := rawList(b)
		if err != nil || len(aa) != len(ba) {
			return false
		}
		for i, v := range aa {
			if !containerEqual(v, ba[i]) {
				return false
			}
		}
		return true
	}
	return reflect.DeepEqual(a, b)
}
