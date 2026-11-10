// Package ledger preserves the state policies of the reached Python ledger
// operations. It does not allocate grants, claim turns or transition journals.
package ledger

import (
	"bytes"
	"context"
	"errors"
	"math/big"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"syscall"
	"time"

	"github.com/halbritt/cairn/internal/lifecycle/admission"
	"github.com/halbritt/cairn/internal/lifecycle/value"
	"github.com/halbritt/cairn/internal/statefile"
)

const codexSchema = "cairn.codex-memory-grants/1"
const inboxSchema = "cairn.inbox-recall-grants/1"
const initSchema = "cairn.inbox-recall-initialized/1"

type Activation struct {
	Memory                 *value.Value
	Harness, Session, Turn string
	Enabled                bool
}

func invalid(message string) error { return &value.Error{Class: "ValueError", Message: message} }

func exists(path string) (bool, error) {
	_, err := os.Stat(path)
	if errors.Is(err, os.ErrNotExist) {
		return false, nil
	}
	return err == nil, err
}

func read(path string) (*value.Value, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	return value.DecodeText(raw, "utf-8", 0)
}

func save(path string, v *value.Value) error {
	yes, no := true, false
	var raw bytes.Buffer
	err := value.WriteKernel(&raw, v, value.WriterRoute{EnsureASCII: &yes, Compact: &yes, TrailingLF: &no, Codec: &value.Codec{Name: "utf-8", Errors: "strict"}}, 0)
	if err != nil {
		return err
	}
	return statefile.Replace(path, raw.Bytes())
}

func markerMatches(v, expected *value.Value) bool {
	if v == nil || v.Kind != value.Object || len(v.Members) != len(expected.Members) {
		return false
	}
	for _, m := range expected.Members {
		if !value.SameText(v.Get(string(m.Key)), string(m.Value.String)) {
			return false
		}
	}
	return true
}

// DecideActivation is grant_ledger.activation_decision's real read/lock/save
// operation. In particular Codex activation does not invoke the stricter turn
// grant reader; fields not examined by this source operation remain lossless.
func DecideActivation(ctx context.Context, in Activation) (bool, error) {
	dirValue := in.Memory.Get("state_dir")
	if dirValue == nil || dirValue.Kind != value.String {
		return false, invalid("activation ledger configuration lacks a state directory")
	}
	directory, err := admission.PathBytes(dirValue.String)
	if err != nil {
		return false, err
	}
	if !filepath.IsAbs(directory) {
		return false, invalid("activation ledger directory must be absolute")
	}
	if err := os.MkdirAll(directory, 0700); err != nil {
		return false, err
	}
	info, err := os.Stat(directory)
	if err != nil {
		return false, err
	}
	if info.Sys().(*syscall.Stat_t).Uid != uint32(os.Getuid()) || info.Mode().Perm()&0022 != 0 {
		return false, invalid("activation ledger directory is not owner-controlled")
	}
	enabled := in.Enabled
	err = statefile.LegacyLock(ctx, filepath.Join(directory, in.Session+".lock"), 1500*time.Millisecond, func() error {
		path := filepath.Join(directory, in.Session+".memory-budget.json")
		expected := value.Mapping(value.Field("schema", value.Text(codexSchema)), value.Field("session_id", value.Text(in.Session)))
		if in.Harness == "claude" {
			path = filepath.Join(directory, in.Session+".inbox-recall.json")
			expected = value.Mapping(value.Field("schema", value.Text(initSchema)), value.Field("session", value.Text(in.Session)))
		}
		marker := strings.TrimSuffix(path, ".json") + ".initialized"
		initialized, err := exists(marker)
		if err != nil {
			return err
		}
		present, err := exists(path)
		if err != nil {
			return err
		}
		if initialized {
			v, err := read(marker)
			if err != nil {
				return err
			}
			if !markerMatches(v, expected) || !present {
				return invalid("activation cannot reset initialized accounting")
			}
		}
		var state *value.Value
		if present {
			state, err = read(path)
			if err != nil {
				return err
			}
		} else if in.Harness == "claude" {
			state = value.Mapping(value.Field("schema", value.Text(inboxSchema)), value.Field("session", value.Text(in.Session)), value.Field("grants", value.Mapping()))
		} else {
			budget := in.Memory.Get("context_bytes")
			if budget == nil {
				budget = value.IntegerText("9500")
			}
			if !boundedInt(budget, 1000, 65536) {
				return invalid("invalid initial activation accounting limit")
			}
			state = value.Mapping(value.Field("schema", value.Text(codexSchema)), value.Field("session_id", value.Text(in.Session)), value.Field("active_turn_id", &value.Value{Kind: value.Null}), value.Field("turns", value.Mapping()), value.Field("pending", value.Mapping(value.Field("limit_bytes", budget), value.Field("emitted_hook_bytes", value.IntegerText("0")), value.Field("reserved_native_bytes", value.IntegerText("0")), value.Field("granted", value.Boolean(false)))))
		}
		if in.Harness == "claude" {
			if err := validateInbox(state, in.Session); err != nil {
				return err
			}
		} else if !value.SameText(state.Get("schema"), codexSchema) || !value.SameText(state.Get("session_id"), in.Session) {
			return invalid("activation ledger identity differs")
		}
		decisions := state.Get("inbox_activation")
		if decisions == nil {
			decisions = value.Mapping()
			state.Set("inbox_activation", decisions)
		}
		if decisions.Kind != value.Object {
			return invalid("invalid activation decisions")
		}
		for _, member := range decisions.Members {
			if member.Value.Kind != value.Bool {
				return invalid("invalid activation decisions")
			}
		}
		if prior := decisions.Get(in.Turn); prior != nil {
			enabled = prior.Boolean
			return nil
		}
		decisions.Set(in.Turn, value.Boolean(enabled))
		if !initialized {
			if err := save(marker, expected); err != nil {
				return err
			}
		}
		return save(path, state)
	})
	return enabled, err
}

