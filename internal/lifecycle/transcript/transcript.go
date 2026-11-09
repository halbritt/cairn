// Package transcript verifies pinned native transcript evidence. It does not
// acquire inbox work, grant memory, settle attempts, or mutate session state.
package transcript

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math"
	"os"
	"regexp"
	"strconv"
	"strings"
	"syscall"
	"time"
	"unicode/utf8"

	"github.com/halbritt/cairn/internal/lifecycle/coordination"
	"github.com/halbritt/cairn/internal/lifecycle/value"
	"github.com/halbritt/cairn/internal/lifecycle/wake"
	"github.com/halbritt/cairn/internal/procpeer"
	"golang.org/x/sys/unix"
)

const (
	ClaudeTranscriptLimit     = 256 * 1024 * 1024
	CodexTurnEndLimit         = 8 * 1024 * 1024
	NativeRecordLimit         = 65536
	NativeHookTimeoutSeconds  = 15
	CodexEndedTurnRegionLimit = 8 * 1024 * 1024
	CodexEndedTurnTailLimit   = 64 * 1024 * 1024
	CodexRefusalScanDuration  = time.Second
	NativeRefusalMessage      = "Cairn coordination: NATIVE_PROMPT_REFUSED: bound inbox recall unavailable; no source or memory grant delivered; channel text rejected, owner prompt continues"
	UUIDText                  = `^[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}$`
)

var (
	uuidPattern        = regexp.MustCompile(UUIDText)
	locatorPattern     = regexp.MustCompile(`^(0|[1-9][0-9]{0,15}):([1-9][0-9]{0,5}):([0-9a-f]{64})$`)
	digestPattern      = regexp.MustCompile(`^[0-9a-f]{64}$`)
	nativeTimePattern  = regexp.MustCompile(`^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$`)
	NativeRefusalKeys  = []string{"parentUuid", "isSidechain", "type", "subtype", "content", "isMeta", "timestamp", "uuid", "level", "preventContinuation", "userType", "entrypoint", "cwd", "sessionId", "version", "gitBranch", "slug"}
	CodexQueueOutcomes = []string{"admitted_active", "completed", "cancelled", "never_admitted", "unknown"}
)

type TranscriptError struct {
	Code, Message string
	Cause         error
}

func (e *TranscriptError) Error() string { return fmt.Sprintf("%s: %s", e.Code, e.Message) }
func (e *TranscriptError) Unwrap() error { return e.Cause }
func invalid(message string, cause ...error) error {
	e := &TranscriptError{Code: "INVALID_REQUEST", Message: message}
	if len(cause) > 0 {
		e.Cause = cause[0]
	}
	return e
}

type Locator struct {
	Offset int64  `json:"offset"`
	Length int64  `json:"length"`
	SHA256 string `json:"sha256"`
}
type ClaudeConfig struct {
	Harness, ConfigHome, ConfigPath, HomeDir string
	HasProcessExeDirs                        bool
	ProcessExeDirs                           []string
	Runtime                                  *SourceRuntime
	ConfigHomePoints, ConfigPathPoints       []rune
}
type CodexConfig struct {
	Harness, ConfigHome, HomeDir string
	Runtime                      *SourceRuntime
	ConfigHomePoints             []rune
}
type SessionState struct {
	NativeSessionID, Workspace, AgentID, ExecutionID string
	Process                                          procpeer.ProcessRef
	WorkspacePoints                                  []rune
	Raw                                              *Record
}
type Delivery struct {
	DeliveryID, State string
	Attempts          int
}
type Attempt struct {
	CreatedAt    any
	NativeTurnID string
	Delivery     Delivery
}
type Wake struct {
	AttemptedAt                    any
	DeliveryID, QueuedSubmissionID string
	Session                        wake.SessionRef
	Raw                            *Record
}
type NativeRecord struct {
	Record  *Record
	Locator Locator
}

