package coordination

import (
	"bytes"
	"context"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"syscall"
	"unicode/utf8"

	"github.com/halbritt/cairn/internal/lifecycle/install"
	"github.com/halbritt/cairn/internal/lifecycle/value"
)

// UnitControlPlan selects files, not authority or a serialized drain receipt.
// An empty callback list is an explicitly observed census; null is unknown.
type UnitControlPlan struct {
	Schema  string `json:"schema"`
	Current struct {
		Directory string            `json:"directory"`
		Pins      map[string]string `json:"pins"`
	} `json:"current"`
	Next      []install.File  `json:"next"`
	Retained  string          `json:"retained"`
	Callbacks json.RawMessage `json:"callbacks"`
}

type UnitControlResult struct {
	Schema              string                         `json:"schema"`
	Operation           string                         `json:"operation"`
	PlanPath            string                         `json:"plan_path,omitempty"`
	PlanSHA256          string                         `json:"plan_sha256,omitempty"`
	Inspected           bool                           `json:"inspected"`
	Current             WatchBindings                  `json:"current"`
	CurrentIdentity     map[string]uint64              `json:"current_identity,omitempty"`
	SelectionIdentities map[string]UnitControlIdentity `json:"selection_identities,omitempty"`
	Facts               map[string]string              `json:"facts,omitempty"`
	Exchange            WatchBindingsExchange          `json:"exchange"`
	Error               string                         `json:"error,omitempty"`
}

type UnitControlIdentity struct {
	Exists     bool   `json:"exists"`
	Device     uint64 `json:"device,omitempty"`
	Inode      uint64 `json:"inode,omitempty"`
	LockDevice uint64 `json:"lock_device,omitempty"`
	LockInode  uint64 `json:"lock_inode,omitempty"`
	Error      string `json:"error,omitempty"`
}

// RunUnitControl keeps a freshly captured receipt inside this one ordinary
// control process. It does not install callbacks, refresh clients, settle an
// inbox, or infer complete external unit coverage. Both switch directions use
// the accepted final re-read, including its per-fact/composite timing limits.
func (c *CLI) RunUnitControl(ctx context.Context, args []string, out io.Writer) int {
	r := UnitControlResult{Schema: "cairn.unit-control-result/1"}
	code := 1
	err := func() error {
		if len(args) != 5 || (args[0] != "inspect" && args[0] != "switch" && args[0] != "reverse") || args[1] != "--plan" || args[3] != "--sha256" {
			code = 2
			return errors.New("usage: cairn-lifecycle unit-control inspect|switch|reverse --plan ABSOLUTE_JSON --sha256 SHA256")
		}
		r.Operation, r.PlanPath, r.PlanSHA256 = args[0], args[2], args[4]
		p, callbacks, err := readUnitControlPlan(r.PlanPath, r.PlanSHA256)
		if err != nil {
			return err
		}
		r.Current = WatchBindings{Directory: p.Current.Directory, Pins: p.Current.Pins}
		if err := c.validateUnitControlSelection(p, r.Operation); err != nil {
			return err
		}
		info, err := os.Stat(p.Current.Directory)
		if err != nil {
			return err
		}
		stat := info.Sys().(*syscall.Stat_t)
		r.CurrentIdentity = map[string]uint64{"device": uint64(stat.Dev), "inode": stat.Ino}
		receipt, err := c.CaptureWatchDrain(ctx, r.Current, callbacks)
		if err != nil {
			return err
		}
		r.Inspected, r.Facts = true, receipt.Facts()
		// Revalidate the pinned input and reverse source after capture too. No
		// caller-supplied facts or completion flag can replace this receipt.
		if _, _, err := readUnitControlPlan(r.PlanPath, r.PlanSHA256); err != nil {
			return err
		}
		if err := c.validateUnitControlSelection(p, r.Operation); err != nil {
			return err
		}
		if r.Operation != "inspect" {
			r.Exchange, err = ExchangeWatchBindings(ctx, receipt, p.Retained, p.Next)
			if err != nil {
				return err
			}
		}
		code = 0
		return nil
	}()
	if err != nil {
		r.Error = err.Error()
	}
	r.SelectionIdentities = map[string]UnitControlIdentity{}
	for _, path := range []string{r.Current.Directory, r.Exchange.Preparation, r.Exchange.Active.Directory, r.Exchange.Retained.Directory} {
		if path == "" {
			continue
		}
		identity := UnitControlIdentity{}
		info, err := os.Stat(path)
		if err != nil {
			identity.Error = err.Error()
		} else if s, ok := info.Sys().(*syscall.Stat_t); ok {
			identity.Exists = true
			identity.Device = uint64(s.Dev)
			identity.Inode = s.Ino
			lock, e := os.Stat(filepath.Join(path, ".watch.lock"))
			if e == nil {
				if ls, ok := lock.Sys().(*syscall.Stat_t); ok {
					identity.LockDevice = uint64(ls.Dev)
					identity.LockInode = ls.Ino
				}
			} else if !errors.Is(e, os.ErrNotExist) {
				identity.Error = e.Error()
			}
		}
		r.SelectionIdentities[path] = identity
	}
	if err := json.NewEncoder(out).Encode(r); err != nil {
		return 1
	}
	return code
}

