package coordination

import (
	"context"
	"errors"
	"math/big"
	"os"
	"strings"
	"syscall"

	"github.com/halbritt/cairn/internal/lifecycle/journal"
	"github.com/halbritt/cairn/internal/lifecycle/value"
)

const (
	SiteGrantLedger   = "coordination.grant.ledger"
	SiteGrantMarker   = "coordination.grant.marker"
	SiteCodexBudget   = "coordination.grant.codex_budget"
	InboxLedgerSchema = "cairn.inbox-recall-grants/1"
	InboxInitSchema   = "cairn.inbox-recall-initialized/1"
	CodexLedgerSchema = "cairn.codex-memory-grants/1"
)

type GrantLedgerError struct{ Message, Stage, Reason string }

func (e *GrantLedgerError) Error() string { return e.Message }
func (e *GrantLedgerError) Unwrap() error {
	return &value.Error{Class: "ValueError", Message: e.Message}
}
func grantRefusal(message, reason string) error {
	return &GrantLedgerError{Message: message, Stage: "inbox_ledger", Reason: reason}
}

type GrantBindings struct {
	Profile        *value.SourceReaderProfile
	MemoryStateDir func(*value.Value) (*value.Value, error)
	Loader         func(string, *value.Value) (*value.Value, error)
	UID            *uint32
}

func (g GrantBindings) uid() uint32 {
	if g.UID != nil {
		return *g.UID
	}
	return uint32(os.Getuid())
}
func sourceJoin(dir, name string) string {
	if strings.HasPrefix(name, "/") || dir == "" {
		return purePosixPath(name)
	}
	return purePosixPath(strings.TrimRight(dir, "/") + "/" + name)
}
func sourceExists(path string, lstat bool) (bool, error) {
	var err error
	if lstat {
		_, err = os.Lstat(path)
	} else {
		_, err = os.Stat(path)
	}
	if errors.Is(err, os.ErrNotExist) || errors.Is(err, syscall.ENOTDIR) {
		return false, nil
	}
	return err == nil, err
}
func (g GrantBindings) read(path, site string) (*value.Value, error) {
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	return g.Profile.Decode(site, raw)
}
func nonnegativeInt(v *value.Value) (*big.Int, bool) {
	if v == nil || v.Kind != value.Int {
		return nil, false
	}
	n, ok := new(big.Int).SetString(v.Integer, 10)
	return n, ok && n.Sign() >= 0
}
func hexText(points []rune, size int) bool {
	if len(points) != size {
		return false
	}
	for _, r := range points {
		if !(r >= '0' && r <= '9' || r >= 'a' && r <= 'f') {
			return false
		}
	}
	return true
}
func valueMember(object, key *value.Value) *value.Value {
	if object == nil || object.Kind != value.Object || key == nil || key.Kind != value.String {
		return nil
	}
	for _, m := range object.Members {
		if string(m.Key) == string(key.String) {
			same := len(m.Key) == len(key.String)
			for i := range m.Key {
				same = same && m.Key[i] == key.String[i]
			}
			if same {
				return m.Value
			}
		}
	}
	return nil
}