// Reader makes the process root, owner identity and cooperative scan clock
// explicit. Construct with NewReader, then override fields for private fixtures.
type Reader struct {
	Profile      *value.SourceReaderProfile
	Runtime      *SourceRuntime
	ProcRoot     string
	ExpectedUID  uint32
	Alive        func(procpeer.ProcessRef) bool
	Now          func() time.Time
	ScanDuration time.Duration
}

func NewReader(profile *value.SourceReaderProfile) Reader {
	var runtime *SourceRuntime
	if profile != nil && profile.Routes != nil {
		if provider, ok := profile.Routes.(interface{ TranscriptRuntime() *SourceRuntime }); ok {
			runtime = provider.TranscriptRuntime()
		}
	}
	return Reader{Profile: profile, Runtime: runtime, ProcRoot: "/proc", ExpectedUID: uint32(os.Getuid()), Alive: wake.ProcessAlive, Now: time.Now, ScanDuration: CodexRefusalScanDuration}
}

func ParseLocator(text string) (Locator, error) {
	m := locatorPattern.FindStringSubmatch(text)
	if m == nil {
		return Locator{}, invalid("native record locator must be OFFSET:LENGTH:SHA256 within bounds")
	}
	o, _ := strconv.ParseInt(m[1], 10, 64)
	n, _ := strconv.ParseInt(m[2], 10, 64)
	l := Locator{o, n, m[3]}
	if !validLocator(l) {
		return Locator{}, invalid("native record locator must be OFFSET:LENGTH:SHA256 within bounds")
	}
	return l, nil
}
func validLocator(l Locator) bool {
	return l.Offset >= 0 && l.Length > 0 && l.Length <= NativeRecordLimit && l.Offset <= math.MaxInt64-l.Length && digestPattern.MatchString(l.SHA256)
}
func pin(raw []byte, offset int64) Locator {
	h := sha256.Sum256(raw)
	return Locator{offset, int64(len(raw)), hex.EncodeToString(h[:])}
}
func NativeTime(input any) (time.Time, error) {
	s, ok := input.(string)
	if node, yes := input.(*Record); yes {
		if node != nil && node.Kind == value.String {
			s, ok = text(node), true
		} else {
			ok = false
		}
	}
	if !ok || !nativeTimePattern.MatchString(s) {
		return time.Time{}, invalid("native record timestamp shape is unknown")
	}
	t, e := time.Parse("2006-01-02T15:04:05.000Z", s)
	if e != nil {
		return time.Time{}, invalid("native record timestamp shape is unknown", e)
	}
	return t, nil
}

// SourceRuntime is the explicit consumer's datetime/filesystem dependency.
// It selects no ambient timezone, locale, Python version, or production domain.
// Unavailable routes escape as ProfileUnavailable rather than invalid data.
type ClaimSite string

const (
	ClaimDaemon       ClaimSite = "daemon_start_claim"
	ClaimNative       ClaimSite = "native_refusal"
	ClaimCodexRefused ClaimSite = "codex_refusal"
	ClaimCodexEnded   ClaimSite = "codex_ended"
)

type ClaimInstant struct {
	Seconds float64
	Aware   bool
}
type SourceRuntime struct {
	ParseClaim func(ClaimSite, *value.Value) (ClaimInstant, error)
	Filesystem value.FilesystemRoutes
}

