package memoryhook

import (
	"bytes"
	"context"
	"encoding/base64"
	"math/big"
	"strconv"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
	"github.com/halbritt/cairn/internal/lifecycle/memoryclient"
	"github.com/halbritt/cairn/internal/lifecycle/value"
)

func bounded(v *value.Value, low, high int) (int, bool) {
	if v == nil || v.Kind != value.Int {
		return 0, false
	}
	n, err := strconv.Atoi(v.Integer)
	return n, err == nil && n >= low && n <= high
}
func sameInteger(a, b *value.Value) bool {
	return a != nil && b != nil && a.Kind == value.Int && b.Kind == value.Int && a.Integer == b.Integer
}
func atLeast(v *value.Value, n int) bool {
	if v == nil || v.Kind != value.Int {
		return false
	}
	x, ok := new(big.Int).SetString(v.Integer, 10)
	return ok && x.Cmp(big.NewInt(int64(n))) >= 0
}
func optionalHint(entry *value.Value) (*core.ByteSpanRequest, error) {
	hint := entry.Get("match_span")
	match := hint != nil
	if !match {
		hint = entry.Get("summary_span")
	}
	offset, offsetOK := bounded(hint.Get("offset"), 0, 65535)
	length, lengthOK := bounded(hint.Get("length"), 1, 4096)
	if !offsetOK || !lengthOK || value.Truth(entry.Get("conflicts")) || value.SameText(entry.Get("class"), "C") {
		return nil, fail("no bounded optional match span")
	}
	if !match {
		length = min(1536, 65536-offset)
	}
	return &core.ByteSpanRequest{Offset: offset, Length: length}, nil
}
func checkedSpan(entry, response *value.Value, hint *core.ByteSpanRequest) error {
	selection := response.Get("selection")
	record := selection.Get("record")
	span := response.Get("span")
	body := span.Get("body")

	offset, offsetOK := bounded(span.Get("offset"), 0, 65535)
	end, endOK := bounded(span.Get("end"), 0, 69631)
	if selection == nil || selection.Kind != value.Object || record == nil || record.Kind != value.Object || span == nil || span.Kind != value.Object {
		return fail("optional span response is incomplete")
	}
	if !value.SameText(record.Get("record_id"), text(entry.Get("record_id"))) || !sameInteger(record.Get("version"), entry.Get("version")) || (!value.SameText(record.Get("class"), "A") && !value.SameText(record.Get("class"), "B")) || !value.SameText(record.Get("body"), "") || value.Truth(response.Get("competing")) || value.Truth(selection.Get("conflicts")) || value.Truth(selection.Get("mandatory")) || body == nil || body.Kind != value.String || len(body.String) == 0 || !offsetOK || offset != hint.Offset || span.Get("end") == nil || span.Get("end").Kind != value.Int {
		return fail("optional span identity or bytes changed")
	}
	if !validText(body) {
		return &value.Error{Class: "UnicodeEncodeError", Message: "optional span has invalid Unicode"}
	}
	if !endOK || end != offset+len(text(body)) || end > offset+hint.Length || !atLeast(span.Get("total_bytes"), end) || !value.SameText(span.Get("sha256"), hashText(text(body))) || !bodyHash.MatchString(text(span.Get("source_sha256"))) || (value.Truth(entry.Get("body_sha256")) && !value.SameText(span.Get("source_sha256"), text(entry.Get("body_sha256")))) {
		return fail("optional span identity or bytes changed")
	}

	response.Set("source_extent", value.Text("partial_span"))
	if opening := response.Get("source_opening"); opening != nil {
		return checkedPairedOpening(opening, span)
	}
	return nil
}
func openingBytes(opening *value.Value, exclusive bool) ([]byte, error) {
	body, encoded := opening.Get("body"), opening.Get("body_base64")
	if exclusive && ((body == nil) == (encoded == nil)) {
		return nil, fail("paired source opening boundary changed")
	}
	if validText(body) && (exclusive || !value.Truth(encoded)) {
		return []byte(text(body)), nil
	}
	if validText(encoded) && (exclusive || !value.Truth(body)) {
		if strings.ContainsAny(text(encoded), "\r\n") {
			return nil, fail("source opening bytes invalid")
		}
		source, err := base64.StdEncoding.DecodeString(text(encoded))
		if err == nil {
			return source, nil
		}
	}
	return nil, fail("source opening bytes invalid")
}
func checkedPairedOpening(opening, match *value.Value) error {
	if opening == nil || opening.Kind != value.Object {
		return fail("paired source opening boundary changed")
	}
	for _, m := range opening.Members {
		switch string(m.Key) {
		case "offset", "end", "total_bytes", "sha256", "body", "body_base64":
		default:
			return fail("paired source opening boundary changed")
		}
	}
	offset, ok := bounded(opening.Get("offset"), 0, 0)
	end, endOK := bounded(opening.Get("end"), 1, min(768, number(match.Get("offset"))))
	if !ok || offset != 0 || !endOK || !sameInteger(opening.Get("total_bytes"), match.Get("total_bytes")) {
		return fail("paired source opening boundary changed")
	}
	source, err := openingBytes(opening, true)
	if err != nil {
		return err
	}
	if len(source) != end || !value.SameText(opening.Get("sha256"), hashText(string(source))) {
		return fail("paired source opening bytes changed")
	}
	return nil
}
func excerptWhole(entry, pulled *value.Value, hint *core.ByteSpanRequest) (*value.Value, error) {
	response := clone(pulled)
	selection := response.Get("selection")
	record := selection.Get("record")
	source := []byte(text(record.Get("body")))
	start, end := min(hint.Offset, len(source)), min(hint.Offset+hint.Length, len(source))
	part := source[start:end]
	if !utf8.Valid(part) {
		return nil, fail("optional excerpt cuts a UTF-8 character")
	}
	record.Set("body", value.Text(""))
	response.Set("span", value.Mapping(value.Field("offset", integer(hint.Offset)), value.Field("end", integer(hint.Offset+len(part))), value.Field("total_bytes", integer(len(source))), value.Field("body", value.Text(string(part))), value.Field("sha256", value.Text(hashText(string(part)))), value.Field("source_sha256", value.Text(hashText(string(source))))))
	response.Set("span_origin", value.Text("whole_pull"))
	if err := checkedSpan(entry, response, hint); err != nil {
		return nil, err
	}
	return response, nil
}
func spanCall(ctx context.Context, client *memoryclient.Client, entry *value.Value, hint *core.ByteSpanRequest) (*value.Value, error) {
	args := entry.Get("pull_arguments")
	request := core.ExpandRequest{RequestID: uuid.NewString(), ReceiptID: text(args.Get("receipt_id")), Handle: text(args.Get("handle")), Span: hint}
	raw, err := client.Pull(ctx, request, 2*time.Second)
	if err != nil {
		return nil, err
	}
	return value.DecodeText(raw, "utf-8", 0)
}
func openingExcerpt(entry *value.Value, source []byte, match *value.Value, origin string) (*value.Value, error) {
	for pos := 0; pos < len(source); {
		r, n := utf8.DecodeRune(source[pos:])
		if r == utf8.RuneError && n == 1 {
			if !utf8.FullRune(source[pos:]) {
				source = source[:pos]
				break
			}
			return nil, fail("source opening contains invalid UTF-8")
		}
		pos += n
	}
	if newline := bytes.LastIndexByte(source, '\n'); newline >= 0 {
		source = source[:newline+1]
	}
	if len(source) == 0 {
		return nil, fail("source opening has no complete UTF-8 character")
	}
	return value.Mapping(value.Field("status", value.Text("provided")), value.Field("origin", value.Text(origin)), value.Field("record_id", entry.Get("record_id")), value.Field("version", entry.Get("version")), value.Field("span", value.Mapping(value.Field("offset", integer(0)), value.Field("end", integer(len(source))), value.Field("total_bytes", match.Get("total_bytes")), value.Field("body", value.Text(string(source))), value.Field("sha256", value.Text(hashText(string(source)))), value.Field("source_sha256", match.Get("source_sha256"))))), nil
}
func pullOpening(ctx context.Context, client *memoryclient.Client, entry, match *value.Value, length int, spend func()) (*value.Value, error) {
	if ctx.Err() != nil {
		return nil, fail("retrieval time budget exhausted")
	}
	hint := &core.ByteSpanRequest{Offset: 0, Length: length}
	spend()
	response, err := spanCall(ctx, client, entry, hint)
	if err != nil {
		return nil, err
	}
	span := response.Get("span")
	if span == nil || span.Kind != value.Object {
		return nil, fail("source opening response is incomplete")
	}
	source, err := openingBytes(span, false)
	if err != nil {
		return nil, err
	}
	if !sameInteger(span.Get("offset"), integer(0)) || !sameInteger(span.Get("end"), integer(length)) || len(source) != length || !value.SameText(span.Get("sha256"), hashText(string(source))) || !sameInteger(span.Get("total_bytes"), match.Get("total_bytes")) || !value.SameText(span.Get("source_sha256"), text(match.Get("source_sha256"))) {
		return nil, fail("source opening identity or bytes changed")
	}
	context, err := openingExcerpt(entry, source, match, "span_pull")
	if err != nil {
		return nil, err
	}
	response.Set("span", context.Get("span"))
	if err := checkedSpan(entry, response, hint); err != nil {
		return nil, err
	}
	return context, nil
}

