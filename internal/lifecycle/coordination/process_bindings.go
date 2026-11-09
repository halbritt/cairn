package coordination

import (
	"bytes"
	"errors"
	"fmt"
	"math"
	"math/big"
	"os"
	"strconv"
	"strings"
	"syscall"
	"unicode"
	"unicode/utf8"

	"github.com/halbritt/cairn/internal/lifecycle/value"
)

// NativeProcView retains Python integers and the caller's pid representation.
// ProcView remains the typed process-scanning API for its existing consumers.
type NativeProcView struct {
	Ref       *value.Value
	Comm, Exe string
	Argv      []string
}

func pythonRepr(v *value.Value, nested bool) string {
	if v == nil || v.Kind == value.Null {
		return "None"
	}
	switch v.Kind {
	case value.Bool:
		if v.Boolean {
			return "True"
		}
		return "False"
	case value.Int:
		return v.Integer
	case value.Float:
		if math.IsNaN(v.Number) {
			return "nan"
		}
		if math.IsInf(v.Number, 1) {
			return "inf"
		}
		if math.IsInf(v.Number, -1) {
			return "-inf"
		}
		style := byte('f')
		n := math.Abs(v.Number)
		if n != 0 && (n < 1e-4 || n >= 1e16) {
			style = 'e'
		}
		s := strconv.FormatFloat(v.Number, style, -1, 64)
		if !strings.ContainsAny(s, ".e") {
			s += ".0"
		}
		return s
	case value.String:
		if !nested {
			return journalText(v.String)
		}
		quote := rune('\'')
		if strings.ContainsRune(string(v.String), '\'') && !strings.ContainsRune(string(v.String), '"') {
			quote = '"'
		}
		var b strings.Builder
		b.WriteRune(quote)
		for _, r := range v.String {
			switch r {
			case '\\':
				b.WriteString(`\\`)
			case '\n':
				b.WriteString(`\n`)
			case '\r':
				b.WriteString(`\r`)
			case '\t':
				b.WriteString(`\t`)
			default:
				if r == quote {
					b.WriteByte('\\')
					b.WriteRune(r)
				} else if !unicode.IsPrint(r) {
					if r <= 255 {
						fmt.Fprintf(&b, `\x%02x`, r)
					} else if r <= 65535 {
						fmt.Fprintf(&b, `\u%04x`, r)
					} else {
						fmt.Fprintf(&b, `\U%08x`, r)
					}
				} else {
					b.WriteRune(r)
				}
			}
		}
		b.WriteRune(quote)
		return b.String()
	case value.Array:
		parts := make([]string, len(v.Items))
		for i, x := range v.Items {
			parts[i] = pythonRepr(x, true)
		}
		return "[" + strings.Join(parts, ", ") + "]"
	case value.Object:
		parts := make([]string, len(v.Members))
		for i, m := range v.Members {
			parts[i] = pythonRepr(&value.Value{Kind: value.String, String: m.Key}, true) + ": " + pythonRepr(m.Value, true)
		}
		return "{" + strings.Join(parts, ", ") + "}"
	default:
		return ""
	}
}
func procName(v *value.Value) (string, error) {
	points := journalPoints(pythonRepr(v, false))
	var b strings.Builder
	for _, r := range points {
		if r == 0 {
			return "", &value.Error{Class: "ValueError", Message: "embedded null byte"}
		}
		if r >= 0xdc80 && r <= 0xdcff {
			b.WriteByte(byte(r - 0xdc00))
		} else if r >= 0xd800 && r <= 0xdfff {
			return "", &value.Error{Class: "UnicodeEncodeError", Message: "process path surrogate"}
		} else {
			b.WriteRune(r)
		}
	}
	return b.String(), nil
}
func procInteger(text string) (*value.Value, error) {
	var digits strings.Builder
	sign := ""
	points := []rune(text)
	if len(points) > 0 && (points[0] == '+' || points[0] == '-') {
		sign = string(points[0])
		points = points[1:]
	}
	previousDigit := false
	for i, r := range points {
		if r == '_' && previousDigit && i+1 < len(points) {
			previousDigit = false
			continue
		}
		d := -1
		for _, span := range unicode.Nd.R16 {
			if r >= rune(span.Lo) && r <= rune(span.Hi) && (r-rune(span.Lo))%rune(span.Stride) == 0 {
				d = int((r-rune(span.Lo))/rune(span.Stride)) % 10
				break
			}
		}
		if d < 0 {
			for _, span := range unicode.Nd.R32 {
				if r >= rune(span.Lo) && r <= rune(span.Hi) && (r-rune(span.Lo))%rune(span.Stride) == 0 {
					d = int((r-rune(span.Lo))/rune(span.Stride)) % 10
					break
				}
			}
		}
		if d < 0 {
			return nil, &value.Error{Class: "ValueError", Message: "invalid process integer"}
		}
		digits.WriteByte(byte('0' + d))
		previousDigit = true
	}
	n, ok := new(big.Int).SetString(sign+digits.String(), 10)
	if !ok || !previousDigit {
		return nil, &value.Error{Class: "ValueError", Message: "invalid process integer"}
	}
	return value.IntegerText(n.String()), nil
}
func nativeProcessReference(root string, pid *value.Value) (*value.Value, error) {
	name, err := procName(pid)
	if err != nil {
		return nil, err
	}
	raw, err := os.ReadFile(strings.TrimRight(procRoot(root), "/") + "/" + name + "/stat")
	if err != nil {
		return nil, err
	}
	if !utf8.Valid(raw) {
		return nil, &value.Error{Class: "UnicodeDecodeError", Message: "process stat is not UTF-8"}
	}
	end := bytes.LastIndexByte(raw, ')')
	if end < 0 {
		return nil, &invalidProcessStat{cause: &value.Error{Class: "IndexError", Message: "missing process command terminator"}}
	}
	fields := strings.FieldsFunc(string(raw[end+1:]), func(r rune) bool { return unicode.IsSpace(r) || r >= 0x1c && r <= 0x1f })
	if len(fields) < 20 {
		return nil, &invalidProcessStat{cause: &value.Error{Class: "IndexError", Message: "short process stat"}}
	}
	var parent, start *value.Value
	for _, i := range []int{1, 2, 3, 19} {
		n, e := procInteger(fields[i])
		if e != nil {
			return nil, &invalidProcessStat{cause: e}
		}
		if i == 1 {
			parent = n
		}
		if i == 19 {
			start = n
		}
	}
	boot, err := os.ReadFile(strings.TrimRight(procRoot(root), "/") + "/sys/kernel/random/boot_id")
	if err != nil {
		return nil, err
	}
	if !utf8.Valid(boot) {
		return nil, &value.Error{Class: "UnicodeDecodeError", Message: "boot ID is not UTF-8"}
	}
	return nativeMap("pid", pid, "start", start, "state", fields[0], "parent", parent, "boot", pythonStrip(string(boot))), nil
}
func nativeSameProcess(current, recorded *value.Value) (bool, error) {
	for _, key := range []string{"pid", "start", "boot"} {
		left, err := nativeRequired(current, key)
		if err != nil {
			return false, err
		}
		right, err := nativeRequired(recorded, key)
		if err != nil {
			return false, err
		}
		if !nativeEqual(left, right) {
			return false, nil
		}
	}
	return true, nil
}

