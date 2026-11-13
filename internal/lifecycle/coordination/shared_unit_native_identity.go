package coordination

import (
	"errors"
	"fmt"
	"strings"
	"unicode/utf8"

	"github.com/halbritt/cairn/internal/lifecycle/value"
)

// Identity observed in state is correspondence data, not authentication.
// Current hook state records registration and store-returned agent identities;
// retained state may also have the legacy outer identity. None can override
// another present identity.
func watchStateNativeSession(state *value.Value) (string, error) {
	if state == nil || state.Kind != value.Object || !value.SameText(state.Get("schema"), NativeSessionSchema) {
		return "", errors.New("native session identity schema is unknown")
	}
	identities := []*value.Value{state.Get("native_session_id")}
	for _, field := range []string{"registration", "agent"} {
		container := state.Get(field)
		if container == nil {
			continue
		}
		if container.Kind != value.Object {
			return "", fmt.Errorf("native session identity %s is not an object", field)
		}
		identities = append(identities, container.Get("native_session_id"))
	}
	var native string
	for _, identity := range identities {
		if identity == nil {
			continue
		}
		if identity.Kind != value.String {
			return "", errors.New("native session identity is not a string")
		}
		for _, r := range identity.String {
			if !utf8.ValidRune(r) {
				return "", errors.New("native session identity has an invalid code point")
			}
		}
		observed := string(identity.String)
		// Match the existing registration domain without trimming or rewriting it.
		if strings.TrimSpace(observed) == "" || len(observed) > 256 || strings.ContainsRune(observed, 0) {
			return "", errors.New("native session identity is malformed")
		}
		if native != "" && observed != native {
			return "", errors.New("native session identities conflict")
		}
		native = observed
	}
	if native == "" {
		return "", errors.New("native session identity is missing")
	}
	return native, nil
}
