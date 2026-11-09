package value

import (
	"bytes"
	"encoding/json"
	"errors"
	"io"
)

// ProfileUnavailable is a code-selection failure, never a Source ValueError.
// A definition describes a reference; it does not attest runtime equality.
type ProfileUnavailable struct{}

func (*ProfileUnavailable) Error() string { return "compatibility_profile" }

type Codec struct {
	Name   string `json:"name"`
	Errors string `json:"errors"`
}

type ReaderRoute struct {
	Input  string `json:"input"`
	Codec  *Codec `json:"codec"`
	Domain string `json:"domain"`
}

type WriterRoute struct {
	Route       string `json:"route"`
	Codec       *Codec `json:"codec"`
	EnsureASCII *bool  `json:"ensure_ascii"`
	Compact     *bool  `json:"compact"`
	TrailingLF  *bool  `json:"trailing_lf"`
	Domain      string `json:"domain"`
}

type Provenance struct {
	RecordID string `json:"record_id"`
	Version  int    `json:"version"`
	SHA256   string `json:"sha256"`
}

type DomainSelection struct {
	Class      string       `json:"class"`
	Route      string       `json:"route"`
	Provenance []Provenance `json:"provenance"`
}

// SourceProfileDefinition is explicitly versioned receipt metadata. Nil digit
// settings are unqualified; zero is Python's explicit unlimited conversion.
// There is deliberately no universal depth limit or Source-finalized flag.
type SourceProfileDefinition struct {
	Schema             string                     `json:"schema"`
	State              string                     `json:"state"`
	ReadIntegerDigits  *int                       `json:"read_integer_digits"`
	WriteIntegerDigits *int                       `json:"write_integer_digits"`
	Readers            map[string]ReaderRoute     `json:"readers"`
	Filesystem         *Codec                     `json:"filesystem"`
	Writers            map[string]WriterRoute     `json:"writers"`
	Domains            map[string]DomainSelection `json:"domains"`
}

// SourceRoutes enforces the actual per-site domain and failure policy around
// the lossless mechanics. Separate decode and streaming-write routes preserve
// Source recursion/error priority and partial-write behavior. A boolean witness,
// receipt hash or a recursionlimit integer cannot implement this contract.
type SourceRoutes interface {
	CheckDefinition(SourceProfileDefinition, []string) error
	// Decode must honor the supplied policy-bound kernels (or an equivalent
	// proved route), not replace their object hook with an already collapsed
	// legacy value. The actual site still owns error/stack/domain priority.
	Decode(site string, raw []byte, route ReaderRoute, integerDigits int, bytesKernel func([]byte, int) (*Value, error), textKernel func([]byte, string, int) (*Value, error)) (*Value, error)
	Write(site string, out io.Writer, v *Value, route WriterRoute, integerDigits int, kernel func(io.Writer, *Value, WriterRoute, int) error) error
	Filesystem() FilesystemRoutes
}

type FilesystemRoutes interface {
	Resolve(*Value) ([]rune, error)
	IsDir([]rune) (bool, error)
	Exists([]rune) (bool, error)
	Encode([]rune) (string, error)
	Decode(string) ([]rune, error)
	Home() ([]rune, error)
}

type SourceReaderProfile struct {
	Definition SourceProfileDefinition
	Routes     SourceRoutes
}

func (p *SourceReaderProfile) Require(sites ...string) error {
	if p == nil || p.Definition.Schema != "cairn.source-reader-profile/1" ||
		p.Definition.State != "definition" || p.Definition.ReadIntegerDigits == nil ||
		p.Definition.WriteIntegerDigits == nil || p.Definition.Filesystem == nil || p.Routes == nil {
		return &ProfileUnavailable{}
	}
	for _, digits := range []*int{p.Definition.ReadIntegerDigits, p.Definition.WriteIntegerDigits} {
		if *digits != 0 && *digits < 640 {
			return &ProfileUnavailable{}
		}
	}
	return p.Routes.CheckDefinition(p.Definition, sites)
}

func (p *SourceReaderProfile) Decode(site string, raw []byte) (*Value, error) {
	return p.DecodeWithObjectPolicy(site, raw, DuplicateLast)
}

// DecodeWithObjectPolicy keeps the operation's duplicate behavior behind the
// same mandatory profile/domain route. It changes no receipt shape or Source
// limit. The supplied kernels preserve pairs until the rejection hook runs;
// qualification failures must not be converted into malformed-record skips.
func (p *SourceReaderProfile) DecodeWithObjectPolicy(site string, raw []byte, policy ObjectPolicy) (*Value, error) {
	if err := p.Require(site); err != nil {
		return nil, err
	}
	if !validObjectPolicy(policy) {
		return nil, &ProfileUnavailable{}
	}
	route, ok := p.Definition.Readers[site]
	if !ok || route.Domain == "" || (route.Input != "bytes-json" && route.Input != "text-json") ||
		(route.Input == "bytes-json" && route.Codec != nil) || (route.Input == "text-json" && route.Codec == nil) {
		return nil, &ProfileUnavailable{}
	}
	bytesKernel := func(raw []byte, digits int) (*Value, error) { return DecodeBytesWithObjectPolicy(raw, digits, policy) }
	textKernel := func(raw []byte, encoding string, digits int) (*Value, error) {
		return DecodeTextWithObjectPolicy(raw, encoding, digits, policy)
	}
	return p.Routes.Decode(site, raw, route, *p.Definition.ReadIntegerDigits, bytesKernel, textKernel)
}

