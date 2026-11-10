package binding

import (
	"encoding/binary"
	"math"
	"math/big"

	"github.com/halbritt/cairn/internal/lifecycle/value"
)

// configurationProfiles is the source predicate boundary after descriptor
// configuration loads, in their source order. These are decoded JSON values,
// not arbitrary native Python objects. ManifestPath is the source manifest
// Path's lexical spelling; Resolve represents Path(value).resolve(), including
// its conversion errors. It supplies no filesystem/runtime/profile default.
// The production caller, loader and parser selection remain separate.
type configurationProfiles struct {
	Current, Memory *value.Value
	Coordinators    []*value.Value
	ManifestPath    []rune
	Resolve         func(*value.Value) ([]rune, error)
}

// ConfigurationProfiles exposes the /3 application configuration predicate
// inputs for downstream callers such as cmd/cairn coordination binding checks.
type ConfigurationProfiles = configurationProfiles

// ValidateConfigurationProfiles validates the /3 profile-agreement and
// 1000..9500 byte-budget predicates on already-decoded configurations.
func ValidateConfigurationProfiles(in ConfigurationProfiles) error {
	return validateConfigurationProfiles(in)
}

func profileFailure(class, message string) error { return &value.Error{Class: class, Message: message} }
func incompatibleProfile() error {
	return profileFailure("ValueError", "incompatible inbox recall profile binding")
}
func profileObject(v *value.Value) error {
	if v == nil || v.Kind != value.Object {
		return profileFailure("AttributeError", "source object has no attribute 'get'")
	}
	return nil
}
func profileIndex(v *value.Value, key string) (*value.Value, error) {
	if err := profileObject(v); err != nil {
		return nil, err
	}
	item := v.Get(key)
	if item == nil {
		return nil, profileFailure("KeyError", key)
	}
	return item, nil
}
func profileString(v *value.Value, s string) bool {
	return v != nil && v.Kind == value.String && equalCodePoints(v.String, []rune(s))
}
func equalCodePoints(a, b []rune) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}
func profileTruthy(v *value.Value) bool {
	if v == nil {
		return false
	}
	switch v.Kind {
	case value.Bool:
		return v.Boolean
	case value.Int:
		return v.Integer != "0"
	case value.Float:
		return v.Number != 0
	case value.String:
		return len(v.String) != 0
	case value.Array:
		return len(v.Items) != 0
	case value.Object:
		return len(v.Members) != 0
	}
	return false
}
func profileAbsolute(v *value.Value) (bool, error) {
	if v == nil || v.Kind != value.String {
		return false, profileFailure("TypeError", "source path conversion requires a string")
	}
	return len(v.String) != 0 && v.String[0] == '/', nil
}
func (in configurationProfiles) resolve(path *value.Value) ([]rune, error) {
	if in.Resolve == nil {
		return nil, profileFailure("ExplicitInputError", "source path resolution callback required")
	}
	return in.Resolve(path)
}

// validateConfigurationProfiles ports only InboxBinding.validate's application
// configuration predicates. It does not load/verify a manifest, binary, receipt
// or configuration, select a parser/runtime, activate a binding, or authorize a
// store operation. Its actual callable consumer is required before landing.
func validateConfigurationProfiles(in configurationProfiles) error {
	if len(in.Coordinators) < 1 || len(in.Coordinators) > 8 {
		return profileFailure("ExplicitInputError", "descriptor count must already be checked before configuration loads")
	}
	homes := make([][]rune, len(in.Coordinators))
	missingHome := false
	uniqueHomes := make(map[string]struct{})
	for i, config := range in.Coordinators {
		if err := profileObject(config); err != nil {
			return err
		}
		home := config.Get("config_home")
		if !profileTruthy(home) {
			missingHome = true
			continue
		}
		resolved, err := in.resolve(home)
		if err != nil {
			return err
		}
		homes[i] = resolved
		uniqueHomes[codePointKey(resolved)] = struct{}{}
	}
	if len(in.Coordinators) > 1 && (missingHome || len(uniqueHomes) != len(homes)) {
		return profileFailure("ValueError", "shared memory binding requires distinct explicit configuration homes")
	}
	if err := profileObject(in.Current); err != nil {
		return err
	}
	if !profileString(in.Current.Get("harness"), "codex") && !profileString(in.Current.Get("harness"), "claude") {
		return incompatibleProfile()
	}
	if err := profileObject(in.Memory); err != nil {
		return err
	}
	if !profileString(in.Memory.Get("recall_mode"), "agent_tools") {
		return incompatibleProfile()
	}
	for _, config := range in.Coordinators {
		if !profileTruthy(config.Get("native_delivery")) {
			return incompatibleProfile()
		}
	}
	profiles := make([]*value.Value, 0, 1+len(in.Coordinators))
	profiles = append(profiles, in.Memory)
	profiles = append(profiles, in.Coordinators...)
	for _, config := range profiles {
		path := config.Get("inbox_recall_binding")
		if path == nil || path.Kind != value.String || !equalCodePoints(path.String, in.ManifestPath) {
			return incompatibleProfile()
		}
	}
	for _, config := range profiles {
		for _, key := range []string{"harness", "cairn", "socket", "token_file", "repo"} {
			equal, err := equalJSONProfiles(config.Get(key), in.Memory.Get(key), false)
			if err != nil {
				return err
			}
			if !equal {
				return incompatibleProfile()
			}
		}
	}
	member := false
	for _, profile := range profiles {
		// Python list membership compares each profile item with the target.
		equal, err := equalJSONProfiles(profile, in.Current, true)
		if err != nil {
			return err
		}
		if equal {
			member = true
			break
		}
	}
	if !member {
		return incompatibleProfile()
	}
	for _, config := range profiles {
		path, err := profileIndex(config, "state_dir")
		if err != nil {
			return err
		}
		absolute, err := profileAbsolute(path)
		if err != nil {
			return err
		}
		if !absolute {
			return incompatibleProfile()
		}
	}
	for _, config := range in.Coordinators {
		// Preserve the repeated memory resolution and left-to-right error order.
		memory, err := in.resolve(in.Memory.Get("state_dir"))
		if err != nil {
			return err
		}
		coordination, err := in.resolve(config.Get("state_dir"))
		if err != nil {
			return err
		}
		if equalCodePoints(memory, coordination) {
			return incompatibleProfile()
		}
	}
	for _, config := range in.Coordinators {
		path := config.Get("config_home")
		if !profileTruthy(path) {
			continue
		}
		absolute, err := profileAbsolute(path)
		if err != nil {
			return err
		}
		if !absolute {
			return incompatibleProfile()
		}
	}
	budget := in.Memory.Get("context_bytes")
	// This is the existing 1000..9500 byte-budget predicate, not a depth rule.
	if budget == nil || budget.Kind != value.Int || len(budget.Integer) != 4 || budget.Integer < "1000" || budget.Integer > "9500" {
		return incompatibleProfile()
	}
	return nil
}

