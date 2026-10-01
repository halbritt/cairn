package core

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"strings"
	"unicode/utf8"
)

// SemanticQueryProjection describes lossy discovery input, never lexical intent.
// Token counts include the worker's fixed instruction prefix and special tokens;
// byte length and digest address only a prefix of the original query text.
type SemanticQueryProjection struct {
	Method         string `json:"method"`
	Truncated      bool   `json:"truncated"`
	OriginalTokens int    `json:"original_tokens"`
	EmbeddedTokens int    `json:"embedded_tokens"`
	PrefixBytes    int    `json:"prefix_bytes"`
	PrefixSHA256   string `json:"prefix_sha256"`
}

// A missing Boolean is not a claim that the full query was embedded.
func (p *SemanticQueryProjection) UnmarshalJSON(raw []byte) error {
	var fields map[string]json.RawMessage
	if err := json.Unmarshal(raw, &fields); err != nil {
		return err
	}
	if len(fields) != 6 {
		return fmt.Errorf("invalid query projection fields")
	}
	for _, key := range []string{"method", "truncated", "original_tokens", "embedded_tokens", "prefix_bytes", "prefix_sha256"} {
		if value, ok := fields[key]; !ok || bytes.Equal(bytes.TrimSpace(value), []byte("null")) {
			return fmt.Errorf("missing query projection field")
		}
	}
	type plain SemanticQueryProjection
	var value plain
	d := json.NewDecoder(bytes.NewReader(raw))
	d.DisallowUnknownFields()
	if err := d.Decode(&value); err != nil {
		return err
	}
	*p = SemanticQueryProjection(value)
	return nil
}

// ValidateSemanticQueryProjection binds reported bytes to the original intent.
// The trusted worker owns tokenization; this check cannot certify its token count.
func ValidateSemanticQueryProjection(p *SemanticQueryProjection, query string) error {
	if p == nil {
		return nil
	} // legacy worker: projection unreported
	if p.Method != "original-prefix/1" || len(query) > 4096 || !utf8.ValidString(query) || p.PrefixBytes <= 0 || p.PrefixBytes > len(query) || p.OriginalTokens <= 0 || p.OriginalTokens > 8192 || p.EmbeddedTokens <= 0 || p.EmbeddedTokens > 512 || p.EmbeddedTokens > p.OriginalTokens {
		return fmt.Errorf("invalid query projection bounds")
	}
	prefix := query[:p.PrefixBytes]
	if !utf8.ValidString(prefix) || strings.TrimSpace(prefix) == "" {
		return fmt.Errorf("invalid query projection prefix")
	}
	sum := sha256.Sum256([]byte(prefix))
	if p.PrefixSHA256 != hex.EncodeToString(sum[:]) {
		return fmt.Errorf("query projection digest mismatch")
	}
	if p.Truncated {
		if p.PrefixBytes == len(query) || p.OriginalTokens <= 512 {
			return fmt.Errorf("invalid truncated query projection")
		}
	} else if p.PrefixBytes != len(query) || p.OriginalTokens != p.EmbeddedTokens {
		return fmt.Errorf("invalid full query projection")
	}
	return nil
}