func claimTime(runtime *SourceRuntime, site ClaimSite, input any) (ClaimInstant, error) {
	var raw *value.Value
	switch v := input.(type) {
	case *value.Value:
		raw = v
	case string:
		raw = value.Text(v)
	case nil:
	default:
		return ClaimInstant{}, &value.Error{Class: "TypeError", Message: "claim time has an unsupported host representation"}
	}
	var parsed ClaimInstant
	var err error
	if raw == nil {
		err = &value.Error{Class: "KeyError", Message: "created_at"}
	} else if runtime == nil || runtime.ParseClaim == nil {
		err = &value.ProfileUnavailable{}
	} else {
		parsed, err = runtime.ParseClaim(site, raw)
	}
	if err != nil {
		if site == ClaimNative && sourceReadError(err, "KeyError", "TypeError", "ValueError") {
			return ClaimInstant{}, invalid("held attempt has no claim time", err)
		}
		return ClaimInstant{}, err
	}
	if !parsed.Aware {
		if site == ClaimNative {
			return ClaimInstant{}, invalid("native chronology does not bracket the single claim of this delivery")
		}
	}
	return parsed, nil
}
func epoch(t time.Time) float64 { return float64(t.Unix()) + float64(t.Nanosecond())/1e9 }
func exactInt(v any) (int64, bool) {
	switch n := v.(type) {
	case *Record:
		if n == nil || n.Kind != value.Int {
			return 0, false
		}
		i, e := strconv.ParseInt(n.Integer, 10, 64)
		return i, e == nil
	case json.Number:
		if strings.ContainsAny(string(n), ".eE") {
			return 0, false
		}
		i, e := strconv.ParseInt(string(n), 10, 64)
		return i, e == nil
	case int:
		return int64(n), true
	case int64:
		return n, true
	}
	return 0, false
}

// numberSeconds mirrors each Source numeric predicate. Queue attempted_at uses
// isinstance(int,float), which includes bool; refusal at uses exact type(...).
// Nonfinite values pass the type check and retain IEEE comparison behavior.
func numberSeconds(v any, allowBool bool) (float64, bool) {
	var f float64
	var e error
	switch n := v.(type) {
	case *Record:
		if n == nil {
			return 0, false
		}
		switch n.Kind {
		case value.Int:
			f, e = strconv.ParseFloat(n.Integer, 64)
		case value.Float:
			f = n.Number
		case value.Bool:
			if !allowBool {
				return 0, false
			}
			if n.Boolean {
				return 1, true
			}
			return 0, true
		default:
			return 0, false
		}
	case json.Number:
		f, e = strconv.ParseFloat(string(n), 64)
	case float64:
		f = n
	case int:
		f = float64(n)
	case int64:
		f = float64(n)
	case bool:
		if !allowBool {
			return 0, false
		}
		if n {
			return 1, true
		}
		return 0, true
	default:
		return 0, false
	}
	return f, e == nil || errors.Is(e, strconv.ErrRange)
}

// openOwner implements the labelled J-06 FIFO repair. Never block opening an
// unclassified node; clear NONBLOCK only after fstat proves an owned regular file.
func openOwner(path string, uid uint32) (*os.File, syscall.Stat_t, error) {
	var st syscall.Stat_t
	fd, e := syscall.Open(path, syscall.O_RDONLY|syscall.O_NOFOLLOW|syscall.O_NONBLOCK|syscall.O_CLOEXEC, 0)
	if e != nil {
		return nil, st, e
	}
	closeOnError := func(err error) (*os.File, syscall.Stat_t, error) { syscall.Close(fd); return nil, st, err }
	if e = syscall.Fstat(fd, &st); e != nil {
		return closeOnError(e)
	}
	if st.Mode&syscall.S_IFMT != syscall.S_IFREG || st.Uid != uid {
		return closeOnError(&ownerValidationError{})
	}
	flags, e := unix.FcntlInt(uintptr(fd), unix.F_GETFL, 0)
	if e != nil {
		return closeOnError(e)
	}
	if _, e = unix.FcntlInt(uintptr(fd), unix.F_SETFL, flags&^unix.O_NONBLOCK); e != nil {
		return closeOnError(e)
	}
	return os.NewFile(uintptr(fd), path), st, nil
}
func readAt(f *os.File, offset, length int64) ([]byte, error) {
	return readAtFrom(offset, length, func(raw []byte, at int64) (int, error) {
		n, e := syscall.Pread(int(f.Fd()), raw, at)
		if e != nil {
			return n, &os.PathError{Op: "pread", Path: f.Name(), Err: e}
		}
		return n, nil
	})
}