type inspectionStopped struct{}

func (*inspectionStopped) Error() string { return "optional inspection stopped" }

func recoverSpan(ctx context.Context, client *memoryclient.Client, entry, whole, inspection *value.Value, spend func(), render func(*value.Value) (string, error)) (*value.Value, error) {
	hint, err := optionalHint(entry)
	if err != nil {
		return nil, err
	}
	if ctx.Err() != nil {
		inspection.Get("refusals").Set("deadline", integer(1))
		return nil, &inspectionStopped{}
	}
	var response *value.Value
	if whole == nil {
		if number(inspection.Get("pull_calls")) >= 2 {
			inspection.Get("refusals").Set("pull_limit", integer(1))
			return nil, &inspectionStopped{}
		}
		spend()
		response, err = spanCall(ctx, client, entry, hint)
		if err == nil {
			err = checkedSpan(entry, response, hint)
		}
	} else {
		response, err = excerptWhole(entry, whole, hint)
	}
	if err != nil {
		return nil, err
	}
	candidate := value.Mapping(value.Field("pull_arguments", entry.Get("pull_arguments")), value.Field("response", response))
	if _, err := render(candidate); err != nil {
		return candidate, err
	}
	if response.Get("source_opening") != nil {
		return candidate, nil
	}
	span := response.Get("span")
	length := min(768, number(span.Get("offset")))
	var opening *value.Value
	switch {
	case length == 0:
		opening = value.Mapping(value.Field("status", value.Text("passage_starts_at_opening")))
	case whole != nil:
		source := []byte(text(whole.Get("selection").Get("record").Get("body")))
		opening, err = openingExcerpt(entry, source[:min(length, len(source))], span, "whole_pull")
		if err != nil {
			return candidate, err
		}
	case number(inspection.Get("pull_calls")) >= 2:
		opening = value.Mapping(value.Field("status", value.Text("unavailable")), value.Field("reason", value.Text("pull_limit")))
	case ctx.Err() != nil:
		opening = value.Mapping(value.Field("status", value.Text("unavailable")), value.Field("reason", value.Text("deadline")))
	default:
		opening, err = pullOpening(ctx, client, entry, span, length, spend)
		if err != nil {
			opening = value.Mapping(value.Field("status", value.Text("unavailable")), value.Field("reason", value.Text("pull_unavailable")))
		}
	}
	paired := clone(candidate)
	paired.Set("source_opening_excerpt", opening)
	if _, err = render(paired); err == nil {
		return paired, nil
	} else if !contextError(err) {
		return candidate, err
	}
	paired.Set("source_opening_excerpt", value.Mapping(value.Field("status", value.Text("unavailable")), value.Field("reason", value.Text("context_budget"))))
	if _, err = render(paired); err == nil {
		return paired, nil
	} else if !contextError(err) {
		return candidate, err
	}
	return candidate, nil
}