// NewProcessHooks binds read-only /proc observations. It never signals a process.
func NewProcessHooks(root string) RecoveryHooks {
	alive := func(recorded *value.Value) (bool, error) {
		pid, err := nativeRequired(recorded, "pid")
		if err != nil {
			return false, err
		}
		current, err := nativeProcessReference(root, pid)
		if errors.Is(err, os.ErrNotExist) || errors.Is(err, syscall.ESRCH) {
			return false, nil
		}
		if err != nil {
			return false, err
		}
		if value.SameText(current.Get("state"), "Z") || value.SameText(current.Get("state"), "X") {
			return false, nil
		}
		return nativeSameProcess(current, recorded)
	}
	probe := func(recorded *value.Value) (*NativeProcView, error) {
		pid, err := nativeRequired(recorded, "pid")
		if err != nil {
			return nil, err
		}
		ref, err := nativeProcessReference(root, pid)
		if err != nil {
			return nil, err
		}
		name, err := procName(pid)
		if err != nil {
			return nil, err
		}
		dir := strings.TrimRight(procRoot(root), "/") + "/" + name
		comm, err := os.ReadFile(dir + "/comm")
		if err != nil {
			return nil, err
		}
		if !utf8.Valid(comm) {
			return nil, &invalidProcessText{path: dir + "/comm"}
		}
		exe, _ := os.Readlink(dir + "/exe")
		view := &NativeProcView{Ref: ref, Comm: pythonStrip(string(comm)), Exe: exe, Argv: []string{}}
		if raw, err := os.ReadFile(dir + "/cmdline"); err == nil {
			if len(raw) > 65536 {
				raw = raw[:65536]
			}
			for _, part := range bytes.Split(raw, []byte{0}) {
				if len(part) > 0 {
					view.Argv = append(view.Argv, strings.ToValidUTF8(string(part), "\ufffd"))
				}
			}
		}
		return view, nil
	}
	return RecoveryHooks{Alive: alive, DaemonView: probe, Daemon: func(recorded *value.Value) (bool, error) {
		view, err := probe(recorded)
		if err != nil {
			return false, err
		}
		ok, err := alive(recorded)
		if err != nil || !ok {
			return false, err
		}
		if ClaudeSupervisorRole(ProcView{Argv: view.Argv}) != "daemon" {
			return false, nil
		}
		return nativeSameProcess(view.Ref, recorded)
	}}
}
