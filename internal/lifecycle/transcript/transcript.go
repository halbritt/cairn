// Package transcript verifies pinned native transcript evidence. It does not
// acquire inbox work, grant memory, settle attempts, or mutate session state.
package transcript

import (
	"bytes"
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
}
type CodexConfig struct{ Harness, ConfigHome, HomeDir string }
type SessionState struct {
	NativeSessionID, Workspace, AgentID, ExecutionID string
	Process                                          procpeer.ProcessRef
}
type Delivery struct {
	DeliveryID, State string
	Attempts          int
}
type Attempt struct {
	CreatedAt, NativeTurnID string
	Delivery                Delivery
}
type Wake struct {
	AttemptedAt                    any
	DeliveryID, QueuedSubmissionID string
	Session                        wake.SessionRef
}
type NativeRecord struct {
	Record  *Record
	Locator Locator
}

// Reader makes the process root, owner identity and cooperative scan clock
// explicit. Construct with NewReader, then override fields for private fixtures.
type Reader struct {
	Profile      *value.SourceReaderProfile
	ProcRoot     string
	ExpectedUID  uint32
	Alive        func(procpeer.ProcessRef) bool
	Now          func() time.Time
	ScanDuration time.Duration
}

func NewReader(profile *value.SourceReaderProfile) Reader {
	return Reader{Profile: profile, ProcRoot: "/proc", ExpectedUID: uint32(os.Getuid()), Alive: wake.ProcessAlive, Now: time.Now, ScanDuration: CodexRefusalScanDuration}
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
func claimTime(s string) (float64, error) {
	t, e := time.Parse(time.RFC3339Nano, s)
	if e != nil {
		return 0, invalid("held attempt has no claim time", e)
	}
	return epoch(t), nil
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
		return closeOnError(errors.New("not an owner-controlled regular file"))
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
	raw := make([]byte, int(length))
	n, e := f.ReadAt(raw, offset)
	if n != len(raw) {
		return nil, io.ErrUnexpectedEOF
	}
	if e != nil && e != io.EOF {
		return nil, e
	}
	return raw, nil
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
		return nil, invalid("native session file is not a readable owner record source", e)
	}
	defer f.Close()
	if l.Offset > st.Size || l.Length > st.Size-l.Offset {
		return nil, invalid("native session file is not a readable owner record source")
	}
	raw, e := readAt(f, l.Offset, l.Length)
	if e != nil || pin(raw, l.Offset) != l || !bytes.HasSuffix(raw, []byte{'\n'}) || bytes.Contains(raw[:max(0, len(raw)-1)], []byte{'\n'}) {
		return nil, invalid("native record bytes differ from their pin or are not one record", e)
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
		if len(bytes.TrimSpace(line)) > 0 {
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