// LoadGrants preserves the Source ledger's validation order and integer types.
func (g GrantBindings) LoadGrants(path string, session *value.Value) (*value.Value, error) {
	marker := strings.TrimSuffix(path, ".json") + ".initialized"
	present, err := sourceExists(marker, false)
	if err != nil {
		return nil, err
	}
	if present {
		v, err := g.read(marker, SiteGrantMarker)
		if err != nil {
			return nil, err
		}
		if !nativeEqual(v, nativeMap("schema", InboxInitSchema, "session", session)) {
			return nil, grantRefusal("inbox recall initialization marker corrupt", "marker")
		}
	}
	present, err = sourceExists(marker, false)
	if err != nil {
		return nil, err
	}
	exists, err := sourceExists(path, false)
	if err != nil {
		return nil, err
	}
	if present && !exists {
		return nil, grantRefusal("inbox recall grant ledger missing", "missing")
	}
	ledger := nativeMap("schema", InboxLedgerSchema, "session", session, "grants", value.Mapping())
	if exists {
		ledger, err = g.read(path, SiteGrantLedger)
		if err != nil {
			return nil, err
		}
	}
	if ledger == nil || ledger.Kind != value.Object {
		return nil, &journal.NonObjectError{Field: "grant ledger"}
	}
	grants := ledger.Get("grants")
	if !value.SameText(ledger.Get("schema"), InboxLedgerSchema) || !nativeEqual(ledger.Get("session"), session) || grants == nil || grants.Kind != value.Object {
		return nil, grantRefusal("inbox recall grant ledger corrupt", "shape")
	}
	checks := []*value.Value{ledger}
	for _, member := range grants.Members {
		grant := member.Value
		if !hexText(member.Key, 64) || grant == nil || grant.Kind != value.Object || grant.Get("identity") == nil || grant.Get("identity").Kind != value.Object {
			return nil, grantRefusal("inbox recall grant ledger corrupt", "shape")
		}
		limit, lok := nonnegativeInt(grant.Get("limit_bytes"))
		output, ook := nonnegativeInt(grant.Get("output_bytes"))
		allowance, aok := nonnegativeInt(grant.Get("native_allowance_bytes"))
		if !lok || !ook || !aok || new(big.Int).Add(output, allowance).Cmp(limit) > 0 {
			return nil, grantRefusal("inbox recall grant ledger corrupt", "shape")
		}
		checks = append(checks, grant)
	}
	for _, grant := range checks {
		required := grant.Get("required")
		if !nativeNone(required) {
			valid := required.Kind == value.Object
			_, count := nonnegativeInt(required.Get("count"))
			pending := required.Get("pending")
			selection := required.Get("selection_sha256")
			if !valid || !count || pending == nil || pending.Kind != value.Bool || selection == nil || selection.Kind != value.String || len(selection.String) != 0 && !hexText(selection.String, 64) {
				return nil, grantRefusal("inbox required-context status corrupt", "required_status")
			}
		}
		refresh := grant.Get("required_refresh_bytes")
		if refresh == nil {
			refresh = value.IntegerText("0")
		}
		if _, ok := nonnegativeInt(refresh); !ok {
			return nil, grantRefusal("inbox required refresh counter corrupt", "refresh_counter")
		}
	}
	pending := ledger.Get("pending_bytes")
	if pending == nil {
		pending = value.IntegerText("0")
	}
	n, ok := nonnegativeInt(pending)
	if !ok || n.Cmp(big.NewInt(9500)) > 0 {
		return nil, grantRefusal("inbox recall pending charge corrupt", "pending_charge")
	}
	active := ledger.Get("active")
	if !nativeNone(active) {
		if active.Kind == value.Array || active.Kind == value.Object {
			return nil, &journal.TypeError{Field: "active grant key"}
		}
		if valueMember(grants, active) == nil {
			return nil, grantRefusal("inbox recall active grant missing", "active_grant")
		}
	}
	folded := ledger.Get("folded")
	if !nativeNone(folded) {
		_, count := nonnegativeInt(folded.Get("count"))
		tokens := folded.Get("tokens")
		if !nativeHasKeys(folded, "count", "tokens") || !count || tokens == nil || tokens.Kind != value.Array {
			return nil, grantRefusal("inbox recall folded grants corrupt", "folded")
		}
		for _, token := range tokens.Items {
			if token == nil || token.Kind != value.String || !hexText(token.String, 16) {
				return nil, grantRefusal("inbox recall folded grants corrupt", "folded")
			}
		}
	}
	return ledger, nil
}

