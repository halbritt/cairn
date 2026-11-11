package memoryhook

import (
	"context"
	"errors"
	"math/big"
	"os"
	"path/filepath"
	"strings"

	"github.com/halbritt/cairn/internal/lifecycle/ledger"
	"github.com/halbritt/cairn/internal/lifecycle/value"
	"github.com/halbritt/cairn/internal/statefile"
)

func boundClaude(config *value.Value) bool {
	return value.SameText(config.Get("harness"), "claude") && value.Truth(config.Get("inbox_recall_binding"))
}

func claudePromptOwner(event *value.Value) string {
	prompt := event.Get("prompt_id")
	if !validText(prompt) || strip(text(prompt)) == "" || len(text(prompt)) > 256 {
		return ""
	}
	for _, r := range prompt.String {
		if r < 32 {
			return ""
		}
	}
	return "claude-channel:" + text(prompt)
}

func claudeTaskKey(config *value.Value) (string, error) {
	key := config.Get("recall_task_key")
	if key == nil {
		return "", nil
	}
	if !nativeUUID(key) || !boundClaude(config) || !value.SameText(config.Get("recall_mode"), "agent_tools") {
		return "", fail("invalid explicit recall_task_key or unsupported recall route")
	}
	return text(key), nil
}

func claudeGrantMatches(grant *value.Value, owner, task string) bool {
	identity := grant.Get("identity")
	if task != "" {
		return value.SameText(identity.Get("recall_task_key"), task)
	}
	return value.SameText(identity.Get("native_owner"), owner) && !value.Truth(identity.Get("recall_task_key"))
}

// claudeRequiredGrant reads ownership only. Neither this lookup nor the public
// failure guard allocates a delivery allowance or treats prompt metadata as auth.
func claudeRequiredGrant(state, event *value.Value, task string) *value.Value {
	if value.SameText(event.Get("hook_event_name"), "UserPromptSubmit") && claudePromptOwner(event) != "" {
		for _, item := range state.Get("grants").Members {
			if claudeGrantMatches(item.Value, claudePromptOwner(event), task) {
				return item.Value
			}
		}
		if !value.Truth(state.Get("active")) {
			return state
		}
		active := state.Get("grants").Get(text(state.Get("active")))
		if value.Truth(active.Get("required").Get("pending")) {
			return active
		}
		return nil
	}
	if value.Truth(state.Get("active")) {
		return state.Get("grants").Get(text(state.Get("active")))
	}
	return state
}

func claudeLedgerPath(config, event *value.Value) (string, error) {
	directory, err := pathValue(config.Get("state_dir"))
	return filepath.Join(directory, text(event.Get("session_id"))+".inbox-recall.json"), err
}

func loadClaudeLedger(path, session string) (*value.Value, error) {
	state, err := ledger.InboxGrants(path, session)
	var refused *ledger.InboxRefusal
	if errors.As(err, &refused) {
		err = &value.Error{Class: "ValueError", Message: refused.Message}
	}
	return state, err
}

// Initialization is durable before publication. A surviving marker with a
// missing ledger is refused by InboxGrants rather than renewing the allowance.
func saveClaudeLedger(path string, state *value.Value) error {
	marker := strings.TrimSuffix(path, ".json") + ".initialized"
	if _, err := os.Stat(marker); errors.Is(err, os.ErrNotExist) {
		if err = ledger.SaveInboxValue(marker, value.Mapping(value.Field("schema", value.Text("cairn.inbox-recall-initialized/1")), value.Field("session", state.Get("session")))); err != nil {
			return err
		}
	} else if err != nil {
		return err
	}
	return ledger.SaveInboxValue(path, state)
}

func nonnegativeInteger(v *value.Value) bool {
	if v == nil || v.Kind != value.Int {
		return false
	}
	n, ok := new(big.Int).SetString(v.Integer, 10)
	return ok && n.Sign() >= 0
}

