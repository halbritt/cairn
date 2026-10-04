package localapi

import (
	"strings"
	"unicode"
	"unicode/utf8"

	"github.com/halbritt/cairn/core"
)

// SourceOpeningBindingEnv names the authenticated profile principals whose new
// context-index receipts bind the source-opening policy: exact principals,
// comma separated, with no surrounding whitespace. Unset or empty enables none.
// It is read once when
// `cairn serve` starts, so a change needs a restart, and it deliberately is not
// an identities.json member: an older binary ignores an unknown environment
// variable but refuses to start on an unknown identity field.
const SourceOpeningBindingEnv = "CAIRN_SOURCE_OPENING_BINDING"

// Option configures a Server beyond its identities. The zero configuration is
// the safe one: no principal binds the source-opening policy.
type Option func(*serverOptions)

type serverOptions struct {
	sourceOpening map[string]bool
}

// WithSourceOpeningBinding enables receipt binding for exactly these configured
// profile principals. A principal that is not a configured identity refuses to
// start the server rather than being ignored.
func WithSourceOpeningBinding(principals []string) Option {
	return func(o *serverOptions) {
		o.sourceOpening = map[string]bool{}
		for _, principal := range principals {
			o.sourceOpening[principal] = true
		}
	}
}

func invalidSourceOpeningBinding() error {
	return &core.Error{Code: "INVALID_REQUEST", Message: "invalid source-opening binding configuration"}
}

// ParseSourceOpeningBinding reads SourceOpeningBindingEnv. Anything other than a
// list of distinct, exact principals without control characters or surrounding
// whitespace is an error, never a wider or partial set.
func ParseSourceOpeningBinding(value string) ([]string, error) {
	if value == "" {
		return nil, nil
	}
	entries := strings.Split(value, ",")
	if len(entries) > maxAPIIdentities {
		return nil, invalidSourceOpeningBinding()
	}
	seen := map[string]bool{}
	for _, entry := range entries {
		if entry == "" || len(entry) > 256 || !utf8.ValidString(entry) || strings.TrimSpace(entry) != entry || seen[entry] {
			return nil, invalidSourceOpeningBinding()
		}
		for _, r := range entry {
			if unicode.IsControl(r) {
				return nil, invalidSourceOpeningBinding()
			}
		}
		seen[entry] = true
	}
	return entries, nil
}

func (o serverOptions) validate(identities []Identity) error {
	configured := map[string]bool{}
	for _, identity := range identities {
		configured[identity.Principal] = true
	}
	for principal := range o.sourceOpening {
		if !configured[principal] {
			return invalidSourceOpeningBinding()
		}
	}
	return nil
}
