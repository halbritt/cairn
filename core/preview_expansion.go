package core

import (
	"encoding/json"
	"strings"
	"unicode"
	"unicode/utf8"
)

const previewBoundariesV1 = "cairn.preview-boundaries/1"
const previewCompactV1 = "cairn.preview-compact/1"

func validatePresentation(p SemanticPackage) error {
	if p.Presentation != "" && ((p.Presentation != previewBoundariesV1 && p.Presentation != previewCompactV1) || p.Mode != "index" || p.Purpose != "context") {
		return failure("INTEGRITY_FAILURE", "historical preview presentation contract is invalid")
	}
	for _, entry := range p.Index {
		if entry.EntitiesOmitted != 0 && (p.Presentation != previewCompactV1 || entry.EntitiesOmitted < 1 || entry.EntitiesOmitted > 16 || len(entry.Entities) != 0 || (entry.Class != "A" && entry.Class != "B") || len(entry.Conflicts) != 0) {
			return failure("INTEGRITY_FAILURE", "historical compact preview metadata is invalid")
		}
	}
	return nil
}

// Admission is finished before this pass. Spend only remaining room, never
// remove another position or shorten previously visible source to fund context.
func expandPreviewBoundaries(p SemanticPackage, candidates []candidate, evaluations map[string]*CandidateEvaluation) (SemanticPackage, error) {
	if p.Presentation == "" {
		return p, nil
	}
	if err := validatePresentation(p); err != nil {
		return p, err
	}
	bodies := map[string]string{}
	for _, c := range candidates {
		for _, member := range unitMembers(c) {
			bodies[member.selection.Record.RecordID] = member.selection.Record.Body
		}
	}
	optionalCost := 0
	for _, entry := range p.Index {
		encoded, err := json.Marshal(entry)
		if err != nil {
			return p, err
		}
		optionalCost += len(encoded) + 160
	}
	for i, entry := range p.Index {
		if entry.SummarySpan == nil || entry.Class == "C" || len(entry.Conflicts) != 0 || entry.EntitiesOmitted != 0 {
			continue
		}
		text, span, ok := enclosingPreview(bodies[entry.RecordID], *entry.SummarySpan)
		if !ok {
			continue
		}
		updated := entry
		updated.Summary, updated.SummarySpan = text, &span
		before, err := json.Marshal(entry)
		if err != nil {
			return p, err
		}
		after, err := json.Marshal(updated)
		if err != nil {
			return p, err
		}
		delta := len(after) - len(before)
		if optionalCost+delta > p.OptionalLimit {
			continue
		}
		p.Index[i] = updated
		cost, err := indexMemoryCost(p)
		if err != nil {
			return p, err
		}
		if cost > indexMemoryRoom(p) {
			p.Index[i] = entry
			continue
		}
		optionalCost += delta
		evaluations[entry.RecordID].Cost += delta
	}
	return p, nil
}

// Recognize simple prose boundaries, not semantic scope. Internal punctuation
// in paths, decimals and config keys is not a terminator. Ambiguous long spans,
// quoted/code text and unsupported punctuation keep the original excerpt.
func enclosingPreview(body string, old ByteSpanRequest) (string, ByteSpanRequest, bool) {
	end := old.Offset + old.Length
	if old.Offset < 0 || old.Length <= 0 || end > len(body) || end < old.Offset {
		return "", old, false
	}
	// Any containing span fitting the display must lie in this small region.
	left, right := max(0, end-160), min(len(body), old.Offset+160)
	for right < len(body) && !utf8.RuneStart(body[right]) {
		right--
	}
	for left < old.Offset && !utf8.RuneStart(body[left]) {
		left++
	}
	start, finish := -1, -1
	if left == 0 {
		start = 0
	}
	for offset, r := range body[left:right] {
		at := left + offset + utf8.RuneLen(r)
		terminal := r == '。' || r == '！' || r == '？'
		if r == '.' || r == '!' || r == '?' {
			if at == len(body) {
				terminal = true
			} else {
				next, _ := utf8.DecodeRuneInString(body[at:])
				terminal = unicode.IsSpace(next)
			}
		}
		if !terminal {
			continue
		}
		if at <= old.Offset {
			next := at
			for next < old.Offset {
				r, size := utf8.DecodeRuneInString(body[next:old.Offset])
				if !unicode.IsSpace(r) {
					break
				}
				next += size
			}
			start = next
		}
		if at >= end && finish < 0 {
			finish = at
		}
	}
	if finish < 0 && right == len(body) {
		finish = len(body)
	}
	if start < 0 || finish < 0 || (start == old.Offset && finish == end) {
		return "", old, false
	}
	text := body[start:finish]
	if !utf8.ValidString(text) || strings.ContainsAny(text, "`\"") {
		return "", old, false
	}
	if start > 0 {
		text = "..." + text
	}
	if finish < len(body) {
		text += "..."
	}
	if len(text) > 160 {
		return "", old, false
	}
	return text, ByteSpanRequest{Offset: start, Length: finish - start}, true
}