// claudeRequiredError's read-only mode consults the existing durable snapshot,
// including pending status when activation dependencies are unavailable.
// persist is used only while the caller holds the memory session lock.
func claudeRequiredError(config, event *value.Value, original error, persist bool) error {
	if !boundClaude(config) || !nativeUUID(event.Get("session_id")) || !(value.SameText(event.Get("hook_event_name"), "SessionStart") || value.SameText(event.Get("hook_event_name"), "UserPromptSubmit")) {
		return original
	}
	var h *hookError
	proven := isRequired(original) || errors.As(original, &h) && (h.code == "OPEN_CONFLICT" || h.code == "POLICY_UNENFORCEABLE")
	refused := func() error {
		if proven {
			return required("current required Claude context could not be restored")
		}
		return original
	}
	path, err := claudeLedgerPath(config, event)
	if err != nil {
		return refused()
	}
	state := value.Mapping(value.Field("schema", value.Text("cairn.inbox-recall-grants/1")), value.Field("session", event.Get("session_id")), value.Field("grants", value.Mapping()))
	raw, err := os.ReadFile(path)
	if err == nil {
		state, err = value.DecodeText(raw, "utf-8", 0)
	}
	if err != nil && !errors.Is(err, os.ErrNotExist) {
		return refused()
	}
	if !value.SameText(state.Get("schema"), "cairn.inbox-recall-grants/1") || !value.SameText(state.Get("session"), text(event.Get("session_id"))) || state.Get("grants") == nil || state.Get("grants").Kind != value.Object {
		return original
	}
	task, taskErr := claudeTaskKey(config)
	var grant *value.Value
	if taskErr == nil {
		grant = claudeRequiredGrant(state, event, task)
	}
	if grant == nil && value.Truth(state.Get("active")) {
		grant = state.Get("grants").Get(text(state.Get("active")))
	}
	if taskErr != nil && grant == nil {
		grant = state
	}
	known := grant.Get("required")
	count, pending, hash := integer(0), value.Boolean(false), value.Text("")
	if known != nil {
		if v := known.Get("count"); v != nil {
			count = v
		}
		if v := known.Get("pending"); v != nil {
			pending = v
		}
		if v := known.Get("selection_sha256"); v != nil {
			hash = v
		}
	}
	if !nonnegativeInteger(count) || pending.Kind != value.Bool {
		return original
	}
	proven = proven || value.Truth(count) || value.Truth(pending)
	if !proven || !persist {
		return refused()
	}
	if grant == nil {
		owner := claudePromptOwner(event)
		var identity *value.Value
		if owner != "" {
			identity = value.Text(owner)
		} else {
			identity = &value.Value{Kind: value.Null}
		}
		key, keyErr := claudeGrantKey(value.Mapping(value.Field("claude_prompt", identity)))
		if keyErr != nil {
			return refused()
		}
		grant = value.Mapping(value.Field("identity", value.Mapping(value.Field("native_owner", identity), value.Field("boundary", value.Text("ordinary_prompt")))), value.Field("limit_bytes", config.Get("context_bytes")), value.Field("output_bytes", integer(0)), value.Field("native_allowance_bytes", integer(0)))
		if grant.Get("limit_bytes") == nil {
			grant.Set("limit_bytes", integer(12000))
		}
		state.Get("grants").Set(key, grant)
		state.Set("active", value.Text(key))
	}
	grant.Set("required", value.Mapping(value.Field("count", count), value.Field("selection_sha256", hash), value.Field("pending", value.Boolean(true))))
	// A failed status save cannot turn a known required refusal into optional
	// success. The original invocation remains refused and never refunds bytes.
	_ = saveClaudeLedger(path, state)
	return refused()
}

func claudeGrantKey(identity *value.Value) (string, error) {
	raw, err := encoded(identity, false)
	return hashText(string(raw)), err
}

// The first classification is read-only. Source's public boundary may then
// preserve a proven failure under the same nonblocking session lock. A busy
// writer cannot be claimed durable, and no optional allowance is renewed.
func claudeBoundaryError(config, event *value.Value, err error) error {
	err = claudeRequiredError(config, event, err, false)
	if !isRequired(err) {
		return err
	}
	directory, pathErr := pathValue(config.Get("state_dir"))
	if pathErr != nil {
		return err
	}
	_ = statefile.LegacyLock(context.Background(), filepath.Join(directory, text(event.Get("session_id"))+".lock"), 0, func() error {
		err = claudeRequiredError(config, event, err, true)
		return nil
	})
	return err
}
