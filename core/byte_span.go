package core

import (
	"crypto/sha256"
	"encoding/base64"
	"encoding/hex"
	"unicode/utf8"
)

// ByteSpanRequest selects at most Length bytes starting at Offset. Only EOF
// clips the range; UTF-8 boundaries do not change the requested byte offsets.
type ByteSpanRequest struct {
	Offset int `json:"offset"`
	Length int `json:"length"`
}

type ByteSpan struct {
	Offset     int    `json:"offset"`
	End        int    `json:"end"` // Exclusive; the next offset when below TotalBytes.
	TotalBytes int    `json:"total_bytes"`
	SHA256     string `json:"sha256"`
	Body       string `json:"body,omitempty"`
	BodyBase64 string `json:"body_base64,omitempty"`
}

// The caller validates range bounds and source eligibility before selecting bytes.
func selectByteSpan(body []byte, req ByteSpanRequest) ByteSpan {
	end := min(req.Offset+req.Length, len(body))
	selected := body[req.Offset:end]
	digest := sha256.Sum256(selected)
	span := ByteSpan{Offset: req.Offset, End: end, TotalBytes: len(body), SHA256: hex.EncodeToString(digest[:])}
	if utf8.Valid(selected) {
		span.Body = string(selected)
	} else {
		span.BodyBase64 = base64.StdEncoding.EncodeToString(selected)
	}
	return span
}