// A short pread returns bytes, not an invented I/O exception. Each caller owns
// its Source length/framing policy; real filesystem errors escape unchanged.
func readAtFrom(offset, length int64, read func([]byte, int64) (int, error)) ([]byte, error) {
	raw := make([]byte, int(length))
	n, e := read(raw, offset)
	if e != nil && !errors.Is(e, io.EOF) {
		return nil, e
	}
	if n < 0 || n > len(raw) {
		return nil, errors.New("invalid positional read count")
	}
	return raw[:n], nil
}

type ownerValidationError struct{}

func (*ownerValidationError) Error() string { return "not an owner-controlled regular file" }
func ownerRefusal(err error, message string) error {
	var validation *ownerValidationError
	if errors.As(err, &validation) {
		return invalid(message, err)
	}
	return err
}
func ReadNativeRecord(path string, l Locator, profile *value.SourceReaderProfile) (*Record, error) {
	return ReadNativeRecordAt(path, l, uint32(os.Getuid()), profile)
}
func ReadNativeRecordAt(path string, l Locator, uid uint32, profile *value.SourceReaderProfile) (*Record, error) {
	if !validLocator(l) {
		return nil, invalid("native record locator must be OFFSET:LENGTH:SHA256 within bounds")
	}
	f, st, e := openOwner(path, uid)
	if e != nil {
		return nil, ownerRefusal(e, "native session file is not a readable owner record source")
	}
	defer f.Close()
	if l.Offset > st.Size || l.Length > st.Size-l.Offset {
		return nil, invalid("native session file is not a readable owner record source")
	}
	raw, e := readAt(f, l.Offset, l.Length)
	if e != nil {
		return nil, e
	}
	if int64(len(raw)) != l.Length || pin(raw, l.Offset) != l || !bytes.HasSuffix(raw, []byte{'\n'}) || bytes.Contains(raw[:max(0, len(raw)-1)], []byte{'\n'}) {
		return nil, invalid("native record bytes differ from their pin or are not one record")
	}
	return StrictNativeLine(raw, profile)
}
func contiguous(ls []Locator) bool {
	for i, l := range ls {
		if !validLocator(l) || (i > 0 && ls[i-1].Offset+ls[i-1].Length != l.Offset) {
			return false
		}
	}
	return true
}
func LastJSONLRecord(path string, profile *value.SourceReaderProfile) (*Record, error) {
	return lastJSONLRecordAt(path, uint32(os.Getuid()), NativeRecordLimit, profile)
}
func lastJSONLRecordAt(path string, uid uint32, window int64, profile *value.SourceReaderProfile) (*Record, error) {
	f, st, e := openOwner(path, uid)
	if e != nil {
		return nil, nil
	}
	defer f.Close()
	base := max(int64(0), st.Size-window)
	raw, e := readAt(f, base, st.Size-base)
	if e != nil {
		return nil, nil
	}
	lines := [][]byte{}
	for _, line := range bytes.Split(raw, []byte{'\n'}) {
		if len(bytes.Trim(line, " \t\n\r\v\f")) > 0 {
			lines = append(lines, line)
		}
	}
	if len(lines) == 0 || (st.Size > window && len(lines) == 1) {
		return nil, nil
	}
	v, e := ParsePlainBytesJSONObject(lines[len(lines)-1], profile)
	if e != nil {
		var te *TranscriptError
		if errors.As(e, &te) || sourceReadError(e, "UnicodeDecodeError", "ValueError", "JSONDecodeError") {
			return nil, nil
		}
		return nil, e
	}
	return v, nil
}

// RecoveryEvidence adapts this verifier to coordination's dependency-inverted
// orchestration boundary. It performs no settlement, store call or state write.
func (r Reader) RecoveryEvidence() coordination.TranscriptEvidence { return readerEvidence{r} }

type readerEvidence struct{ reader Reader }