func fitExcerpt(ctx context.Context, entry, candidate *value.Value, render func(*value.Value) (string, error)) (*value.Value, error) {
	checked := func(item *value.Value) error {
		if ctx.Err() != nil {
			return &contextRefused{}
		}
		_, err := render(item)
		if ctx.Err() != nil {
			return &contextRefused{}
		}
		return err
	}
	if err := checked(candidate); err == nil {
		return candidate, nil
	} else if !contextError(err) {
		return nil, err
	}
	span := candidate.Get("response").Get("span")
	preview := entry.Get("summary_span")
	start, startOK := bounded(preview.Get("offset"), number(span.Get("offset")), number(span.Get("end")))
	length, lengthOK := bounded(preview.Get("length"), 1, 65536)
	if !startOK || !lengthOK || start+length > number(span.Get("end")) {
		return nil, &contextRefused{}
	}
	source := []byte(text(span.Get("body")))
	minimum := max(start+length-number(span.Get("offset")), length+1)
	cuts := []int{}
	for end := minimum; end < len(source); end++ {
		if source[end]&0xc0 != 0x80 {
			cuts = append(cuts, end)
		}
	}
	var best *value.Value
	low, high := 0, len(cuts)-1
	for low <= high {
		middle := (low + high) / 2
		part := source[:cuts[middle]]
		smaller := clone(candidate)
		response := smaller.Get("response")
		excerpt := response.Get("span")
		excerpt.Set("body", value.Text(string(part)))
		excerpt.Set("end", integer(number(span.Get("offset"))+len(part)))
		excerpt.Set("sha256", value.Text(hashText(string(part))))
		if response.Get("span_origin") == nil {
			response.Set("span_origin", value.Text("span_pull"))
		}
		err := checked(smaller)
		if contextError(err) {
			if ctx.Err() != nil {
				return nil, err
			}
			high = middle - 1
		} else if err != nil {
			return nil, err
		} else {
			best = smaller
			low = middle + 1
		}
	}
	if best == nil || ctx.Err() != nil {
		return nil, &contextRefused{}
	}
	return best, nil
}
