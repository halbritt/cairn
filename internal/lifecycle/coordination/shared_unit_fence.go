package coordination

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"maps"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"slices"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"
	"unicode/utf8"

	"github.com/jackc/pgx/v5"
)

// SelectedUnitFenceScope is a selection to inspect, never a stopped assertion.
// Other hosts, units, executable identities and database selections stay unknown.
type SelectedUnitFenceScope struct {
	UID              uint32
	Units            []string
	Executables      []WatchCensusPin
	CommandSHA256    []string
	Database         string
	DatabaseSystemID string
	Window           time.Duration
}

// SelectedUnitFenceReceipt cannot be reconstructed from retained evidence.
type SelectedUnitFenceReceipt struct{ origin *selectedFenceOrigin }

type selectedFenceOrigin struct {
	mu       sync.Mutex
	owner    *SelectedUnitFenceReceipt
	pid      int
	start    uint64
	boot     string
	scope    SelectedUnitFenceScope
	facts    map[string]string
	reader   *selectedFenceHost
	created  time.Time
	deadline time.Time
	invalid  bool
}

var errSelectedUnitFence = errors.New("selected unit fence unavailable or invalidated")

func selectedFenceUnknown(phase string) error {
	return fmt.Errorf("%w: %s", errSelectedUnitFence, phase)
}

func (SelectedUnitFenceReceipt) MarshalJSON() ([]byte, error) {
	return nil, selectedFenceUnknown("process-local receipt; retain Facts instead")
}

func (*SelectedUnitFenceReceipt) UnmarshalJSON([]byte) error {
	return selectedFenceUnknown("serialized evidence cannot mint a receipt")
}

// Facts returns only a copy of selected evidence, without credentials or argv.
// It cannot authorize a swap or replace Recheck.
func (r *SelectedUnitFenceReceipt) Facts() map[string]string {
	if r == nil || r.origin == nil {
		return nil
	}
	o := r.origin
	o.mu.Lock()
	defer o.mu.Unlock()
	if o.owner != r {
		return nil
	}
	facts := maps.Clone(o.facts)
	facts["external_coverage"] = "selected-units-only"
	facts["outside_scope"] = "unknown"
	facts["captured_at"] = o.created.UTC().Format(time.RFC3339Nano)
	facts["expires_at"] = o.deadline.UTC().Format(time.RFC3339Nano)
	facts["receipt_invalidated"] = strconv.FormatBool(o.invalid)
	facts["atomicity"] = "per-fact reads; final read/action interval is not fenced"
	return facts
}

// CaptureSelectedUnitFence issues no stop, mask, start, restart or data write.
// An empty database name and system ID require db == nil and select no database
// coverage. Otherwise db must already be an authenticated local Unix PostgreSQL
// connection owned by this caller. No DSN or serialized fact is accepted.
func CaptureSelectedUnitFence(ctx context.Context, scope SelectedUnitFenceScope, db *pgx.Conn) (*SelectedUnitFenceReceipt, error) {
	if _, err := selectedFenceScope(scope); err != nil {
		return nil, err
	}
	if scope.Database == "" && db != nil {
		return nil, selectedFenceUnknown("database connection supplied without selection")
	}
	if scope.Database != "" && (db == nil || db.IsClosed() || db.PgConn().Conn() == nil || db.PgConn().Conn().RemoteAddr().Network() != "unix" || db.PgConn().TxStatus() != 'I') {
		return nil, selectedFenceUnknown("authenticated local database connection required")
	}
	host := &selectedFenceHost{procRoot: "/proc", cgroupRoot: "/sys/fs/cgroup", machineFile: "/etc/machine-id", db: db}
	host.query = host.systemd
	return captureSelectedUnitFence(ctx, scope, host)
}