func sourceFieldText(v *Record, key string, optional bool) (string, error) {
	field := v.Get(key)
	if field == nil {
		if optional {
			return "", nil
		}
		return "", &value.Error{Class: "KeyError", Message: key}
	}
	if field.Kind != value.String {
		return "", &value.Error{Class: "TypeError", Message: key + " must be a string"}
	}
	for _, r := range field.String {
		if !utf8.ValidRune(r) {
			return "", &value.ProfileUnavailable{}
		}
	}
	return string(field.String), nil
}
func sourcePathField(runtime *SourceRuntime, v *Record, key string, optional bool) (string, error) {
	field := v.Get(key)
	if field == nil {
		if optional {
			return "", nil
		}
		return "", &value.Error{Class: "KeyError", Message: key}
	}
	if field.Kind != value.String {
		return "", &value.Error{Class: "TypeError", Message: key + " must be a path string"}
	}
	if runtime == nil || runtime.Filesystem == nil {
		return "", &value.ProfileUnavailable{}
	}
	return runtime.Filesystem.Encode(field.String)
}
func sourceProcess(v *Record) (procpeer.ProcessRef, error) {
	if v == nil || v.Kind != value.Object {
		return procpeer.ProcessRef{}, &value.Error{Class: "TypeError", Message: "process must be an object"}
	}
	pid, ok := exactInt(v.Get("pid"))
	if !ok {
		return procpeer.ProcessRef{}, &value.Error{Class: "TypeError", Message: "process pid must be an integer"}
	}
	start := v.Get("start")
	if start == nil || start.Kind != value.Int {
		return procpeer.ProcessRef{}, &value.Error{Class: "TypeError", Message: "process start must be an integer"}
	}
	n, err := strconv.ParseUint(start.Integer, 10, 64)
	if err != nil {
		return procpeer.ProcessRef{}, &value.Error{Class: "ValueError", Message: "process start is out of range"}
	}
	boot, err := sourceFieldText(v, "boot", false)
	if err != nil {
		return procpeer.ProcessRef{}, err
	}
	return procpeer.ProcessRef{PID: int(pid), Start: n, Boot: boot}, nil
}
func processRecord(p procpeer.ProcessRef) *Record {
	return value.Mapping(value.Field("pid", value.IntegerText(strconv.Itoa(p.PID))), value.Field("start", value.IntegerText(strconv.FormatUint(p.Start, 10))), value.Field("boot", value.Text(p.Boot)))
}
func sourceLocators(raw *Record) ([]Locator, error) {
	if raw == nil || raw.Kind == value.Null {
		return nil, nil
	}
	if raw.Kind != value.Array {
		return nil, invalid("native record locator must be OFFSET:LENGTH:SHA256 within bounds")
	}
	ls := make([]Locator, 0, len(raw.Items))
	for _, v := range raw.Items {
		if v != nil && v.Kind == value.String {
			l, err := ParseLocator(text(v))
			if err != nil {
				return nil, err
			}
			ls = append(ls, l)
			continue
		}
		if v == nil || v.Kind != value.Object {
			return nil, invalid("native record locator must be OFFSET:LENGTH:SHA256 within bounds")
		}
		offset, ok := exactInt(v.Get("offset"))
		length, okLength := exactInt(v.Get("length"))
		l := Locator{Offset: offset, Length: length, SHA256: text(v.Get("sha256"))}
		if !ok || !okLength || !validLocator(l) {
			return nil, invalid("native record locator must be OFFSET:LENGTH:SHA256 within bounds")
		}
		ls = append(ls, l)
	}
	return ls, nil
}
func locatorsRecord(ls []Locator) *Record {
	if ls == nil {
		return &value.Value{Kind: value.Null}
	}
	out := &value.Value{Kind: value.Array}
	for _, l := range ls {
		out.Items = append(out.Items, value.Mapping(value.Field("offset", value.IntegerText(strconv.FormatInt(l.Offset, 10))), value.Field("length", value.IntegerText(strconv.FormatInt(l.Length, 10))), value.Field("sha256", value.Text(l.SHA256))))
	}
	return out
}
func proofRecord(kind coordination.EvidenceKind, m map[string]any) (*Record, error) {
	orders := map[coordination.EvidenceKind][]string{
		coordination.EvidenceDaemonClaim:   {"last_turn_record_at", "records_after", "transcript_bytes"},
		coordination.EvidenceNativeRefusal: {"refusal_uuid", "refused_at", "claude_version", "records"},
		coordination.EvidenceCodexRefusal:  {"schema", "records", "native_session_id", "native_turn_id"},
		coordination.EvidenceCodexEnded:    {"schema", "records", "native_session_id", "native_turn_id", "codex_error_info", "ended_at"},
		coordination.EvidenceCodexQueue:    {"schema", "outcome", "native_session_id", "native_turn_id", "records"},
	}
	out := value.Mapping()
	for _, key := range orders[kind] {
		v, present := m[key]
		if !present {
			continue
		}
		var node *value.Value
		switch x := v.(type) {
		case *value.Value:
			node = x
		case string:
			node = value.Text(x)
		case int:
			node = value.IntegerText(strconv.Itoa(x))
		case int64:
			node = value.IntegerText(strconv.FormatInt(x, 10))
		case []Locator:
			node = locatorsRecord(x)
		case nil:
			node = &value.Value{Kind: value.Null}
		default:
			return nil, &value.ProfileUnavailable{}
		}
		out.Set(key, node)
	}
	return out, nil
}
func (a readerEvidence) Read(_ context.Context, kind coordination.EvidenceKind, req coordination.EvidenceRequest) (*value.Value, error) {
	r := a.reader
	agent := req.State.Get("agent")
	native, err := sourceFieldText(agent, "native_session_id", false)
	if err != nil {
		return nil, err
	}
	workspace := ""
	var workspacePoints []rune
	if raw := req.State.Get("workspace"); raw != nil && raw.Kind == value.String {
		workspacePoints = raw.String
		workspace = text(raw)
	}
	var process procpeer.ProcessRef
	if kind == coordination.EvidenceDaemonWorker || kind == coordination.EvidenceCodexRefusal || kind == coordination.EvidenceCodexEnded || kind == coordination.EvidenceCodexQueue || kind == coordination.EvidenceCodexLocators || kind == coordination.EvidenceCodexLatest {
		process, err = sourceProcess(req.State.Get("process"))
		if err != nil {
			return nil, err
		}
	}
	agentID, execution := "", ""
	state := SessionState{NativeSessionID: native, Workspace: workspace, WorkspacePoints: workspacePoints, Raw: req.State, AgentID: agentID, ExecutionID: execution, Process: process}
	harness, err := sourceFieldText(req.Config, "harness", false)
	if err != nil {
		return nil, err
	}
	home := ""
	if raw := req.Config.Get("config_home"); raw != nil && raw.Kind == value.String {
		home, err = sourcePathField(r.Runtime, req.Config, "config_home", false)
		if err != nil {
			return nil, err
		}
	}
	configPath, err := sourcePathField(r.Runtime, req.Config, "config_path", true)
	if err != nil {
		return nil, err
	}
	claude := ClaudeConfig{Harness: harness, ConfigHome: home, ConfigPath: configPath, Runtime: r.Runtime}
	codex := CodexConfig{Harness: harness, ConfigHome: home, Runtime: r.Runtime}
	if raw := req.Config.Get("config_home"); raw != nil && raw.Kind == value.String {
		claude.ConfigHomePoints = raw.String
		codex.ConfigHomePoints = raw.String
	}
	if raw := req.Config.Get("config_path"); raw != nil {
		claude.ConfigPathPoints = raw.String
	}
	attempt := Attempt{CreatedAt: req.Attempt.Get("created_at")}
	if req.Attempt != nil {
		attempt.NativeTurnID, err = sourceFieldText(req.Attempt, "native_turn_id", true)
		if err != nil {
			return nil, err
		}
		if delivery := req.Attempt.Get("delivery"); delivery != nil && delivery.Kind == value.Object {
			attempt.Delivery.DeliveryID, err = sourceFieldText(delivery, "delivery_id", true)
			if err != nil {
				return nil, err
			}
			attempt.Delivery.State, err = sourceFieldText(delivery, "state", true)
			if err != nil {
				return nil, err
			}
			if sameRecord(delivery.Get("attempts"), &value.Value{Kind: value.Int, Integer: "1"}) {
				attempt.Delivery.Attempts = 1
			}
		}
	}
	locators, err := sourceLocators(req.Locators)
	if err != nil {
		return nil, err
	}
	var result map[string]any
	switch kind {
	case coordination.EvidenceDaemonWorker:
		path, err := claudePath(claude, state)
		if err != nil {
			return nil, err
		}
		if dirs := req.Config.Get("process_exe_dirs"); dirs != nil {
			claude.HasProcessExeDirs = true
			if dirs.Kind != value.Array {
				return nil, &value.Error{Class: "TypeError", Message: "process_exe_dirs"}
			}
			for _, dir := range dirs.Items {
				if r.Runtime == nil || r.Runtime.Filesystem == nil {
					return nil, &value.ProfileUnavailable{}
				}
				if dir == nil || dir.Kind != value.String {
					return nil, &value.Error{Class: "TypeError", Message: "process_exe_dirs"}
				}
				encoded, err := r.Runtime.Filesystem.Encode(dir.String)
				if err != nil {
					return nil, err
				}
				claude.ProcessExeDirs = append(claude.ProcessExeDirs, encoded)
			}
		} else if harness == "claude" {
			if r.Runtime == nil || r.Runtime.Filesystem == nil {
				return nil, &value.ProfileUnavailable{}
			}
			points, err := r.Runtime.Filesystem.Home()
			if err != nil {
				return nil, err
			}
			claude.HomeDir, err = r.Runtime.Filesystem.Encode(points)
			if err != nil {
				return nil, err
			}
		}
		worker, err := findDaemonWorkerViewAt(r.ProcRoot, claude, state, path)
		if err != nil {
			return nil, err
		}
		owner := worker.OwnerRecord()
		return value.Mapping(value.Field("pid", value.IntegerText(strconv.Itoa(owner.PID))), value.Field("start", value.IntegerText(strconv.FormatUint(owner.Start, 10))), value.Field("state", value.Text(owner.State)), value.Field("parent", value.IntegerText(strconv.Itoa(owner.Parent))), value.Field("boot", value.Text(owner.Boot))), nil
	case coordination.EvidenceDaemonClaim:
		result, err = r.DaemonStartClaimEvidence(claude, state, attempt)
	case coordination.EvidenceNativeRefusal:
		result, err = r.NativeRefusalProof(claude, state, attempt, locators)
	case coordination.EvidenceCodexRefusal:
		result, err = r.CodexRefusalProof(codex, state, attempt, map[string]any{"at": req.Recorded.Get("at")}, locators)
	case coordination.EvidenceCodexEnded:
		result, err = r.CodexEndedTurnProof(codex, state, attempt, locators)
	case coordination.EvidenceCodexQueue:
		w := Wake{AttemptedAt: req.Wake.Get("attempted_at"), Raw: req.Wake}
		result, err = r.CodexQueueRolloutProof(codex, state, w, locators)
	case coordination.EvidenceCodexLocators:
		ls, err := r.FindCodexRefusalLocators(codex, state, req.TurnID)
		return locatorsRecord(ls), err
	case coordination.EvidenceCodexLatest:
		last, err := r.LatestCodexTurnEnd(codex, state)
		if err != nil || last == nil {
			return nil, err
		}
		return &value.Value{Kind: value.Array, Items: []*value.Value{last.Record, locatorsRecord([]Locator{last.Locator}).Items[0]}}, nil
	default:
		return nil, invalid("unsupported transcript evidence kind")
	}
	if err != nil {
		return nil, err
	}
	return proofRecord(kind, result)
}

func workspaceMatches(v *Record, state SessionState) (bool, error) {
	if state.Raw != nil {
		workspace := state.Raw.Get("workspace")
		if workspace == nil {
			return false, &value.Error{Class: "KeyError", Message: "workspace"}
		}
		return sameRecord(v, workspace), nil
	}
	return stringIs(v, state.Workspace), nil
}
