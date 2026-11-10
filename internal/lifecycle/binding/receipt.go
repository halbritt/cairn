package binding

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"syscall"
	"unicode/utf16"

	"github.com/halbritt/cairn/internal/jsontext"
)

const compatibilitySchema = "cairn.lifecycle-compatibility/1"
const binaryBindingSchema = "cairn.inbox-recall-binding/3"
const receiptLimit = 1024 * 1024
const memberLimit = 2 * 1024 * 1024

var closureMembers = [...]string{
	"lifecycle_bundle.py", "memory.py", "coordination.py", "inbox_recall.py", "inbox_binding.py",
	"grant_ledger.py", "transcript_recovery.py", "unix_peer_transport.py", "wake_transport.py",
	"process_scan.py", "codex_queue.py", "claude_channel.py", "opencode_queue.py", "hermes_queue.py",
	"recall_dispatch.py", "hook_admission.py", "native_session.py", "lifecycle_compatibility.py",
}

type fileDescriptor struct {
	Path   string `json:"path"`
	SHA256 string `json:"sha256"`
}

type memberDescriptor struct {
	SHA256 string `json:"sha256"`
	Bytes  int64  `json:"bytes"`
}

type bindingDescriptor struct {
	fileDescriptor
	Schema string `json:"schema"`
}

// compatibilityReceipt is code-selection metadata. Loading it never reads the
// binding or downstream configuration data; those reads follow source admission.
type compatibilityReceipt struct {
	Schema              string                      `json:"schema"`
	SourceRevision      string                      `json:"source_revision"`
	SnapshotRoot        string                      `json:"snapshot_root"`
	Members             map[string]memberDescriptor `json:"members"`
	Bootstrap           string                      `json:"bootstrap"`
	Loader              string                      `json:"loader"`
	Roles               map[string]string           `json:"roles"`
	Aliases             map[string]string           `json:"aliases"`
	Binary              fileDescriptor              `json:"binary"`
	Binding             bindingDescriptor           `json:"binding"`
	MemoryConfig        fileDescriptor              `json:"memory_config"`
	CoordinationConfigs []fileDescriptor            `json:"coordination_configs"`
}

func digest(raw []byte) string {
	sum := sha256.Sum256(raw)
	return hex.EncodeToString(sum[:])
}

func validDigest(s string) bool {
	return len(s) == 64 && strings.Trim(s, "0123456789abcdef") == ""
}

func canonicalPath(s string) bool {
	return filepath.IsAbs(s) && filepath.Clean(s) == s && !strings.ContainsRune(s, 0)
}

func validDescriptor(d fileDescriptor) bool { return canonicalPath(d.Path) && validDigest(d.SHA256) }

func exactObject(raw []byte, names ...string) (map[string]json.RawMessage, error) {
	var fields map[string]json.RawMessage
	if err := json.Unmarshal(raw, &fields); err != nil || fields == nil {
		return nil, errors.New("receipt field must be an object")
	}
	if len(fields) != len(names) {
		return nil, errors.New("receipt fields differ from the selected schema")
	}
	for _, name := range names {
		if _, ok := fields[name]; !ok {
			return nil, errors.New("receipt fields differ from the selected schema")
		}
	}
	return fields, nil
}

// checkReceiptJSON uses the new receipt's strict grammar, never the legacy
// event/state grammar. Duplicate keys and lossy Unicode decoding are refused.
func checkReceiptJSON(raw []byte) error {
	if err := jsontext.CheckUnicode(raw); err != nil {
		return err
	}
	d := json.NewDecoder(bytes.NewReader(raw))
	d.UseNumber()
	var walk func() error
	walk = func() error {
		token, err := d.Token()
		if err != nil {
			return err
		}
		switch token {
		case json.Delim('{'):
			seen := make(map[string]bool)
			for d.More() {
				key, err := d.Token()
				if err != nil {
					return err
				}
				name, ok := key.(string)
				if !ok || seen[name] {
					return errors.New("duplicate or invalid receipt key")
				}
				seen[name] = true
				if err := walk(); err != nil {
					return err
				}
			}
			end, err := d.Token()
			if err != nil || end != json.Delim('}') {
				return errors.New("invalid receipt object")
			}
		case json.Delim('['):
			for d.More() {
				if err := walk(); err != nil {
					return err
				}
			}
			end, err := d.Token()
			if err != nil || end != json.Delim(']') {
				return errors.New("invalid receipt array")
			}
		}
		return nil
	}
	if err := walk(); err != nil {
		return err
	}
	if _, err := d.Token(); err != io.EOF {
		return errors.New("receipt has trailing data")
	}
	return nil
}