func selectedFenceScope(scope SelectedUnitFenceScope) (SelectedUnitFenceScope, error) {
	if scope.UID != uint32(os.Getuid()) || os.Getuid() != os.Geteuid() || len(scope.Units) > 128 ||
		len(scope.Executables) == 0 || len(scope.Executables) > 128 || len(scope.CommandSHA256) > 128 ||
		(scope.Database == "") != (scope.DatabaseSystemID == "") ||
		scope.Database != "" && (!utf8.ValidString(scope.Database) || len(scope.Database) > 63 || strings.ContainsRune(scope.Database, 0) ||
			!regexp.MustCompile(`^[1-9][0-9]{0,19}$`).MatchString(scope.DatabaseSystemID)) ||
		scope.Window <= 0 || scope.Window > 30*time.Second {
		return scope, selectedFenceUnknown("explicit bounded local selection required")
	}
	scope.Units = slices.Clone(scope.Units)
	scope.Executables = slices.Clone(scope.Executables)
	scope.CommandSHA256 = slices.Clone(scope.CommandSHA256)
	slices.Sort(scope.Units)
	slices.Sort(scope.CommandSHA256)
	slices.SortFunc(scope.Executables, func(a, b WatchCensusPin) int { return strings.Compare(a.Path, b.Path) })
	for i, unit := range scope.Units {
		if !selectedFenceUnitName(unit) || i > 0 && scope.Units[i-1] == unit {
			return scope, selectedFenceUnknown("unit selection unknown or duplicated")
		}
	}
	for i, pin := range scope.Executables {
		if !utf8.ValidString(pin.Path) || strings.ContainsRune(pin.Path, 0) || !filepath.IsAbs(pin.Path) || filepath.Clean(pin.Path) != pin.Path || !censusDigest(pin.SHA256) ||
			i > 0 && scope.Executables[i-1].Path == pin.Path {
			return scope, selectedFenceUnknown("executable selection unknown or duplicated")
		}
	}
	for i, digest := range scope.CommandSHA256 {
		if !censusDigest(digest) || i > 0 && scope.CommandSHA256[i-1] == digest {
			return scope, selectedFenceUnknown("command selection unknown or duplicated")
		}
	}
	return scope, nil
}

func selectedFenceHash(v any) string {
	raw, err := json.Marshal(v)
	if err != nil {
		panic("selected fence internal evidence type is not JSON-compatible")
	}
	sum := sha256.Sum256(raw)
	return hex.EncodeToString(sum[:])
}

func captureSelectedUnitFence(ctx context.Context, scope SelectedUnitFenceScope, reader *selectedFenceHost) (*SelectedUnitFenceReceipt, error) {
	scope, err := selectedFenceScope(scope)
	if err != nil || reader == nil || reader.query == nil {
		return nil, errors.Join(err, selectedFenceUnknown("producer required"))
	}
	created := time.Now()
	deadline := created.Add(scope.Window)
	ctx, cancel := context.WithDeadline(ctx, deadline)
	defer cancel()
	start, err := readProcessStatAt("/proc", os.Getpid())
	if err != nil {
		return nil, selectedFenceUnknown("origin generation unreadable")
	}
	boot, err := bootIDAt("/proc")
	if err != nil || boot == "" {
		return nil, selectedFenceUnknown("origin boot unknown")
	}
	first, err := reader.observe(ctx, scope)
	if err != nil || len(first) == 0 {
		return nil, errors.Join(err, selectedFenceUnknown("initial observation incomplete"))
	}
	last, err := reader.observe(ctx, scope)
	if err != nil || !maps.Equal(first, last) || ctx.Err() != nil {
		return nil, errors.Join(err, selectedFenceUnknown("capture final reread changed or expired"))
	}
	r := &SelectedUnitFenceReceipt{}
	r.origin = &selectedFenceOrigin{owner: r, pid: os.Getpid(), start: start.start, boot: boot,
		scope: scope, facts: maps.Clone(last), reader: reader, created: created, deadline: deadline}
	return r, nil
}

// Recheck requires this exact process-local handle and the consumer's expected
// scope, and performs two fresh observations. A failed recheck is terminal for
// this receipt. Neither observation is atomic with a later swap or reverse.
func (r *SelectedUnitFenceReceipt) Recheck(ctx context.Context, expected SelectedUnitFenceScope) error {
	if r == nil || r.origin == nil {
		return selectedFenceUnknown("origin receipt required")
	}
	o := r.origin
	o.mu.Lock()
	defer o.mu.Unlock()
	if o.owner != r || o.pid != os.Getpid() || o.reader == nil {
		return selectedFenceUnknown("copied or foreign receipt")
	}
	if o.invalid {
		return selectedFenceUnknown("receipt already invalidated")
	}
	o.invalid = true
	normal, err := selectedFenceScope(expected)
	if err != nil || selectedFenceHash(normal) != selectedFenceHash(o.scope) || !time.Now().Before(o.deadline) {
		return selectedFenceUnknown("scope changed or window expired")
	}
	current, err := readProcessStatAt("/proc", o.pid)
	boot, bootErr := bootIDAt("/proc")
	if err != nil || bootErr != nil || current.start != o.start || boot != o.boot {
		return selectedFenceUnknown("origin process generation changed")
	}
	ctx, cancel := context.WithDeadline(ctx, o.deadline)
	defer cancel()
	for range 2 {
		facts, err := o.reader.observe(ctx, o.scope)
		if err != nil || !maps.Equal(o.facts, facts) || ctx.Err() != nil {
			return errors.Join(err, selectedFenceUnknown("fresh selected facts changed or unreadable"))
		}
	}
	o.invalid = false
	return nil
}