func readUnitControlPlan(path, pin string) (*UnitControlPlan, []*value.Value, error) {
	digest, err := hex.DecodeString(pin)
	if err != nil || len(digest) != 32 || hex.EncodeToString(digest) != pin {
		return nil, nil, errors.New("unit control requires a lowercase SHA256 plan pin")
	}
	if os.Getuid() != os.Geteuid() {
		return nil, nil, errors.New("unit control requires unchanged UID")
	}
	raw, actual, err := compatibilityOpened(path, 65536, uint32(os.Getuid()))
	if err != nil {
		return nil, nil, err
	}
	info, err := os.Lstat(path)
	if err != nil || info.Mode() != 0600 {
		return nil, nil, errors.Join(err, errors.New("unit control plan must be owner-only mode0600"))
	}
	if actual != pin || !utf8.Valid(raw) {
		return nil, nil, errors.New("unit control plan pin or UTF-8 mismatch")
	}
	tree, err := value.DecodeBytes(raw, 0)
	if err != nil {
		return nil, nil, err
	}
	if err := unitControlUniqueKeys(tree, 0); err != nil {
		return nil, nil, err
	}
	if err := unitControlFields(tree, "schema", "current", "next", "retained", "callbacks"); err != nil {
		return nil, nil, err
	}
	if err := unitControlFields(tree.Get("current"), "directory", "pins"); err != nil {
		return nil, nil, err
	}
	if next := tree.Get("next"); next != nil && next.Kind == value.Array {
		for _, file := range next.Items {
			if err := unitControlFields(file, "name", "path", "sha256", "executable"); err != nil {
				return nil, nil, err
			}
		}
	}
	var plan UnitControlPlan
	d := json.NewDecoder(bytes.NewReader(raw))
	d.DisallowUnknownFields()
	if err := d.Decode(&plan); err != nil {
		return nil, nil, err
	}
	if err := d.Decode(new(any)); err != io.EOF {
		return nil, nil, errors.New("unit control requires exactly one JSON plan")
	}
	if plan.Schema != "cairn.unit-control/1" || len(plan.Current.Pins) < 1 || len(plan.Current.Pins) > 32 || len(plan.Next) != len(plan.Current.Pins) {
		return nil, nil, errors.New("unit control plan schema or bounded membership is unknown")
	}
	refs, err := value.DecodeBytes(plan.Callbacks, 0)
	if err != nil || refs == nil || refs.Kind != value.Array || len(refs.Items) > 256 {
		return nil, nil, errors.New("D5 explicit bounded callback census array required; null/missing is unknown")
	}
	callbacks := make([]*value.Value, 0, len(refs.Items))
	for _, ref := range refs.Items {
		if err := unitControlFields(ref, "pid", "start", "boot"); err != nil {
			return nil, nil, fmt.Errorf("D5 callback identity is unknown: %w", err)
		}
		pid := ref.Get("pid")
		if pid == nil || pid.Kind != value.Int {
			return nil, nil, errors.New("D5 callback PID is unknown")
		}
		number, err := strconv.Atoi(pid.Integer)
		if err != nil || number <= 1 {
			return nil, nil, errors.New("D5 callback PID must exceed 1")
		}
		if start, boot := ref.Get("start"), ref.Get("boot"); start == nil || start.Kind != value.Int || boot == nil || boot.Kind != value.String || nativeText(boot) == "" {
			return nil, nil, errors.New("D5 callback start/boot identity is unknown")
		}
		if start, err := strconv.ParseUint(ref.Get("start").Integer, 10, 64); err != nil || start == 0 {
			return nil, nil, errors.New("D5 callback start must be a positive uint64")
		}
		callbacks = append(callbacks, ref)
	}
	return &plan, callbacks, nil
}

func unitControlFields(v *value.Value, allowed ...string) error {
	if v == nil || v.Kind != value.Object {
		return errors.New("unit control requires a known object shape")
	}
	for _, member := range v.Members {
		known := false
		for _, key := range allowed {
			known = known || string(member.Key) == key
		}
		if !known {
			return errors.New("unit control unknown or nonliteral JSON field")
		}
	}
	return nil
}

