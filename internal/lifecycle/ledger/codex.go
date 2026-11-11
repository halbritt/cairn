package ledger

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"math/big"
	"strings"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/internal/lifecycle/value"
	"github.com/halbritt/cairn/internal/statefile"
)

// CodexBudget is the strict reader consumed by the SessionStart memory hook.
// Writers hold the memory session lock; a read-only refusal boundary may inspect
// an atomic snapshot without it. Unknown fields remain lossless.
func CodexBudget(path, session string, budget int) (*value.Value, error) {
	marker := strings.TrimSuffix(path, ".json") + ".initialized"
	initialized, err := exists(marker)
	if err != nil {
		return nil, err
	}
	if initialized {
		v, err := read(marker)
		if err != nil {
			var source *value.Error
			if errors.As(err, &source) {
				return nil, invalid("native-turn memory initialization marker is corrupt")
			}
			return nil, err
		}
		if !markerMatches(v, value.Mapping(value.Field("schema", value.Text(codexSchema)), value.Field("session_id", value.Text(session)))) {
			return nil, invalid("native-turn memory initialization marker is corrupt")
		}
	}
	present, err := exists(path)
	if err != nil {
		return nil, err
	}
	if !present {
		if initialized {
			return nil, invalid("native-turn memory ledger is missing")
		}
		return value.Mapping(value.Field("schema", value.Text(codexSchema)), value.Field("session_id", value.Text(session)), value.Field("active_turn_id", &value.Value{Kind: value.Null}), value.Field("pending", value.Mapping(value.Field("limit_bytes", integer(budget)), value.Field("emitted_hook_bytes", integer(0)), value.Field("reserved_native_bytes", integer(0)), value.Field("granted", value.Boolean(false)))), value.Field("turns", value.Mapping())), nil
	}
	v, err := read(path)
	if err != nil {
		var source *value.Error
		if errors.As(err, &source) {
			return nil, invalid("native-turn memory ledger is corrupt; no allowance renewed")
		}
		return nil, err
	}
	bad := func() (*value.Value, error) {
		return nil, invalid("native-turn memory ledger is corrupt; no allowance renewed")
	}
	turns, active, pending := v.Get("turns"), v.Get("active_turn_id"), v.Get("pending")
	if !value.SameText(v.Get("schema"), codexSchema) || !value.SameText(v.Get("session_id"), session) || turns == nil || turns.Kind != value.Object || active == nil || (active.Kind == value.Null && len(turns.Members) != 0) || (active.Kind != value.Null && (active.Kind != value.String || turns.Get(string(active.String)) == nil)) {
		return bad()
	}
	grants := []*value.Value{pending}
	for _, m := range turns.Members {
		if !uuidText(string(m.Key)) {
			return bad()
		}
		grants = append(grants, m.Value)
	}
	for _, g := range grants {
		if g == nil || g.Kind != value.Object || g.Get("granted") == nil || g.Get("granted").Kind != value.Bool || !boundedInt(g.Get("limit_bytes"), 1000, 65536) || !validNonnegative(g.Get("emitted_hook_bytes")) || !validNonnegative(g.Get("reserved_native_bytes")) {
			return bad()
		}
		emitted, reserved, limit := integerValue(g.Get("emitted_hook_bytes")), integerValue(g.Get("reserved_native_bytes")), integerValue(g.Get("limit_bytes"))
		if new(big.Int).Add(emitted, reserved).Cmp(limit) > 0 || (!g.Get("granted").Boolean && reserved.Sign() != 0) {
			return bad()
		}
		if r := g.Get("required"); r != nil {
			if r.Kind != value.Object || !validNonnegative(r.Get("count")) || r.Get("pending") == nil || r.Get("pending").Kind != value.Bool || r.Get("selection_sha256") == nil || r.Get("selection_sha256").Kind != value.String {
				return bad()
			}
			if integerValue(r.Get("count")).Sign() > 0 {
				if !textMatches(r.Get("selection_sha256"), hex64) {
					return bad()
				}
			} else if !value.SameText(r.Get("selection_sha256"), "") {
				return bad()
			}
		}
		for _, key := range []string{"required_refresh_bytes", "checkpoint_refresh_bytes", "serialized_hook_bytes"} {
			if n := g.Get(key); n != nil && !validNonnegative(n) {
				return bad()
			}
		}
	}
	if pending.Get("granted").Boolean || (active.Kind != value.Null && integerValue(pending.Get("emitted_hook_bytes")).Sign() != 0) {
		return bad()
	}
	return v, nil
}

func integer(n int) *value.Value { return value.IntegerText(big.NewInt(int64(n)).String()) }
func integerValue(v *value.Value) *big.Int {
	if v == nil || v.Kind != value.Int {
		return nil
	}
	n, _ := new(big.Int).SetString(v.Integer, 10)
	return n
}
func validNonnegative(v *value.Value) bool { n := integerValue(v); return n != nil && n.Sign() >= 0 }
func uuidText(s string) bool {
	id, err := uuid.Parse(s)
	return err == nil && id != uuid.Nil && id.String() == s
}

// SaveCodexBudget durably writes the anti-reset marker before the ledger.
func SaveCodexBudget(path string, state *value.Value) error {
	marker := strings.TrimSuffix(path, ".json") + ".initialized"
	present, err := exists(marker)
	if err != nil {
		return err
	}
	if !present {
		if err = saveCodex(marker, value.Mapping(value.Field("schema", value.Text(codexSchema)), value.Field("session_id", state.Get("session_id")))); err != nil {
			return err
		}
	}
	return saveCodex(path, state)
}

func saveCodex(path string, v *value.Value) error {
	var raw bytes.Buffer
	yes, no := true, false
	if err := value.WriteKernel(&raw, v, value.WriterRoute{EnsureASCII: &no, Compact: &yes, TrailingLF: &no, Codec: &value.Codec{Name: "utf-8", Errors: "strict"}}, 0); err != nil {
		return err
	}
	return statefile.Replace(path, raw.Bytes())
}

// CodexRequiredSelection records the exact serialized current selection.
func CodexRequiredSelection(grant *value.Value, selected *value.Value, encoded []byte) {
	digest := ""
	if len(selected.Items) > 0 {
		sum := sha256.Sum256(encoded)
		digest = hex.EncodeToString(sum[:])
	}
	grant.Set("required", value.Mapping(value.Field("count", integer(len(selected.Items))), value.Field("selection_sha256", value.Text(digest)), value.Field("pending", value.Boolean(false))))
}

// CodexCurrentGrant never allocates or changes a native turn at SessionStart.
func CodexCurrentGrant(state *value.Value) *value.Value {
	active := state.Get("active_turn_id")
	if active == nil || active.Kind == value.Null {
		return state.Get("pending")
	}
	return state.Get("turns").Get(string(active.String))
}

// AddCodexCounter preserves arbitrary-size integer counters written by Python.
func AddCodexCounter(grant *value.Value, key string, n int) {
	previous := integerValue(grant.Get(key))
	if previous == nil {
		previous = new(big.Int)
	}
	grant.Set(key, value.IntegerText(previous.Add(previous, big.NewInt(int64(n))).String()))
}
