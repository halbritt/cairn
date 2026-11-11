package ledger

import (
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"os"
	"path/filepath"
	"strings"

	"github.com/halbritt/cairn/internal/lifecycle/admission"
	"github.com/halbritt/cairn/internal/lifecycle/value"
)

// InboxRefusal carries the Source guard site independently of its error family.
// The coordination caller owns settlement and the native-prompt refusal.
type InboxRefusal struct {
	Message, Stage, Reason string
	Details                *value.Value
}

func (e *InboxRefusal) Error() string { return e.Message }

func RefuseInbox(message, stage, reason string) *InboxRefusal {
	return &InboxRefusal{Message: message, Stage: stage, Reason: reason}
}

// InboxGrants reads an atomic snapshot. Its caller holds the memory session lock;
// initialization and grant publication remain separate, ordered operations.
func InboxGrants(path, session string) (*value.Value, error) {
	marker := strings.TrimSuffix(path, ".json") + ".initialized"
	initialized, err := exists(marker)
	if err != nil {
		return nil, err
	}
	if initialized {
		v, err := read(marker)
		if err != nil {
			return nil, err
		}
		if !markerMatches(v, value.Mapping(value.Field("schema", value.Text(initSchema)), value.Field("session", value.Text(session)))) {
			return nil, RefuseInbox("inbox recall initialization marker corrupt", "inbox_ledger", "marker")
		}
	}
	present, err := exists(path)
	if err != nil {
		return nil, err
	}
	if initialized && !present {
		return nil, RefuseInbox("inbox recall grant ledger missing", "inbox_ledger", "missing")
	}
	state := value.Mapping(value.Field("schema", value.Text(inboxSchema)), value.Field("session", value.Text(session)), value.Field("grants", value.Mapping()))
	if present {
		state, err = read(path)
		if err != nil {
			return nil, err
		}
	}
	if err := validateInbox(state, session); err != nil {
		message := "inbox recall grant ledger corrupt"
		var source *value.Error
		if errors.As(err, &source) {
			message = source.Message
		}
		reason := map[string]string{
			"inbox required-context status corrupt": "required_status", "inbox required refresh counter corrupt": "refresh_counter",
			"inbox recall pending charge corrupt": "pending_charge", "inbox recall active grant missing": "active_grant",
			"inbox recall folded grants corrupt": "folded",
		}[message]
		if reason == "" {
			reason = "shape"
		}
		return nil, RefuseInbox(message, "inbox_ledger", reason)
	}
	return state, nil
}

// ResolvedInboxActivation only reads the decision made by the parent activation.
// It never creates a directory, marker, decision or native-turn grant.
func ResolvedInboxActivation(memory *value.Value, harness, session, turn string) (bool, error) {
	dir := memory.Get("state_dir")
	if dir == nil || dir.Kind != value.String {
		return false, invalid("activation ledger configuration lacks a state directory")
	}
	// The checked profile supplies an absolute filesystem path; the reader keeps
	// Source's ordinary path policy rather than adding a second file policy.
	directory, err := admission.PathBytes(dir.String)
	if err != nil {
		return false, err
	}
	if !filepath.IsAbs(directory) {
		return false, invalid("activation ledger directory must be absolute")
	}
	path := filepath.Join(directory, session+".memory-budget.json")
	var state *value.Value
	if harness == "claude" {
		path = filepath.Join(directory, session+".inbox-recall.json")
		state, err = InboxGrants(path, session)
	} else {
		state, err = read(path)
		if err == nil && (!value.SameText(state.Get("schema"), codexSchema) || !value.SameText(state.Get("session_id"), session)) {
			err = invalid("activation ledger identity differs")
		}
	}
	if err != nil {
		return false, err
	}
	decisions := state.Get("inbox_activation")
	if decisions == nil || decisions.Kind != value.Object {
		return false, invalid("already-resolved inbox activation required")
	}
	for _, decision := range decisions.Members {
		if decision.Value == nil || decision.Value.Kind != value.Bool {
			return false, invalid("invalid activation decisions")
		}
	}
	decision := decisions.Get(turn)
	if decision == nil || decision.Kind != value.Bool {
		return false, invalid("already-resolved inbox activation required")
	}
	return decision.Boolean, nil
}

func InboxFoldedToken(kind, text string) string {
	sum := sha256.Sum256([]byte("cairn.inbox-recall-fold/1\x00" + kind + "\x00" + text))
	return hex.EncodeToString(sum[:])[:16]
}

func InboxFoldedConflict(state *value.Value, key, wake string, owner *value.Value) bool {
	tokens := state.Get("folded").Get("tokens")
	if tokens == nil || tokens.Kind != value.Array {
		return false
	}
	probes := []string{InboxFoldedToken("key", key), InboxFoldedToken("wake", wake)}
	if owner != nil && owner.Kind == value.String {
		probes = append(probes, InboxFoldedToken("owner", string(owner.String)))
	}
	for _, token := range tokens.Items {
		for _, probe := range probes {
			if value.SameText(token, probe) {
				return true
			}
		}
	}
	return false
}

// SaveInboxValue is the retained durable atomic write without an implicit marker.
// The consumer supplies the Source-specific marker/ledger ordering explicitly.
func SaveInboxValue(path string, state *value.Value) error { return saveCodex(path, state) }

// WithdrawPristineInbox preserves Source's anti-reset window before first grant.
func WithdrawPristineInbox(path string) error {
	if err := os.Remove(path); err != nil {
		return err
	}
	directory, err := os.Open(filepath.Dir(path))
	if err != nil {
		return err
	}
	defer directory.Close()
	return directory.Sync()
}