func (g GrantBindings) directory(config *value.Value) (string, error) {
	var dir *value.Value
	var err error
	if g.MemoryStateDir != nil {
		dir, err = g.MemoryStateDir(config)
	} else {
		if config == nil || config.Kind != value.Object {
			return "", &journal.NonObjectError{Field: "config"}
		}
		binding, err := nativeRequired(config, "inbox_recall_binding")
		if err != nil {
			return "", err
		}
		if binding.Kind != value.String {
			return "", &journal.TypeError{Field: "inbox_recall_binding"}
		}
		name := journalText(binding.String)
		if !strings.HasPrefix(name, "/") || strings.ContainsRune(name, 0) {
			return "", &value.Error{Class: "ValueError", Message: "invalid inbox recall binding path"}
		}
		return "", &value.ProfileUnavailable{}
	}
	if err != nil {
		return "", err
	}
	return sourcePathText(g.Profile, dir)
}
func (g GrantBindings) native(state *value.Value) (*value.Value, string, error) {
	agent, err := nativeRequired(state, "agent")
	if err != nil {
		return nil, "", err
	}
	native, err := nativeRequired(agent, "native_session_id")
	if err != nil {
		return nil, "", err
	}
	name, err := sourcePathText(g.Profile, native)
	return native, name, err
}
func ownerFile(info os.FileInfo, uid uint32) bool {
	stat, ok := info.Sys().(*syscall.Stat_t)
	return ok && stat.Uid == uid && info.Mode().IsRegular()
}
func controlledDirectory(info os.FileInfo, uid uint32) bool {
	stat, ok := info.Sys().(*syscall.Stat_t)
	return ok && stat.Uid == uid && info.Mode().Perm()&0022 == 0
}
func grantKind(identity *value.Value) string {
	boundary := nativeText(identity.Get("boundary"))
	if value.Truth(identity.Get("attempt_id")) || value.Truth(identity.Get("delivery_id")) || nativeContains([]string{"claude_prompt_delivery", "native_turn"}, boundary) {
		return "inbox_delivery"
	}
	if nativeContains([]string{"ordinary_prompt", "launcher_task"}, boundary) {
		return "ordinary_context"
	}
	if boundary == "unadmitted_wake" {
		return boundary
	}
	return "unknown"
}