type selectedFenceHost struct {
	procRoot, cgroupRoot, machineFile string
	db                                *pgx.Conn
	query                             func(context.Context, ...string) (string, error)
	dbRead                            func(context.Context, SelectedUnitFenceScope) (map[string]string, error)
}

var selectedFenceUnitPattern = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9_.@:-]*\.(service|timer|socket|path|scope|target)$`)

func selectedFenceUnitName(name string) bool { return selectedFenceUnitPattern.MatchString(name) }

func selectedFenceTool() (string, error) {
	path := "/usr/bin/systemctl"
	info, err := os.Lstat(path)
	if err != nil || !info.Mode().IsRegular() || info.Mode().Perm()&0022 != 0 || info.Sys().(*syscall.Stat_t).Uid != 0 {
		return "", selectedFenceUnknown("installed systemctl identity unknown")
	}
	return selectedFenceFile(path)
}

type selectedFenceFileObservation struct {
	fingerprint, contentSHA string
	identity                *syscall.Stat_t
	mode                    os.FileMode
}

func selectedFenceFile(path string) (string, error) {
	observation, err := selectedFenceReadFile(path)
	return observation.fingerprint, err
}

func selectedFenceReadFile(path string) (selectedFenceFileObservation, error) {
	file, err := os.Open(path)
	if err != nil {
		return selectedFenceFileObservation{}, selectedFenceUnknown("selected file unreadable")
	}
	defer file.Close()
	before, err := file.Stat()
	if err != nil || !before.Mode().IsRegular() {
		return selectedFenceFileObservation{}, selectedFenceUnknown("selected file identity unknown")
	}
	hash := sha256.New()
	n, err := io.Copy(hash, io.LimitReader(file, 512<<20+1))
	after, statErr := file.Stat()
	pathInfo, pathErr := os.Stat(path)
	if err != nil || statErr != nil || pathErr != nil || n > 512<<20 ||
		!os.SameFile(before, after) || !os.SameFile(before, pathInfo) || before.Size() != after.Size() ||
		before.ModTime() != after.ModTime() || before.Mode() != after.Mode() || before.Mode() != pathInfo.Mode() ||
		before.Sys().(*syscall.Stat_t).Uid != after.Sys().(*syscall.Stat_t).Uid || before.Sys().(*syscall.Stat_t).Gid != after.Sys().(*syscall.Stat_t).Gid {
		return selectedFenceFileObservation{}, selectedFenceUnknown("selected file changed during read")
	}
	s := after.Sys().(*syscall.Stat_t)
	contentSHA := hex.EncodeToString(hash.Sum(nil))
	return selectedFenceFileObservation{selectedFenceHash([]any{contentSHA, s.Dev, s.Ino, s.Uid, s.Gid, after.Mode(), after.Size()}), contentSHA, s, after.Mode()}, nil
}

func (h *selectedFenceHost) systemd(ctx context.Context, args ...string) (string, error) {
	before, err := selectedFenceTool()
	if err != nil {
		return "", err
	}
	runtime := fmt.Sprintf("/run/user/%d", os.Getuid())
	cmd := exec.CommandContext(ctx, "/usr/bin/systemctl", append([]string{"--user", "--no-pager"}, args...)...)
	cmd.Env = append(os.Environ(), "LC_ALL=C", "SYSTEMD_COLORS=0", "DBUS_SESSION_BUS_ADDRESS=unix:path="+runtime+"/bus", "XDG_RUNTIME_DIR="+runtime)
	raw, err := cmd.Output()
	after, pinErr := selectedFenceTool()
	if err != nil || pinErr != nil || before != after || len(raw) > 1<<20 {
		return "", selectedFenceUnknown("local user-manager read failed or changed")
	}
	return string(raw), nil
}

var selectedFenceProperties = []string{"Id", "LoadState", "ActiveState", "SubState", "MainPID", "ControlGroup", "UnitFileState", "FragmentPath", "DropInPaths", "ExecStart", "Restart", "Triggers", "TriggeredBy", "NeedDaemonReload", "Transient", "InvocationID", "StateChangeTimestampMonotonic"}

func selectedFenceParseProperties(raw string) (map[string]string, error) {
	if !utf8.ValidString(raw) || strings.ContainsRune(raw, 0) {
		return nil, selectedFenceUnknown("unit property encoding unknown")
	}
	properties := map[string]string{}
	for _, line := range strings.Split(strings.TrimSuffix(raw, "\n"), "\n") {
		key, value, ok := strings.Cut(line, "=")
		if !ok || !slices.Contains(selectedFenceProperties, key) {
			return nil, selectedFenceUnknown("unknown unit property")
		}
		if _, duplicate := properties[key]; duplicate {
			return nil, selectedFenceUnknown("duplicate unit property")
		}
		properties[key] = value
	}
	return properties, nil
}

func selectedFenceStoppedUnit(name string, properties map[string]string) error {
	for _, key := range []string{"Id", "LoadState", "ActiveState", "SubState", "UnitFileState", "FragmentPath", "DropInPaths", "Triggers", "TriggeredBy", "NeedDaemonReload", "StateChangeTimestampMonotonic", "InvocationID"} {
		if _, ok := properties[key]; !ok {
			return selectedFenceUnknown("unit property coverage unknown")
		}
	}
	if properties["Id"] != name || properties["ActiveState"] != "inactive" || properties["SubState"] != "dead" ||
		(properties["LoadState"] != "loaded" && properties["LoadState"] != "masked") ||
		!slices.Contains([]string{"enabled", "disabled", "static", "masked", "masked-runtime", "indirect"}, properties["UnitFileState"]) || properties["NeedDaemonReload"] != "no" {
		return selectedFenceUnknown("unit or activation trigger is not stopped and masked")
	}
	if _, err := strconv.ParseUint(properties["StateChangeTimestampMonotonic"], 10, 64); err != nil {
		return selectedFenceUnknown("unit generation unknown")
	}
	if strings.HasSuffix(name, ".service") {
		for _, key := range []string{"MainPID", "ControlGroup", "Restart"} {
			if _, ok := properties[key]; !ok {
				return selectedFenceUnknown("service property coverage unknown")
			}
		}
		if _, ok := properties["ExecStart"]; !ok && properties["LoadState"] == "loaded" {
			return selectedFenceUnknown("loaded service command coverage unknown")
		}
		if properties["MainPID"] != "0" {
			return selectedFenceUnknown("service PID is not stopped")
		}
	}
	if strings.HasSuffix(name, ".scope") || strings.HasSuffix(name, ".target") {
		return selectedFenceUnknown("selected unit type has no supported stopped contract")
	}
	return nil
}

func selectedFenceDefinition(fragment, loadState string) (string, error) {
	if loadState != "masked" {
		return selectedFenceFile(fragment)
	}
	if fragment == "/dev/null" {
		return "manager-reported-mask:/dev/null", nil
	}
	if !filepath.IsAbs(fragment) || filepath.Clean(fragment) != fragment {
		return "", selectedFenceUnknown("masked definition path unknown")
	}
	before, err := os.Lstat(fragment)
	if err != nil || before.Mode()&os.ModeSymlink == 0 {
		return "", selectedFenceUnknown("masked definition is not a symlink")
	}
	target, err := os.Readlink(fragment)
	if err != nil || target != "/dev/null" {
		return "", selectedFenceUnknown("masked definition target unknown")
	}
	after, err := os.Lstat(fragment)
	again, linkErr := os.Readlink(fragment)
	if err != nil || linkErr != nil || !os.SameFile(before, after) || before.Mode() != after.Mode() || again != target {
		return "", selectedFenceUnknown("masked definition changed during read")
	}
	a, b := before.Sys().(*syscall.Stat_t), after.Sys().(*syscall.Stat_t)
	if a.Uid != b.Uid || a.Gid != b.Gid {
		return "", selectedFenceUnknown("masked definition ownership changed")
	}
	return selectedFenceHash([]any{b.Dev, b.Ino, b.Uid, b.Gid, after.Mode(), target}), nil
}

func selectedFenceUnitList(raw string) ([]string, error) {
	var names []string
	for _, line := range strings.Split(strings.TrimSpace(raw), "\n") {
		if line == "" {
			continue
		}
		fields := strings.Fields(line)
		if len(fields) < 4 || !selectedFenceUnitName(fields[0]) || !strings.HasPrefix(fields[0], "cairn-wake-") || slices.Contains(names, fields[0]) {
			return nil, selectedFenceUnknown("transient membership unknown")
		}
		names = append(names, fields[0])
	}
	if len(names) > 128 {
		return nil, selectedFenceUnknown("transient membership exceeds bound")
	}
	slices.Sort(names)
	return names, nil
}

func selectedFenceGroup(root, group string) (string, error) {
	if !filepath.IsAbs(group) || filepath.Clean(group) != group || group == "/" {
		return "", selectedFenceUnknown("selected cgroup path unknown")
	}
	path := filepath.Join(root, strings.TrimPrefix(group, "/"))
	resolved, err := filepath.EvalSymlinks(path)
	if err != nil || resolved != path {
		return "", selectedFenceUnknown("selected cgroup missing or indirect")
	}
	info, err := os.Lstat(path)
	if err != nil || !info.IsDir() {
		return "", selectedFenceUnknown("selected cgroup is not a directory")
	}
	facts := map[string]string{}
	directories := 0
	err = filepath.WalkDir(path, func(name string, entry os.DirEntry, walkErr error) error {
		if walkErr != nil || entry.Type()&os.ModeSymlink != 0 {
			return selectedFenceUnknown("cgroup membership unreadable")
		}
		if !entry.IsDir() {
			return nil
		}
		directories++
		if directories > 256 {
			return selectedFenceUnknown("cgroup subtree exceeds bound")
		}
		info, err := entry.Info()
		if err != nil {
			return selectedFenceUnknown("cgroup identity unknown")
		}
		s := info.Sys().(*syscall.Stat_t)
		events, err := os.ReadFile(filepath.Join(name, "cgroup.events"))
		if err != nil {
			return selectedFenceUnknown("cgroup events unreadable")
		}
		populated := false
		seen := map[string]bool{}
		for _, line := range strings.Split(strings.TrimSpace(string(events)), "\n") {
			fields := strings.Fields(line)
			if len(fields) != 2 || seen[fields[0]] || (fields[0] != "populated" && fields[0] != "frozen") || (fields[1] != "0" && fields[1] != "1") {
				return selectedFenceUnknown("cgroup event coverage unknown")
			}
			seen[fields[0]] = true
			if fields[0] == "populated" {
				if fields[1] != "0" {
					return selectedFenceUnknown("selected cgroup populated")
				}
				populated = true
			}
		}
		procs, err := os.ReadFile(filepath.Join(name, "cgroup.procs"))
		if err != nil || !populated || len(bytes.TrimSpace(procs)) != 0 {
			return selectedFenceUnknown("selected cgroup emptiness unknown")
		}
		facts[name] = selectedFenceHash([]any{s.Dev, s.Ino, string(events), string(procs)})
		return nil
	})
	if err != nil {
		return "", err
	}
	return selectedFenceHash(facts), nil
}

type selectedFenceExecutable struct {
	path   string
	device uint64
	inode  uint64
}

type selectedFenceProcScan struct {
	root        string
	uid         uint32
	executables []selectedFenceExecutable
	commands    []string
	units       []string
	selfStart   uint64
	read        func(string) ([]byte, error)
	readlink    func(string) (string, error)
}

func selectedFenceUID(raw []byte) ([4]uint32, error) {
	var result [4]uint32
	found := false
	for _, line := range strings.Split(string(raw), "\n") {
		if !strings.HasPrefix(line, "Uid:") {
			continue
		}
		fields := strings.Fields(line)
		if found || len(fields) != 5 {
			return result, selectedFenceUnknown("process UID unknown")
		}
		for i := range result {
			n, err := strconv.ParseUint(fields[i+1], 10, 32)
			if err != nil {
				return result, selectedFenceUnknown("process UID malformed")
			}
			result[i] = uint32(n)
		}
		found = true
	}
	if !found {
		return result, selectedFenceUnknown("process UID missing")
	}
	return result, nil
}

func (s selectedFenceProcScan) possibleMatch(fields map[string][]byte) bool {
	if command, ok := fields["cmdline"]; ok {
		sum := sha256.Sum256(command)
		if slices.Contains(s.commands, hex.EncodeToString(sum[:])) {
			return true
		}
		argv0, _, _ := bytes.Cut(command, []byte{0})
		for _, selected := range s.executables {
			if string(argv0) == selected.path || filepath.Base(string(argv0)) == filepath.Base(selected.path) {
				return true
			}
		}
	}
	comm := strings.TrimSuffix(string(fields["comm"]), "\n")
	for _, selected := range s.executables {
		name := filepath.Base(selected.path)
		if comm == name[:min(15, len(name))] {
			return true
		}
	}
	group := strings.ReplaceAll(string(fields["cgroup"]), `\x2d`, "-")
	if strings.Contains(group, "cairn-wake-") {
		return true
	}
	for _, unit := range s.units {
		if strings.Contains(group, unit) {
			return true
		}
	}
	return false
}

func (s selectedFenceProcScan) process(pid int) (bool, error) {
	base := filepath.Join(s.root, strconv.Itoa(pid))
	unknown := func(err error, phase string) (bool, error) {
		if errors.Is(err, os.ErrNotExist) || errors.Is(err, syscall.ESRCH) {
			if _, statErr := os.Lstat(base); errors.Is(statErr, os.ErrNotExist) {
				return false, nil
			}
		}
		return false, selectedFenceUnknown(phase)
	}
	raw, err := s.read(filepath.Join(base, "status"))
	if err != nil {
		return unknown(err, "process UID unreadable")
	}
	uids, err := selectedFenceUID(raw)
	if err != nil {
		return false, err
	}
	if !slices.Contains(uids[:], s.uid) && pid != os.Getpid() {
		again, readErr := s.read(filepath.Join(base, "status"))
		if readErr != nil {
			return unknown(readErr, "process UID unreadable during exclusion")
		}
		lastUID, parseErr := selectedFenceUID(again)
		if parseErr != nil || uids != lastUID {
			return false, selectedFenceUnknown("process UID changed during exclusion")
		}
		return false, nil
	}
	first, err := readProcessStatAt(s.root, pid)
	if err != nil {
		return unknown(err, "process generation unknown")
	}
	if pid == os.Getpid() {
		last, err := readProcessStatAt(s.root, pid)
		if err != nil {
			return unknown(err, "caller generation unreadable")
		}
		again, err := s.read(filepath.Join(base, "status"))
		if err != nil {
			return unknown(err, "caller UID unreadable")
		}
		lastUID, parseErr := selectedFenceUID(again)
		if s.selfStart == 0 || first.start != s.selfStart || last.start != s.selfStart ||
			first.state == "Z" || first.state == "X" || last.state == "Z" || last.state == "X" || parseErr != nil || uids != lastUID || !slices.Contains(uids[:], s.uid) {
			return false, selectedFenceUnknown("caller generation or UID changed")
		}
		return false, nil
	}
	readlink := s.readlink
	if readlink == nil {
		readlink = os.Readlink
	}
	zombie := first.state == "Z" || first.state == "X"
	var exe string
	var identity *syscall.Stat_t
	denied := false
	fields := []string{"cmdline"}
	if zombie {
		fields = []string{"comm", "cgroup"}
	} else {
		exe, err = readlink(filepath.Join(base, "exe"))
		denied = errors.Is(err, os.ErrPermission)
		if err != nil && !denied {
			return unknown(err, "process executable unreadable")
		}
		if denied {
			fields = []string{"cmdline", "comm", "cgroup"}
		} else {
			info, err := os.Stat(filepath.Join(base, "exe"))
			if err != nil {
				return unknown(err, "process image identity unreadable")
			}
			identity = info.Sys().(*syscall.Stat_t)
		}
	}
	observed := map[string][]byte{}
	for _, name := range fields {
		raw, err := s.read(filepath.Join(base, name))
		if err != nil {
			return unknown(err, "process exclusion facts unreadable")
		}
		if len(bytes.TrimSpace(raw)) == 0 {
			return false, selectedFenceUnknown("process exclusion facts empty")
		}
		observed[name] = raw
	}
	for _, name := range fields {
		raw, err := s.read(filepath.Join(base, name))
		if err != nil {
			return unknown(err, "process exclusion reread unavailable")
		}
		if !bytes.Equal(raw, observed[name]) {
			return false, selectedFenceUnknown("process exclusion facts changed")
		}
	}
	if !zombie {
		again, err := readlink(filepath.Join(base, "exe"))
		if denied {
			if !errors.Is(err, os.ErrPermission) {
				return unknown(err, "process executable accessibility changed")
			}
		} else {
			if err != nil {
				return unknown(err, "process executable reread unavailable")
			}
			info, err := os.Stat(filepath.Join(base, "exe"))
			if err != nil {
				return unknown(err, "process image reread unavailable")
			}
			last := info.Sys().(*syscall.Stat_t)
			if exe != again || identity.Dev != last.Dev || identity.Ino != last.Ino {
				return false, selectedFenceUnknown("process executable changed")
			}
		}
	}
	lastUIDRaw, uidErr := s.read(filepath.Join(base, "status"))
	if uidErr != nil {
		return unknown(uidErr, "process UID reread unavailable")
	}
	lastUID, parseErr := selectedFenceUID(lastUIDRaw)
	last, err := readProcessStatAt(s.root, pid)
	if err != nil {
		return unknown(err, "process generation reread unavailable")
	}
	if parseErr != nil || first.start != last.start || first.state != last.state || uids != lastUID {
		return false, selectedFenceUnknown("process generation or UID changed during read")
	}
	if zombie || denied {
		if s.possibleMatch(observed) {
			return false, selectedFenceUnknown("unreadable executable or zombie may be selected")
		}
		return false, nil
	}
	for _, selected := range s.executables {
		if strings.TrimSuffix(exe, " (deleted)") == selected.path || identity.Dev == selected.device && identity.Ino == selected.inode {
			return true, nil
		}
	}
	sum := sha256.Sum256(observed["cmdline"])
	return slices.Contains(s.commands, hex.EncodeToString(sum[:])), nil
}

func (s selectedFenceProcScan) empty(ctx context.Context) error {
	self, err := readProcessStatAt("/proc", os.Getpid())
	if err != nil || self.start == 0 {
		return selectedFenceUnknown("caller generation unavailable")
	}
	s.selfStart = self.start
	entries, err := os.ReadDir(s.root)
	if err != nil || len(entries) > 65536 {
		return selectedFenceUnknown("process enumeration unavailable")
	}
	for _, entry := range entries {
		if ctx.Err() != nil {
			return selectedFenceUnknown("process enumeration expired")
		}
		pid, err := strconv.Atoi(entry.Name())
		if err != nil || pid <= 0 {
			continue
		}
		matches, err := s.process(pid)
		if err != nil || matches {
			return errors.Join(err, selectedFenceUnknown("selected process present or observation unknown"))
		}
	}
	return nil
}

func (h *selectedFenceHost) observe(ctx context.Context, scope SelectedUnitFenceScope) (map[string]string, error) {
	facts := map[string]string{"scope": selectedFenceHash(scope)}
	machine, err := os.ReadFile(h.machineFile)
	if err != nil || !regexp.MustCompile(`^[a-f0-9]{32}$`).Match(bytes.TrimSpace(machine)) {
		return nil, selectedFenceUnknown("machine identity unknown")
	}
	boot, err := os.ReadFile(filepath.Join(h.procRoot, "sys/kernel/random/boot_id"))
	if err != nil || !regexp.MustCompile(`^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$`).Match(bytes.TrimSpace(boot)) {
		return nil, selectedFenceUnknown("host boot unknown")
	}
	facts["machine"] = selectedFenceHash(string(bytes.TrimSpace(machine)))
	facts["boot"] = selectedFenceHash(string(bytes.TrimSpace(boot)))
	facts["uid"] = strconv.FormatUint(uint64(scope.UID), 10)
	facts["systemctl"], err = selectedFenceTool()
	if err != nil {
		return nil, err
	}
	scan := selectedFenceProcScan{root: h.procRoot, uid: scope.UID, commands: scope.CommandSHA256, read: os.ReadFile}
	for _, selected := range scope.Executables {
		resolved, err := filepath.EvalSymlinks(selected.Path)
		if err != nil || resolved != selected.Path {
			return nil, selectedFenceUnknown("selected executable path indirect")
		}
		observation, err := selectedFenceReadFile(selected.Path)
		if err != nil || observation.mode.Perm()&0111 == 0 || observation.contentSHA != selected.SHA256 {
			return nil, selectedFenceUnknown("selected executable pin changed")
		}
		identity := observation.identity
		scan.executables = append(scan.executables, selectedFenceExecutable{selected.Path, identity.Dev, identity.Ino})
		facts["executable:"+selectedFenceHash(selected.Path)] = observation.fingerprint
	}
	list := func() ([]string, error) {
		raw, err := h.query(ctx, "list-units", "--all", "--full", "--plain", "--no-legend", "cairn-wake-*")
		if err != nil {
			return nil, err
		}
		return selectedFenceUnitList(raw)
	}
	transients, err := list()
	if err != nil {
		return nil, err
	}
	facts["transient-membership"] = selectedFenceHash(transients)
	queue := append(slices.Clone(scope.Units), transients...)
	seen := map[string]bool{}
	for len(queue) > 0 {
		name := queue[0]
		queue = queue[1:]
		if seen[name] {
			continue
		}
		if !selectedFenceUnitName(name) || len(seen) >= 256 {
			return nil, selectedFenceUnknown("unit/trigger membership unknown")
		}
		seen[name] = true
		scan.units = append(scan.units, name)
		raw, err := h.query(ctx, "show", "--all", "--property="+strings.Join(selectedFenceProperties, ","), name)
		if err != nil {
			return nil, err
		}
		properties, err := selectedFenceParseProperties(raw)
		if err != nil {
			return nil, err
		}
		if err = selectedFenceStoppedUnit(name, properties); err != nil {
			return nil, err
		}
		facts["unit:"+name] = selectedFenceHash(properties)
		facts["definition:"+name], err = selectedFenceDefinition(properties["FragmentPath"], properties["LoadState"])
		if err != nil {
			return nil, err
		}
		for _, dropin := range strings.Fields(properties["DropInPaths"]) {
			facts["dropin:"+name+":"+selectedFenceHash(dropin)], err = selectedFenceFile(dropin)
			if err != nil {
				return nil, err
			}
		}
		if strings.HasSuffix(name, ".service") {
			if properties["ControlGroup"] == "" {
				facts["cgroup:"+name] = "manager-reported-empty"
			} else {
				facts["cgroup:"+name], err = selectedFenceGroup(h.cgroupRoot, properties["ControlGroup"])
				if err != nil {
					return nil, err
				}
			}
		}
		queue = append(queue, strings.Fields(properties["TriggeredBy"])...)
		queue = append(queue, strings.Fields(properties["Triggers"])...)
	}
	if err := scan.empty(ctx); err != nil {
		return nil, err
	}
	facts["matching-selected-processes"] = "0"
	if scope.Database == "" {
		if h.db != nil {
			return nil, selectedFenceUnknown("database connection supplied without selection")
		}
		facts["database_coverage"] = "not-selected"
	} else {
		database := h.dbRead
		if database == nil {
			database = func(ctx context.Context, scope SelectedUnitFenceScope) (map[string]string, error) {
				return selectedFenceDatabase(ctx, h.db, scope)
			}
		}
		dbFacts, err := database(ctx, scope)
		if err != nil {
			return nil, err
		}
		if len(dbFacts) != 4 || !censusDigest(dbFacts["identity"]) || dbFacts["other-backends"] != "0" ||
			dbFacts["visibility"] != "pg_read_all_stats-or-superuser" || dbFacts["transaction"] != "read-only" {
			return nil, selectedFenceUnknown("database fact coverage incomplete")
		}
		facts["database_coverage"] = "selected"
		for key, value := range dbFacts {
			facts["database:"+key] = value
		}
	}
	last, err := list()
	if err != nil || !slices.Equal(transients, last) {
		return nil, selectedFenceUnknown("transient membership changed during observation")
	}
	return facts, nil
}

func selectedFenceDatabase(ctx context.Context, db *pgx.Conn, scope SelectedUnitFenceScope) (map[string]string, error) {
	if db == nil || db.IsClosed() || db.PgConn().Conn() == nil || db.PgConn().Conn().RemoteAddr().Network() != "unix" || db.PgConn().TxStatus() != 'I' {
		return nil, selectedFenceUnknown("authenticated local database connection required")
	}
	tx, err := db.BeginTx(ctx, pgx.TxOptions{AccessMode: pgx.ReadOnly})
	if err != nil {
		return nil, selectedFenceUnknown("database readonly transaction unavailable")
	}
	observe := func() (map[string]string, error) {
		var visible bool
		if err := tx.QueryRow(ctx, `SELECT rolsuper OR pg_has_role(current_user, 'pg_read_all_stats', 'MEMBER') FROM pg_roles WHERE rolname=current_user`).Scan(&visible); err != nil || !visible {
			return nil, selectedFenceUnknown("database activity visibility unknown")
		}
		var database, systemID, databaseOID, user, session, relationOID, readonly string
		var local bool
		if err := tx.QueryRow(ctx, `SELECT current_database(), (SELECT system_identifier::text FROM pg_control_system()), (SELECT oid::text FROM pg_database WHERE datname=current_database()), current_user, session_user, to_regclass('cairn.semantic_job')::oid::text, current_setting('transaction_read_only'), inet_server_addr() IS NULL`).Scan(&database, &systemID, &databaseOID, &user, &session, &relationOID, &readonly, &local); err != nil || !local || readonly != "on" || database != scope.Database || systemID != scope.DatabaseSystemID || relationOID == "" {
			return nil, selectedFenceUnknown("actual database selection or semantic_job identity unknown")
		}
		var backends int64
		if err := tx.QueryRow(ctx, `SELECT count(*) FROM pg_stat_activity WHERE datid=(SELECT oid FROM pg_database WHERE datname=current_database()) AND pid<>pg_backend_pid()`).Scan(&backends); err != nil || backends != 0 {
			return nil, selectedFenceUnknown("other selected-database backend present or unreadable")
		}
		return map[string]string{"identity": selectedFenceHash([]string{database, systemID, databaseOID, user, session, relationOID}), "other-backends": "0", "visibility": "pg_read_all_stats-or-superuser", "transaction": "read-only"}, nil
	}
	facts, readErr := observe()
	cleanup, cancel := context.WithTimeout(context.WithoutCancel(ctx), time.Second)
	rollbackErr := tx.Rollback(cleanup)
	cancel()
	if rollbackErr != nil {
		return nil, errors.Join(readErr, selectedFenceUnknown("readonly database observation did not close"))
	}
	if readErr != nil {
		return nil, readErr
	}
	return facts, nil
}