func unitControlUniqueKeys(v *value.Value, depth int) error {
	if v == nil || depth > 32 {
		return errors.New("unit control JSON depth is unknown")
	}
	seen := map[string]bool{}
	for _, member := range v.Members {
		key := strings.ToLower(string(member.Key))
		if seen[key] {
			return errors.New("unit control duplicate JSON key")
		}
		seen[key] = true
		if err := unitControlUniqueKeys(member.Value, depth+1); err != nil {
			return err
		}
	}
	for _, item := range v.Items {
		if err := unitControlUniqueKeys(item, depth+1); err != nil {
			return err
		}
	}
	return nil
}

func unitControlDirectory(path string) error {
	if !compatibilityCanonical(path) {
		return errors.New("unit control directory must be canonical absolute")
	}
	info, err := os.Lstat(path)
	if err != nil {
		return err
	}
	stat, ok := info.Sys().(*syscall.Stat_t)
	if !ok || info.Mode() != os.ModeDir|0700 || stat.Uid != uint32(os.Getuid()) {
		return errors.New("unit control directory ownership/mode/type is unknown")
	}
	return nil
}

func (c *CLI) validateUnitControlSelection(p *UnitControlPlan, operation string) error {
	current := WatchBindings{Directory: p.Current.Directory, Pins: p.Current.Pins}
	if err := unitControlDirectory(current.Directory); err != nil {
		return err
	}
	if !compatibilityCanonical(p.Retained) || filepath.Dir(p.Retained) != filepath.Dir(current.Directory) || p.Retained == current.Directory {
		return errors.New("unit control retained path must be a distinct canonical sibling")
	}
	if err := unitControlDirectory(filepath.Dir(p.Retained)); err != nil {
		return err
	}
	if _, err := os.Lstat(p.Retained); !errors.Is(err, os.ErrNotExist) {
		return errors.Join(err, errors.New("unit control retained path already exists or is unknown"))
	}
	if err := readWatchBindings(current); err != nil {
		return err
	}
	next, _, err := nextWatchBindingFiles(current, p.Next)
	if err != nil {
		return err
	}
	if _, err := watchBindingNames(next.Pins); err != nil {
		return err
	}
	for _, file := range p.Next {
		info, err := os.Lstat(file.Path)
		if err != nil || info.Mode() != 0400 {
			return errors.Join(err, errors.New("unit control next files must be immutable mode0400 data"))
		}
		if err := c.unitControlConfigPair(filepath.Join(current.Directory, file.Name), file.Path); err != nil {
			return err
		}
	}
	if operation == "reverse" {
		source := WatchBindings{Directory: filepath.Dir(p.Next[0].Path), Pins: next.Pins}
		if source.Directory == current.Directory {
			return errors.New("reverse requires a distinct retained selection")
		}
		for _, file := range p.Next {
			if filepath.Dir(file.Path) != source.Directory || filepath.Base(file.Path) != file.Name {
				return errors.New("reverse requires the complete pinned retained directory")
			}
		}
		if err := unitControlDirectory(source.Directory); err != nil {
			return err
		}
		if err := readWatchBindings(source); err != nil {
			return err
		}
		a, errA := os.Stat(filepath.Join(source.Directory, ".watch.lock"))
		b, errB := os.Stat(filepath.Join(current.Directory, ".watch.lock"))
		if errA != nil || errB != nil || !os.SameFile(a, b) {
			return errors.Join(errA, errB, fmt.Errorf("reverse retained watch-lock identity differs"))
		}
	}
	return nil
}

func (c *CLI) unitControlConfigPair(currentPath, nextPath string) error {
	if c.profile() == nil {
		return &value.ProfileUnavailable{}
	}
	var configs []*value.Value
	var memories []string
	for _, path := range []string{currentPath, nextPath} {
		raw, _, err := compatibilityOpened(path, 1048576, uint32(os.Getuid()))
		if err != nil {
			return err
		}
		config, err := c.profile().Decode(SiteSessionRead, raw)
		if err != nil {
			return err
		}
		if _, err := ValidateConfig(config); err != nil {
			return err
		}
		read := &watchDrainRead{cli: c, facts: map[string]string{}, tokenPaths: map[string]bool{nativeText(config.Get("token_file")): true}}
		memory, err := read.disabledBinding(config)
		if err != nil {
			return err
		}
		configs = append(configs, config)
		memories = append(memories, nativeText(memory.Get("state_dir")))
	}
	for _, key := range []string{"binding", "state_dir", "harness", "repo", "socket", "token_file", "config_home"} {
		if !nativeEqual(configs[0].Get(key), configs[1].Get(key)) {
			return fmt.Errorf("unit control cannot change unit identity/selector %s", key)
		}
	}
	if memories[0] != memories[1] {
		return errors.New("unit control cannot change the affected memory state directory")
	}
	return nil
}
