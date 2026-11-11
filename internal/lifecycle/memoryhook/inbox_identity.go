package memoryhook

import (
	"crypto/sha256"
	"encoding/hex"
	"path/filepath"
	"regexp"
	"strings"
	"unicode/utf8"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/internal/lifecycle/admission"
	"github.com/halbritt/cairn/internal/lifecycle/ledger"
	"github.com/halbritt/cairn/internal/lifecycle/value"
)

type inboxIdentity struct {
	value, publication, attempt *value.Value
	key, wake, session, native  string
	admitted                    bool
}

var inboxWakeIDs = regexp.MustCompile(`Current agent ([0-9a-f-]+), execution ([0-9a-f-]+)\. Wake delivery ([0-9a-f-]+)\.`)

func inboxUUID(v *value.Value) bool {
	if !validText(v) {
		return false
	}
	id, err := uuid.Parse(text(v))
	return err == nil && id.String() == text(v)
}

func inboxHash(raw []byte) string {
	sum := sha256.Sum256(raw)
	return hex.EncodeToString(sum[:])
}

func inboxEqual(a, b *value.Value) bool {
	if a == nil || b == nil {
		return a == b
	}
	if a.Kind != b.Kind {
		return false
	}
	switch a.Kind {
	case value.Null:
		return true
	case value.Bool:
		return a.Boolean == b.Boolean
	case value.Int:
		return a.Integer == b.Integer
	case value.Float:
		return a.Number == b.Number
	case value.String:
		return string(a.String) == string(b.String)
	case value.Array:
		if len(a.Items) != len(b.Items) {
			return false
		}
		for i := range a.Items {
			if !inboxEqual(a.Items[i], b.Items[i]) {
				return false
			}
		}
		return true
	case value.Object:
		if len(a.Members) != len(b.Members) {
			return false
		}
		for _, member := range a.Members {
			if !inboxEqual(member.Value, b.Get(string(member.Key))) {
				return false
			}
		}
		return true
	}
	return false
}

func inboxPrivate(event, state *value.Value, directory string) (bool, error) {
	project := event.Get("project_path")
	if project != nil && project.Kind != value.Null {
		path, err := pathValue(project)
		if err != nil || !filepath.IsAbs(path) {
			return false, ledger.RefuseInbox("invalid explicit host project directory", "identity", "project")
		}
		probe := admission.OSProbe{Directory: directory}
		if yes, err := probe.IsDir(project.String); err != nil || !yes {
			return false, ledger.RefuseInbox("invalid explicit host project directory", "identity", "project")
		}
	}
	root, err := projectRoot(event, directory)
	if err != nil {
		return false, err
	}
	for _, workspace := range []*value.Value{event.Get("cwd"), value.Text(root), state.Get("workspace"), state.Get("agent").Get("metadata").Get("workspace")} {
		if !value.Truth(workspace) {
			continue
		}
		points, err := (admission.OSProbe{Directory: directory}).Resolve(workspace)
		if err != nil {
			return false, err
		}
		path, err := admission.PathBytes(points)
		if err != nil {
			return false, err
		}
		for {
			found, err := (admission.OSProbe{Directory: directory}).Exists(admission.PathPoints(filepath.Join(path, ".cairn-no-memory")))
			if err != nil {
				return false, err
			}
			if found {
				return true, nil
			}
			parent := filepath.Dir(path)
			if parent == path {
				break
			}
			path = parent
		}
	}
	return false, nil
}

func inboxClaim(config, event, observation, state *value.Value) (inboxIdentity, error) {
	refuse := func(message, reason string) (inboxIdentity, error) {
		return inboxIdentity{}, ledger.RefuseInbox(message, "identity", reason)
	}
	if !inboxUUID(event.Get("session_id")) {
		return refuse("inbox recall requires canonical native session identity", "native_session")
	}
	session, native := text(event.Get("session_id")), text(observation.Get("native_turn_id"))
	claude := value.SameText(config.Get("harness"), "claude")
	if !claude && !inboxUUID(observation.Get("native_turn_id")) {
		return refuse("inbox recall requires actual Codex native turn UUID", "native_turn")
	}
	if claude {
		prompt := event.Get("prompt_id")
		if !validText(prompt) || strip(text(prompt)) == "" || !utf8.ValidString(text(prompt)) || len(text(prompt)) > 256 || strings.IndexFunc(text(prompt), func(r rune) bool { return r < 32 }) >= 0 {
			return refuse("inbox recall requires actual Claude prompt ownership", "claude_owner")
		}
		if native != "claude-channel:"+text(prompt) {
			return refuse("inbox recall Claude prompt owner differs", "claude_owner")
		}
		if !strings.HasPrefix(text(event.Get("prompt")), `<channel source="cairn-events" native_session_id="`+session+`" `) {
			return refuse("inbox recall requires this session channel envelope", "claude_envelope")
		}
	}
	wake := inboxWakeIDs.FindStringSubmatch(text(event.Get("prompt")))
	agent := state.Get("agent")
	if wake == nil || wake[1] != text(agent.Get("agent_id")) || wake[2] != text(agent.Get("execution_id")) {
		return refuse("reserved wake belongs to another registered execution", "wake_session")
	}
	attempt := state.Get("inbox_attempt")
	admitted := value.Truth(attempt) && !value.Truth(attempt.Get("finished_at"))
	ref := value.Mapping(value.Field("agent_id", agent.Get("agent_id")), value.Field("execution_id", agent.Get("execution_id")))
	wakeHash := inboxHash([]byte(text(event.Get("prompt"))))
	identity := value.Mapping(value.Field("native_owner", value.Text(native)), value.Field("wake_sha256", value.Text(wakeHash)), value.Field("session", ref), value.Field("boundary", value.Text("unadmitted_wake")))
	var publication *value.Value
	keyIdentity := identity
	if admitted {
		if value.Truth(attempt.Get("lease_lapsed")) || value.Truth(attempt.Get("cancel")) {
			return refuse("inbox recall attempt is not live", "attempt_live")
		}
		if !inboxEqual(attempt.Get("session"), ref) {
			return refuse("inbox recall attempt session differs", "attempt_session")
		}
		if !value.Truth(attempt.Get("native_turn_id")) || !value.SameText(attempt.Get("native_turn_id"), native) {
			return refuse("inbox recall native owner differs", "attempt_owner")
		}
		delivery := attempt.Get("delivery")
		if !value.SameText(delivery.Get("delivery_id"), wake[3]) {
			return refuse("reserved wake belongs to another delivery", "wake_delivery")
		}
		publication = delivery.Get("event")
		boundary := "native_turn"
		if claude {
			boundary = "claude_prompt_delivery"
		}
		identity = value.Mapping(value.Field("attempt_id", attempt.Get("attempt_id")), value.Field("delivery_id", delivery.Get("delivery_id")), value.Field("event_id", publication.Get("event_id")), value.Field("session", attempt.Get("session")), value.Field("source", publication.Get("ref")), value.Field("native_owner", value.Text(native)), value.Field("boundary", value.Text(boundary)))
		keyIdentity = value.Mapping(value.Field("session", identity.Get("session")), value.Field("attempt_id", identity.Get("attempt_id")), value.Field("delivery_id", identity.Get("delivery_id")))
	}
	raw, err := encoded(keyIdentity, false)
	if err != nil {
		return inboxIdentity{}, err
	}
	return inboxIdentity{value: identity, publication: publication, attempt: attempt, key: inboxHash(raw), wake: wakeHash, session: session, native: native, admitted: admitted}, nil
}