func decodeCompatibilityReceipt(raw []byte) (*compatibilityReceipt, error) {
	if len(raw) > receiptLimit {
		return nil, errors.New("compatibility receipt exceeds limit")
	}
	if err := checkReceiptJSON(raw); err != nil {
		return nil, err
	}
	fields, err := exactObject(raw, "schema", "source_revision", "snapshot_root", "members", "bootstrap", "loader", "roles", "aliases", "binary", "binding", "memory_config", "coordination_configs")
	if err != nil {
		return nil, err
	}
	for _, key := range []string{"binary", "memory_config"} {
		if _, err := exactObject(fields[key], "path", "sha256"); err != nil {
			return nil, err
		}
	}
	if _, err := exactObject(fields["binding"], "path", "sha256", "schema"); err != nil {
		return nil, err
	}
	if _, err := exactObject(fields["roles"], "memory", "coordination", "inbox_recall", "inbox_binding"); err != nil {
		return nil, err
	}
	var configs []json.RawMessage
	if err := json.Unmarshal(fields["coordination_configs"], &configs); err != nil {
		return nil, err
	}
	for _, config := range configs {
		if _, err := exactObject(config, "path", "sha256"); err != nil {
			return nil, err
		}
	}
	var members map[string]json.RawMessage
	if err := json.Unmarshal(fields["members"], &members); err != nil {
		return nil, err
	}
	for _, member := range members {
		if _, err := exactObject(member, "sha256", "bytes"); err != nil {
			return nil, err
		}
	}
	var r compatibilityReceipt
	if err := json.Unmarshal(raw, &r); err != nil {
		return nil, err
	}
	if r.Schema != compatibilitySchema || r.Binding.Schema != binaryBindingSchema ||
		r.Bootstrap != "lifecycle_compatibility.py" || r.Loader != "lifecycle_bundle.py" ||
		!canonicalPath(r.SnapshotRoot) || !validDescriptor(r.Binary) || !validDescriptor(r.Binding.fileDescriptor) ||
		!validDescriptor(r.MemoryConfig) || len(r.CoordinationConfigs) < 1 || len(r.CoordinationConfigs) > 8 {
		return nil, errors.New("incompatible receipt selection")
	}
	if r.Aliases == nil {
		return nil, errors.New("receipt aliases must be an object")
	}
	for _, d := range r.CoordinationConfigs {
		if !validDescriptor(d) {
			return nil, errors.New("invalid coordination configuration descriptor")
		}
	}
	for role, filename := range r.Roles {
		if filename != role+".py" {
			return nil, errors.New("receipt role differs from its canonical member")
		}
	}
	expected := make(map[string]bool)
	for _, name := range closureMembers {
		expected[name] = true
	}
	for alias, role := range r.Aliases {
		if alias == "" || filepath.Base(alias) != alias || alias == "." || alias == ".." ||
			strings.ContainsAny(alias, "\\\x00") || expected[alias] || (role != "memory.py" && role != "coordination.py") {
			return nil, errors.New("invalid compatibility alias")
		}
		expected[alias] = true
	}
	if len(r.Members) != len(expected) {
		return nil, errors.New("incomplete compatibility closure")
	}
	codeMap := make(map[string]string, len(expected))
	for name := range expected {
		m, ok := r.Members[name]
		if !ok || !validDigest(m.SHA256) || m.Bytes < 0 || m.Bytes > memberLimit {
			return nil, errors.New("invalid compatibility member descriptor")
		}
		codeMap[name] = m.SHA256
	}
	encoded, err := asciiCodeMap(codeMap)
	if err != nil {
		return nil, err
	}
	if filepath.Base(r.SnapshotRoot) != digest(encoded) {
		return nil, errors.New("compatibility snapshot identity differs")
	}
	for alias, role := range r.Aliases {
		if r.Members[alias] != r.Members[role] {
			return nil, errors.New("compatibility alias differs from its role")
		}
	}
	return &r, nil
}

// asciiCodeMap retains the existing sorted-key, ensure_ascii=True digest
// convention, including non-ASCII aliases and literal HTML characters.
func asciiCodeMap(codeMap map[string]string) ([]byte, error) {
	var encoded bytes.Buffer
	encoder := json.NewEncoder(&encoded)
	encoder.SetEscapeHTML(false)
	if err := encoder.Encode(codeMap); err != nil {
		return nil, err
	}
	var ascii bytes.Buffer
	for _, c := range strings.TrimSuffix(encoded.String(), "\n") {
		if c <= 0x7f {
			ascii.WriteByte(byte(c))
		} else if c <= 0xffff {
			fmt.Fprintf(&ascii, "\\u%04x", c)
		} else {
			high, low := utf16.EncodeRune(c)
			fmt.Fprintf(&ascii, "\\u%04x\\u%04x", high, low)
		}
	}
	return ascii.Bytes(), nil
}