func codePointKey(raw []rune) string {
	bytes := make([]byte, 4*len(raw))
	for i, point := range raw {
		binary.LittleEndian.PutUint32(bytes[4*i:], uint32(point))
	}
	return string(bytes)
}

type profileComparison struct {
	left, right *value.Value
	identity    bool
	lookup      map[string]*value.Value
	key         []rune
}

// equalJSONProfiles models Python comparisons specifically for decoded JSON.
// Its NaNs are json.loads' shared parse-constant object. Container comparisons
// and membership use the identity shortcut; a direct scalar operator does not.
// This is not equality for arbitrary native objects/freshly created NaNs.
// Iteration avoids choosing a Go recursion/depth domain for the future caller.
func equalJSONProfiles(left, right *value.Value, identity bool) (bool, error) {
	stack := []profileComparison{{left: left, right: right, identity: identity}}
	for len(stack) != 0 {
		pair := stack[len(stack)-1]
		stack = stack[:len(stack)-1]
		a, b := pair.left, pair.right
		if pair.lookup != nil {
			var present bool
			b, present = pair.lookup[codePointKey(pair.key)]
			if !present {
				return false, nil
			}
		}
		aNone, bNone := a == nil || a.Kind == value.Null, b == nil || b.Kind == value.Null
		if aNone || bNone {
			if aNone != bNone {
				return false, nil
			}
			continue
		}
		if pair.identity && (a == b || a.Kind == value.Float && b.Kind == value.Float && math.IsNaN(a.Number) && math.IsNaN(b.Number)) {
			continue
		}
		if profileNumeric(a) && profileNumeric(b) {
			equal, err := equalProfileNumbers(a, b)
			if err != nil || !equal {
				return equal, err
			}
			continue
		}
		if a.Kind != b.Kind {
			return false, nil
		}
		switch a.Kind {
		case value.String:
			if !equalCodePoints(a.String, b.String) {
				return false, nil
			}
		case value.Array:
			if len(a.Items) != len(b.Items) {
				return false, nil
			}
			for i := len(a.Items) - 1; i >= 0; i-- {
				stack = append(stack, profileComparison{left: a.Items[i], right: b.Items[i], identity: true})
			}
		case value.Object:
			if len(a.Members) != len(b.Members) {
				return false, nil
			}
			other := make(map[string]*value.Value, len(b.Members))
			for _, item := range b.Members {
				other[codePointKey(item.Key)] = item.Value
			}
			for i := len(a.Members) - 1; i >= 0; i-- {
				item := a.Members[i]
				stack = append(stack, profileComparison{left: item.Value, lookup: other, key: item.Key, identity: true})
			}
		default:
			return false, profileFailure("ExplicitInputError", "decoded JSON kind required")
		}
	}
	return true, nil
}

func profileNumeric(v *value.Value) bool {
	return v.Kind == value.Bool || v.Kind == value.Int || v.Kind == value.Float
}
func profileInteger(v *value.Value) (*big.Int, error) {
	if v.Kind == value.Bool {
		if v.Boolean {
			return big.NewInt(1), nil
		}
		return big.NewInt(0), nil
	}
	number, valid := new(big.Int).SetString(v.Integer, 10)
	if !valid {
		return nil, profileFailure("ExplicitInputError", "decoded canonical integer required")
	}
	return number, nil
}
func equalProfileNumbers(a, b *value.Value) (bool, error) {
	if a.Kind == value.Float && b.Kind == value.Float {
		return a.Number == b.Number, nil
	}
	if a.Kind != value.Float && b.Kind != value.Float {
		left, err := profileInteger(a)
		if err != nil {
			return false, err
		}
		right, err := profileInteger(b)
		if err != nil {
			return false, err
		}
		return left.Cmp(right) == 0, nil
	}
	if b.Kind == value.Float {
		a, b = b, a
	}
	if math.IsNaN(a.Number) || math.IsInf(a.Number, 0) {
		return false, nil
	}
	integer, err := profileInteger(b)
	if err != nil {
		return false, err
	}
	rational := new(big.Rat).SetFloat64(a.Number)
	return rational.Denom().Cmp(big.NewInt(1)) == 0 && rational.Num().Cmp(integer) == 0, nil
}