func nonnegative(v *value.Value) bool {
	return v != nil && v.Kind == value.Int && !strings.HasPrefix(v.Integer, "-")
}
func boundedInt(v *value.Value, low, high int64) bool {
	if v == nil || v.Kind != value.Int {
		return false
	}
	n, ok := new(big.Int).SetString(v.Integer, 10)
	return ok && n.Cmp(big.NewInt(low)) >= 0 && n.Cmp(big.NewInt(high)) <= 0
}

var hex64 = regexp.MustCompile(`^[0-9a-f]{64}$`)
var hex16 = regexp.MustCompile(`^[0-9a-f]{16}$`)

func textMatches(v *value.Value, pattern *regexp.Regexp) bool {
	return v != nil && v.Kind == value.String && pattern.MatchString(string(v.String))
}

func validateInbox(state *value.Value, session string) error {
	grants := state.Get("grants")
	if !value.SameText(state.Get("schema"), inboxSchema) || !value.SameText(state.Get("session"), session) || grants == nil || grants.Kind != value.Object {
		return invalid("inbox recall grant ledger corrupt")
	}
	for _, m := range grants.Members {
		grant := m.Value
		if !hex64.MatchString(string(m.Key)) || grant.Kind != value.Object || grant.Get("identity") == nil || grant.Get("identity").Kind != value.Object {
			return invalid("inbox recall grant ledger corrupt")
		}
		for _, key := range []string{"limit_bytes", "output_bytes", "native_allowance_bytes"} {
			if !nonnegative(grant.Get(key)) {
				return invalid("inbox recall grant ledger corrupt")
			}
		}
		out, _ := new(big.Int).SetString(grant.Get("output_bytes").Integer, 10)
		native, _ := new(big.Int).SetString(grant.Get("native_allowance_bytes").Integer, 10)
		limit, _ := new(big.Int).SetString(grant.Get("limit_bytes").Integer, 10)
		if out.Add(out, native).Cmp(limit) > 0 {
			return invalid("inbox recall grant ledger corrupt")
		}
	}
	all := []*value.Value{state}
	for _, m := range grants.Members {
		all = append(all, m.Value)
	}
	for _, grant := range all {
		required := grant.Get("required")
		if required != nil && required.Kind != value.Null {
			if required.Kind != value.Object || !nonnegative(required.Get("count")) || required.Get("pending") == nil || required.Get("pending").Kind != value.Bool || required.Get("selection_sha256") == nil || required.Get("selection_sha256").Kind != value.String || (!value.SameText(required.Get("selection_sha256"), "") && !textMatches(required.Get("selection_sha256"), hex64)) {
				return invalid("inbox required-context status corrupt")
			}
		}
		if refresh := grant.Get("required_refresh_bytes"); refresh != nil && !nonnegative(refresh) {
			return invalid("inbox required refresh counter corrupt")
		}
	}
	if pending := state.Get("pending_bytes"); pending != nil && !boundedInt(pending, 0, 9500) {
		return invalid("inbox recall pending charge corrupt")
	}
	if active := state.Get("active"); active != nil && active.Kind != value.Null && (active.Kind != value.String || grants.Get(string(active.String)) == nil) {
		return invalid("inbox recall active grant missing")
	}
	if folded := state.Get("folded"); folded != nil && folded.Kind != value.Null {
		if folded.Kind != value.Object || len(folded.Members) != 2 || !nonnegative(folded.Get("count")) || folded.Get("tokens") == nil || folded.Get("tokens").Kind != value.Array {
			return invalid("inbox recall folded grants corrupt")
		}
		for _, token := range folded.Get("tokens").Items {
			if !textMatches(token, hex16) {
				return invalid("inbox recall folded grants corrupt")
			}
		}
	}
	return nil
}