// readCompatibilityReceipt checks the independent external pin. It does not
// authenticate its own caller, select an interpreter, or manufacture a /2 pin.
func readCompatibilityReceipt(path, expected string) (*compatibilityReceipt, error) {
	if !validDigest(expected) {
		return nil, errors.New("invalid external compatibility receipt pin")
	}
	raw, err := readArtifact(path, receiptLimit)
	if err != nil {
		return nil, err
	}
	if digest(raw) != expected {
		return nil, errors.New("compatibility receipt changed")
	}
	return decodeCompatibilityReceipt(raw)
}

// checkedClosure returns the exact source bytes a checked loader may execute.
// A later pathname read cannot substitute for this snapshot.
func (r *compatibilityReceipt) checkedClosure() (map[string][]byte, error) {
	names := make([]string, 0, len(r.Members))
	for name := range r.Members {
		names = append(names, name)
	}
	sort.Strings(names)
	sources := make(map[string][]byte, len(names))
	for _, name := range names {
		member := r.Members[name]
		raw, err := readArtifact(filepath.Join(r.SnapshotRoot, name), memberLimit)
		if err != nil {
			return nil, err
		}
		if int64(len(raw)) != member.Bytes || digest(raw) != member.SHA256 {
			return nil, errors.New("compatibility closure member changed")
		}
		sources[name] = raw
	}
	return sources, nil
}

func readArtifact(path string, limit int64) ([]byte, error) {
	if !canonicalPath(path) {
		return nil, errors.New("artifact path must be canonical and absolute")
	}
	file, err := openArtifact(path)
	if err != nil {
		return nil, err
	}
	before, err := fileStat(file)
	if err != nil {
		_ = file.Close()
		return nil, err
	}
	if before.Size < 0 || before.Size > limit {
		_ = file.Close()
		return nil, errors.New("artifact exceeds size limit")
	}
	raw, readErr := io.ReadAll(io.LimitReader(file, limit+1))
	after, statErr := fileStat(file)
	closeErr := file.Close()
	if readErr != nil {
		return nil, readErr
	}
	if statErr != nil {
		return nil, statErr
	}
	if closeErr != nil {
		return nil, closeErr
	}
	if !sameMetadata(before, after) || int64(len(raw)) != before.Size {
		return nil, errors.New("artifact changed during read")
	}
	return raw, nil
}

func openArtifact(path string) (*os.File, error) {
	parts := strings.Split(strings.TrimPrefix(path, "/"), "/")
	fd, err := syscall.Open("/", syscall.O_RDONLY|syscall.O_DIRECTORY|syscall.O_NOFOLLOW|syscall.O_CLOEXEC, 0)
	if err != nil {
		return nil, err
	}
	defer func() { _ = syscall.Close(fd) }()
	walked := "/"
	var parent syscall.Stat_t
	for _, part := range parts[:len(parts)-1] {
		next, err := syscall.Openat(fd, part, syscall.O_RDONLY|syscall.O_DIRECTORY|syscall.O_NOFOLLOW|syscall.O_CLOEXEC, 0)
		if err != nil {
			return nil, err
		}
		_ = syscall.Close(fd)
		fd = next
		walked = filepath.Join(walked, part)
		if err := syscall.Fstat(fd, &parent); err != nil {
			return nil, err
		}
		if parent.Uid != 0 && parent.Uid != uint32(os.Getuid()) {
			return nil, errors.New("artifact ancestor has another owner")
		}
		stickyTemp := (walked == "/tmp" || walked == "/var/tmp") && parent.Uid == 0 && parent.Mode&syscall.S_ISVTX != 0
		if parent.Mode&0022 != 0 && !stickyTemp {
			return nil, errors.New("artifact ancestor is writable by group or others")
		}
	}
	if err := syscall.Fstat(fd, &parent); err != nil {
		return nil, err
	}
	if parent.Uid != uint32(os.Getuid()) || parent.Mode&07777 != 0700 {
		return nil, errors.New("artifact directory must be real-UID-owned mode 0700")
	}
	opened, err := syscall.Openat(fd, parts[len(parts)-1], syscall.O_RDONLY|syscall.O_NOFOLLOW|syscall.O_NONBLOCK|syscall.O_CLOEXEC, 0)
	if err != nil {
		return nil, err
	}
	file := os.NewFile(uintptr(opened), path)
	info, err := fileStat(file)
	if err == nil && (info.Mode&syscall.S_IFMT != syscall.S_IFREG || info.Uid != uint32(os.Getuid()) || info.Mode&07022 != 0) {
		err = fmt.Errorf("artifact must be a real-UID-owned controlled regular file")
	}
	if err != nil {
		_ = file.Close()
		return nil, err
	}
	return file, nil
}