func (g GrantBindings) Evidence(ctx context.Context, config, state, attempt, nativeOwner *value.Value, requireFiles bool) (string, *value.Value, error) {
	owner := nativeOwner
	if nativeNone(owner) {
		a, err := nativeObj(attempt, "attempt")
		if err != nil {
			return "", nil, err
		}
		owner = a.Get("native_turn_id")
	}
	status, kind, err := g.evidence(ctx, config, state, attempt, owner, requireFiles)
	if err != nil {
		var profile *value.ProfileUnavailable
		if errors.As(err, &profile) {
			return "", nil, err
		}
		return "unknown", value.Text("unknown"), nil
	}
	return status, kind, nil
}
func (g GrantBindings) evidence(_ context.Context, config, state, attempt, owner *value.Value, requireFiles bool) (string, *value.Value, error) {
	dir, err := g.directory(config)
	if err != nil {
		return "", nil, err
	}
	native, name, err := g.native(state)
	if err != nil {
		return "", nil, err
	}
	if !requireFiles && strings.HasPrefix(dir, "/") {
		exists, err := sourceExists(dir, true)
		if err != nil && !sourceOSError(err) {
			return "", nil, err
		}
		if !exists {
			return "absent", nil, nil
		}
	}
	info, err := os.Stat(dir)
	if err != nil {
		return "", nil, err
	}
	if !strings.HasPrefix(dir, "/") || !controlledDirectory(info, g.uid()) {
		return "unknown", value.Text("unknown"), nil
	}
	lock, err := os.OpenFile(sourceJoin(dir, name+".lock"), os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0666)
	if err != nil {
		return "", nil, err
	}
	defer lock.Close()
	if err = syscall.Flock(int(lock.Fd()), syscall.LOCK_EX|syscall.LOCK_NB); err != nil {
		if errors.Is(err, syscall.EWOULDBLOCK) || errors.Is(err, syscall.EAGAIN) {
			return "busy", value.Text("unknown"), nil
		}
		return "", nil, err
	}
	path := sourceJoin(dir, name+".inbox-recall.json")
	for _, required := range []string{path, strings.TrimSuffix(path, ".json") + ".initialized"} {
		info, err := os.Lstat(required)
		if errors.Is(err, os.ErrNotExist) {
			if requireFiles {
				return "unknown", value.Text("unknown"), nil
			}
			continue
		}
		if err != nil {
			return "", nil, err
		}
		if !ownerFile(info, g.uid()) {
			return "unknown", value.Text("unknown"), nil
		}
	}
	loader := g.Loader
	if loader == nil {
		loader = g.LoadGrants
	}
	ledger, err := loader(path, native)
	if err != nil {
		return "", nil, err
	}
	kinds := map[string]bool{}
	grants, err := nativeRequired(ledger, "grants")
	if err != nil {
		return "", nil, err
	}
	if grants.Kind != value.Object {
		return "", nil, &journal.NonObjectError{Field: "grants"}
	}
	for _, member := range grants.Members {
		identity, err := nativeRequired(member.Value, "identity")
		if err != nil {
			return "", nil, err
		}
		if identity.Kind != value.Object {
			return "", nil, &journal.NonObjectError{Field: "identity"}
		}
		matched := false
		if value.Truth(attempt) {
			id, err := nativeRequired(attempt, "attempt_id")
			if err != nil {
				return "", nil, err
			}
			matched = nativeEqual(identity.Get("attempt_id"), id)
			if !matched {
				delivery, err := nativeRequired(attempt, "delivery")
				if err != nil {
					return "", nil, err
				}
				id, err = nativeRequired(delivery, "delivery_id")
				if err != nil {
					return "", nil, err
				}
				matched = nativeEqual(identity.Get("delivery_id"), id)
			}
		}
		if matched {
			kinds["inbox_delivery"] = true
		} else if value.Truth(owner) && nativeEqual(identity.Get("native_owner"), owner) {
			kinds[grantKind(identity)] = true
		}
	}
	if len(kinds) == 0 {
		return "absent", nil, nil
	}
	if len(kinds) > 1 {
		return "present", value.Text("mixed"), nil
	}
	kind := "unknown"
	for observed := range kinds {
		kind = observed
	}
	return "present", value.Text(kind), nil
}
func (g GrantBindings) GrantEvidence(ctx context.Context, config, state, attempt *value.Value) (string, *value.Value, error) {
	return g.Evidence(ctx, config, state, attempt, nil, true)
}
func (g GrantBindings) GrantState(ctx context.Context, config, state, attempt *value.Value) (string, error) {
	status, _, err := g.GrantEvidence(ctx, config, state, attempt)
	if status == "busy" {
		status = "unknown"
	}
	return status, err
}
func (g GrantBindings) PreGrantLedgerAbsent(_ context.Context, config, state, attempt, recorded *value.Value) (bool, error) {
	if attempt == nil || attempt.Kind != value.Object {
		return false, &journal.NonObjectError{Field: "attempt"}
	}
	turn := attempt.Get("native_turn_id")
	if !value.SameText(config.Get("harness"), "codex") {
		return false, nil
	}
	if recorded == nil || recorded.Kind != value.Object {
		return false, &journal.NonObjectError{Field: "recorded"}
	}
	if !value.SameText(recorded.Get("recall_phase"), "required_search") || !value.SameText(recorded.Get("grant"), "not_checked") || turn == nil || turn.Kind != value.String {
		return false, nil
	}
	ok, err := g.preGrant(config, state, turn)
	if err != nil {
		var profile *value.ProfileUnavailable
		if errors.As(err, &profile) {
			return false, err
		}
		return false, nil
	}
	return ok, nil
}
func (g GrantBindings) preGrant(config, state, turn *value.Value) (bool, error) {
	dir, err := g.directory(config)
	if err != nil {
		return false, err
	}
	native, name, err := g.native(state)
	if err != nil {
		return false, err
	}
	info, err := os.Stat(dir)
	if err != nil {
		return false, err
	}
	if !strings.HasPrefix(dir, "/") || !controlledDirectory(info, g.uid()) {
		return false, nil
	}
	lock, err := os.OpenFile(sourceJoin(dir, name+".lock"), os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0666)
	if err != nil {
		return false, err
	}
	defer lock.Close()
	if err = syscall.Flock(int(lock.Fd()), syscall.LOCK_EX|syscall.LOCK_NB); err != nil {
		return false, err
	}
	for _, path := range []string{sourceJoin(dir, name+".inbox-recall.json"), sourceJoin(dir, name+".inbox-recall.initialized")} {
		exists, err := sourceExists(path, true)
		if err != nil && !sourceOSError(err) {
			return false, err
		}
		if exists {
			return false, nil
		}
	}
	path := sourceJoin(dir, name+".memory-budget.json")
	info, err = os.Lstat(path)
	if err != nil {
		return false, err
	}
	if !ownerFile(info, g.uid()) {
		return false, nil
	}
	budget, err := g.read(path, SiteCodexBudget)
	if err != nil {
		return false, err
	}
	if budget == nil || budget.Kind != value.Object || !value.SameText(budget.Get("schema"), CodexLedgerSchema) || !nativeEqual(budget.Get("session_id"), native) {
		return false, nil
	}
	activation, turns := budget.Get("inbox_activation"), budget.Get("turns")
	active := valueMember(activation, turn)
	return activation != nil && activation.Kind == value.Object && active != nil && active.Kind == value.Bool && active.Boolean && turns != nil && turns.Kind == value.Object && valueMember(turns, turn) == nil, nil
}