func (p *SourceReaderProfile) Write(site string, out io.Writer, v *Value) error {
	if err := p.Require(site); err != nil {
		return err
	}
	route, ok := p.Definition.Writers[site]
	if !ok || route.Domain == "" || route.EnsureASCII == nil || route.Compact == nil || route.TrailingLF == nil ||
		(route.Route != "dumps" && route.Route != "dump") || route.Codec == nil || route.Codec.Name == "" || route.Codec.Errors == "" {
		return &ProfileUnavailable{}
	}
	return p.Routes.Write(site, out, v, route, *p.Definition.WriteIntegerDigits, WriteKernel)
}

// ProfileFromReceipt constructs no runtime witness or default policy. Production
// routes remain uninstantiated until the exact profile/domain is independently
// selected and its enforcing adapter is supplied with the real consumer.
func ProfileFromReceipt(raw json.RawMessage) (*SourceReaderProfile, error) {
	p := &SourceReaderProfile{}
	if len(raw) == 0 || string(raw) == "null" {
		return p, nil
	}
	var fields map[string]json.RawMessage
	malformed := func() (*SourceReaderProfile, error) { return nil, errors.New("malformed source profile descriptor") }
	if err := json.Unmarshal(raw, &fields); err != nil || len(fields) != 8 {
		return malformed()
	}
	for _, name := range []string{"schema", "state", "read_integer_digits", "write_integer_digits", "readers", "filesystem", "writers", "domains"} {
		if _, ok := fields[name]; !ok {
			return malformed()
		}
	}
	shape := func(raw json.RawMessage, names ...string) bool {
		if string(raw) == "null" {
			return true
		}
		var object map[string]json.RawMessage
		if json.Unmarshal(raw, &object) != nil || object == nil || len(object) != len(names) {
			return false
		}
		for _, key := range names {
			if _, ok := object[key]; !ok {
				return false
			}
		}
		return true
	}
	text := func(raw json.RawMessage) bool {
		var v any
		if json.Unmarshal(raw, &v) != nil {
			return false
		}
		_, ok := v.(string)
		return ok
	}
	codec := func(raw json.RawMessage) bool {
		if string(raw) == "null" {
			return true
		}
		if !shape(raw, "name", "errors") {
			return false
		}
		var object map[string]json.RawMessage
		_ = json.Unmarshal(raw, &object)
		return text(object["name"]) && text(object["errors"])
	}
	if !text(fields["schema"]) || !text(fields["state"]) || !codec(fields["filesystem"]) {
		return malformed()
	}
	for _, group := range []string{"readers", "writers", "domains"} {
		var entries map[string]json.RawMessage
		if json.Unmarshal(fields[group], &entries) != nil {
			return malformed()
		}
		for _, entry := range entries {
			var object map[string]json.RawMessage
			if json.Unmarshal(entry, &object) != nil || object == nil {
				return malformed()
			}
			switch group {
			case "readers":
				if !shape(entry, "input", "codec", "domain") || !codec(object["codec"]) || !text(object["input"]) || !text(object["domain"]) {
					return malformed()
				}
			case "writers":
				if !shape(entry, "route", "codec", "ensure_ascii", "compact", "trailing_lf", "domain") || !codec(object["codec"]) || !text(object["route"]) || !text(object["domain"]) {
					return malformed()
				}
			case "domains":
				if !shape(entry, "class", "route", "provenance") || !text(object["class"]) || !text(object["route"]) {
					return malformed()
				}
				var refs []json.RawMessage
				if json.Unmarshal(object["provenance"], &refs) != nil {
					return malformed()
				}
				for _, ref := range refs {
					if string(ref) == "null" || !shape(ref, "record_id", "version", "sha256") {
						return malformed()
					}
					var r map[string]json.RawMessage
					_ = json.Unmarshal(ref, &r)
					if !text(r["record_id"]) || !text(r["sha256"]) || string(r["version"]) == "null" {
						return malformed()
					}
				}
			}
		}
	}
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&p.Definition); err != nil {
		return malformed()
	}
	if p.Definition.Schema != "cairn.source-reader-profile/1" {
		return malformed()
	}
	if _, err := decoder.Token(); !errors.Is(err, io.EOF) {
		return malformed()
	}
	return p, nil
}
