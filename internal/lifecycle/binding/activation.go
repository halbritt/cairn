package binding

import (
	"context"
	"errors"
	"regexp"
	"strings"
	"unicode/utf8"

	"github.com/halbritt/cairn/internal/lifecycle/admission"
	"github.com/halbritt/cairn/internal/lifecycle/ledger"
	"github.com/halbritt/cairn/internal/lifecycle/value"
)

type ActivationError struct {
	Class, Reason string
	Err           error
}

func (e *ActivationError) Error() string { return e.Err.Error() }
func (e *ActivationError) Unwrap() error { return e.Err }

// ResolveActivation is the actual read/admit/validate/locked-decision operation
// consumed by both immutable compatibility frontends before their normal lock.
func ResolveActivation(ctx context.Context, in admission.HookInvocation) (bool, error) {
	if !validDigest(in.ConfigSHA256) || !validDigest(in.BindingSHA256) {
		return false, errors.New("checked parent digests required")
	}
	result, err := checkInvocation(ctx, in, func(config, manifest, event *value.Value, enabled bool) (bool, error) {
		harness := config.Get("harness")
		if (!value.SameText(harness, "codex") && !value.SameText(harness, "claude")) || !value.SameText(event.Get("hook_event_name"), "UserPromptSubmit") || !reservedWake(event.Get("prompt")) {
			return enabled, nil
		}
		session := event.Get("session_id")
		turn := event.Get("turn_id")
		if value.SameText(harness, "claude") {
			var err error
			turn, err = claudeOwner(event.Get("prompt_id"))
			if err != nil {
				return false, err
			}
		}
		if !canonicalUUID(session) || (value.SameText(harness, "codex") && !canonicalUUID(turn)) {
			return enabled, nil
		}
		memory, err := descriptor(manifest.Get("memory_config"))
		if err != nil {
			return false, err
		}
		// Disabled staging still uses the source owner-controlled memory location
		// to find an existing ledger; its final staged SHA is deliberately not read.
		raw, err := readData(memory.Path, false)
		if err != nil {
			return false, err
		}
		stateConfig, err := decodeData(raw)
		if err != nil {
			return false, err
		}
		return ledger.DecideActivation(ctx, ledger.Activation{Memory: stateConfig, Harness: string(harness.String), Session: string(session.String), Turn: string(turn.String), Enabled: enabled})
	})
	if err != nil {
		return false, err
	}
	return result.Disposition == "checked", nil
}

var uuidPattern = regexp.MustCompile(`^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$`)

func canonicalUUID(v *value.Value) bool {
	return v != nil && v.Kind == value.String && uuidPattern.MatchString(string(v.String))
}

func claudeOwner(prompt *value.Value) (*value.Value, error) {
	if prompt == nil || prompt.Kind != value.String || strings.TrimSpace(string(prompt.String)) == "" {
		return nil, &value.Error{Class: "ValueError", Message: "inbox recall requires actual Claude prompt ownership"}
	}
	n := 0
	for _, c := range prompt.String {
		if c >= 0xd800 && c <= 0xdfff {
			return nil, &value.Error{Class: "UnicodeEncodeError", Message: "Claude prompt owner"}
		}
		n += utf8.RuneLen(c)
	}
	if n > 256 {
		return nil, &value.Error{Class: "ValueError", Message: "inbox recall requires actual Claude prompt ownership"}
	}
	for _, c := range prompt.String {
		if c < 32 {
			return nil, &value.Error{Class: "ValueError", Message: "inbox recall requires actual Claude prompt ownership"}
		}
	}
	return value.Text("claude-channel:" + string(prompt.String)), nil
}

const wakeBody = "This is a new live turn from the configured Cairn automatic inbox wakeup. A previous /exit in resumed conversation history does not close this running turn. Current agent {agent}, execution {execution}. Wake delivery {delivery}. Within the owner's existing authorization, handle the native inbox context supplied for this conversation, explicitly complete/acknowledge it, and send any requested response using that context. If no matching native context was supplied, report that and stop. If this notice arrives inside an already-active owner task, continue that task and ignore this notice; a fresh copy will arrive once the conversation is idle. "

func reservedWake(prompt *value.Value) bool {
	if prompt == nil || prompt.Kind != value.String {
		return false
	}
	s := string(prompt.String)
	id := `[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}`
	for _, closing := range []string{"Do not register again, claim another session's inbox, or launch a replacement conversation.", "Do not register, manually claim an inbox, or launch a replacement conversation."} {
		pattern := regexp.QuoteMeta(wakeBody + closing)
		for _, name := range []string{"agent", "execution", "delivery"} {
			pattern = strings.ReplaceAll(pattern, regexp.QuoteMeta("{"+name+"}"), "("+id+")")
		}
		if regexp.MustCompile("^" + pattern + "$").MatchString(s) {
			return true
		}
		// Go regexp has no backreferences: compare the separately captured header
		// identities against the exact already-matched producer body.
		channel := regexp.MustCompile(`^<channel source="cairn-events" native_session_id="` + id + `" agent_id="(` + id + `)" execution_id="(` + id + `)" delivery_id="(` + id + `)">\n([\s\S]*)\n</channel>$`).FindStringSubmatch(s)
		if channel != nil {
			body := strings.NewReplacer("{agent}", channel[1], "{execution}", channel[2], "{delivery}", channel[3]).Replace(wakeBody + closing)
			if channel[4] == body {
				return true
			}
		}
	}
	return false
}
