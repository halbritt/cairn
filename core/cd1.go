package core

import (
	"fmt"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"time"
)

const (
	CD1Proto     = "cd1"
	CairnC1Proto = "cairn-c/1"
)

var (
	cd1ValidKindsD = map[string]bool{
		"req": true, "resp": true, "notice": true, "handoff": true, "update": true,
	}
	cd1ValidKindsC = map[string]bool{
		"request": true, "req": true, "response": true, "resp": true,
		"notice": true, "handoff": true, "update": true,
	}
	cd1KindCanonical = map[string]string{
		"req": "request", "request": "request",
		"resp": "response", "response": "response",
		"notice": "notice", "handoff": "handoff", "update": "update",
	}
	cd1CanonicalToD = map[string]string{
		"request": "req", "response": "resp",
		"notice": "notice", "handoff": "handoff", "update": "update",
	}
	cd1ValidStatusesD = map[string]bool{
		"pending": true, "active": true, "attempted": true, "partial": true,
		"done": true, "verified": true, "blocked": true, "failed": true, "inconclusive": true,
	}
	cd1ValidStatusesC = map[string]bool{
		"pending": true, "in_progress": true, "attempted": true, "partial": true,
		"complete": true, "verified": true, "blocked": true, "failed": true, "inconclusive": true,
	}
	cd1ValidHdrKeysD = map[string]bool{
		"re": true, "from": true, "to": true, "act": true, "ttl": true,
	}
	cd1ValidActions = map[string]bool{
		"none": true, "review": true, "decide": true, "work": true,
		"construct": true, "info": true, "note": true, "expand": true,
	}
	// CD1ActToEnvelopeAction maps cd1's 8 act values to Cairn's 3 envelope action values.
	CD1ActToEnvelopeAction = map[string]string{
		"none":      "none",
		"info":      "none",
		"note":      "none",
		"review":    "review",
		"decide":    "decide",
		"work":      "decide",
		"construct": "decide",
		"expand":    "decide",
	}
	cd1EnvelopeActions     = map[string]bool{"none": true, "review": true, "decide": true}
	cd1ActNoneForbiddenOps = map[string]bool{
		"req": true, "if": true, "stop": true, "dec": true,
		"blk": true, "q": true, "ho": true, "expand": true,
	}
	cd1DOpcodes = map[string]bool{
		"ref": true, "auth": true, "base": true, "rw": true, "ro": true, "no": true,
		"req": true, "if": true, "dep": true, "stop": true,
		"obs": true, "hyp": true, "rec": true, "dec": true, "dis": true, "claim": true,
		"pat": true, "chk": true, "blk": true, "fail": true, "stat": true, "ho": true, "q": true,
		"expand": true, "meta": true,
	}
	cd1CFields = map[string]bool{
		"proto": true, "kind": true, "proj": true, "task": true, "status": true,
		"re": true, "from": true, "to": true, "auth": true,
		"ref": true, "base": true, "rw": true, "ro": true, "deny": true,
		"act": true, "req": true, "if": true, "dep": true, "stop": true,
		"obs": true, "hyp": true, "rec": true, "dec": true, "dis": true, "claim": true,
		"pat": true, "chk": true, "blk": true, "fail": true, "stat": true,
		"next": true, "q": true, "expand": true, "meta": true,
	}
	cd1NonHandleLiterals = map[string]bool{
		"absent": true, "unknown": true, "empty": true, "false": true,
		"partial": true, "failed": true, "none": true, "ok": true, "true": true,
		"clean": true, "dirty": true, "main": true, "master": true, "head": true, "origin": true,
	}
	cd1Macros = []struct {
		name string
		exp  string
	}{
		{"adv", "advisory only (not adoption or approval)"},
		{"self", "scoped to stated target only; no hold on other projects"},
		{"clean", "git working tree clean with no stray worktree/branch"},
		{"noreplay", "failed delivery was not replayed or re-claimed"},
		{"untested", "static/source inspection only; no runtime tests executed"},
	}
)

var (
	cd1HandleRe   = regexp.MustCompile(`^[a-zA-Z][a-zA-Z0-9_]{1,15}$`)
	cd1UUIDRe     = regexp.MustCompile(`^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$`)
	cd1SHA1Re     = regexp.MustCompile(`^[0-9a-fA-F]{40}$`)
	cd1SHA256Re   = regexp.MustCompile(`^[0-9a-fA-F]{64}$`)
	cd1ShortHexRe = regexp.MustCompile(`^[0-9a-fA-F]{6,39}$`)
	cd1HexPrefix  = regexp.MustCompile(`^[0-9a-fA-F]{6,8}-`)
	cd1ChkTriplet = regexp.MustCompile(`\b(\d+|unknown)/(\d+|unknown)/(\d+|unknown)\b`)
	cd1CLineRe    = regexp.MustCompile(`^([a-z]+):\s+(.+)$`)
)

// CD1ParentContext supplies thread-scoped handle bindings for L1 deltas.
type CD1ParentContext struct {
	ThreadID        string            `json:"thread_id"`
	Refs            map[string]string `json:"refs"`
	SourceVersion   int               `json:"source_version"`
	ExpiresAt       string            `json:"expires_at,omitempty"`
	ConsumedThreads map[string]string `json:"consumed_threads,omitempty"`
}

// CD1ValidateOptions configures structural validation and envelope cross-checking.
type CD1ValidateOptions struct {
	ParentContext  *CD1ParentContext
	ThreadID       string
	EnvelopeKind   string
	EnvelopeAction string
	Now            *time.Time
}

type CD1ParsedItem struct {
	Opcode  string `json:"opcode"`
	Payload string `json:"payload"`
	LineNo  int    `json:"line_no"`
}

type CD1ParsedMessage struct {
	Proto      string            `json:"proto"`
	Kind       string            `json:"kind"`
	Proj       string            `json:"proj"`
	Task       string            `json:"task"`
	Status     string            `json:"status"`
	HeaderMeta map[string]string `json:"header_meta"`
	Refs       map[string]string `json:"refs"`
	Items      []CD1ParsedItem   `json:"items"`
	RawText    string            `json:"raw_text"`
}

type CD1ValidationResult struct {
	Valid    bool              `json:"valid"`
	Proto    string            `json:"proto,omitempty"`
	Errors   []string          `json:"errors"`
	Warnings []string          `json:"warnings"`
	Parsed   *CD1ParsedMessage `json:"parsed,omitempty"`
}

// DetectCompactProtocol inspects line 1 to identify cd1 / cairn-c/1 / versioned headers.
// Ordinary prose returns ("", false).
func DetectCompactProtocol(text string) (string, bool) {
	for _, raw := range strings.Split(text, "\n") {
		ln := strings.TrimSpace(raw)
		if ln == "" {
			continue
		}
		if strings.HasPrefix(ln, "proto:") {
			val := strings.TrimSpace(strings.TrimPrefix(ln, "proto:"))
			return val, true
		}
		fields := strings.Fields(ln)
		if len(fields) > 0 {
			tok := fields[0]
			if strings.HasPrefix(tok, "cd") && len(tok) <= 6 {
				if tok == CD1Proto || (len(tok) >= 3 && tok[2] >= '0' && tok[2] <= '9') {
					return tok, true
				}
			}
		}
		return "", false
	}
	return "", false
}

func cd1WordChar(b byte) bool {
	return (b >= 'a' && b <= 'z') || (b >= 'A' && b <= 'Z') || (b >= '0' && b <= '9') || b == '_'
}

func cd1HexChar(b byte) bool {
	return (b >= '0' && b <= '9') || (b >= 'a' && b <= 'f') || (b >= 'A' && b <= 'F')
}

func cd1HandleTrailerForbidden(b byte) bool {
	return cd1WordChar(b) || b == '-' || b == '.' || b == '/' || b == ':' || b == '@'
}

func cd1IsCanonicalOrNonTruncatedRef(val string) (bool, string) {
	if strings.Contains(val, "...") {
		return false, fmt.Sprintf("ellipsis-truncated reference target %q", val)
	}
	core := val
	for _, prefix := range []string{"agent/", "record/", "event/", "delivery/", "execution/", "evidence/", "source/", "rev/"} {
		if strings.HasPrefix(core, prefix) {
			core = strings.TrimPrefix(core, prefix)
			break
		}
	}
	if strings.Contains(core, "@") && !strings.HasPrefix(core, "http://") && !strings.HasPrefix(core, "https://") {
		left, right, _ := strings.Cut(core, "@")
		if _, err := strconv.Atoi(right); err == nil {
			core = left
		} else if strings.HasPrefix(right, "v") {
			if _, err := strconv.Atoi(right[1:]); err == nil {
				core = left
			}
		} else if cd1ShortHexRe.MatchString(right) && !cd1SHA1Re.MatchString(right) && !cd1SHA256Re.MatchString(right) {
			return false, fmt.Sprintf("truncated commit/digest %q in %q", right, val)
		}
	}
	if idx := strings.LastIndex(core, "/v"); idx >= 0 && !strings.HasPrefix(core, "http://") && !strings.HasPrefix(core, "https://") {
		if _, err := strconv.Atoi(core[idx+2:]); err == nil {
			core = core[:idx]
		}
	}
	if cd1UUIDRe.MatchString(core) || cd1SHA1Re.MatchString(core) || cd1SHA256Re.MatchString(core) {
		return true, ""
	}
	if cd1ShortHexRe.MatchString(core) {
		return false, fmt.Sprintf("truncated UUID/SHA %q; use full 36-char UUID, 40-char git SHA, or 64-char SHA-256", val)
	}
	if cd1HexPrefix.MatchString(core) && !cd1UUIDRe.MatchString(core) {
		return false, fmt.Sprintf("incomplete UUID %q", val)
	}
	return true, ""
}

func cd1ParseRefsStr(payload string, refs map[string]string, errors *[]string, lineNo int) {
	parts := strings.Split(payload, ";")
	nonEmpty := 0
	for _, raw := range parts {
		part := strings.TrimSpace(raw)
		if part == "" {
			continue
		}
		nonEmpty++
		eq := strings.IndexByte(part, '=')
		if eq < 0 {
			*errors = append(*errors, fmt.Sprintf("line %d: malformed ref binding %q (expected handle=canonical_value)", lineNo, part))
			continue
		}
		k := strings.TrimSpace(part[:eq])
		v := strings.TrimSpace(part[eq+1:])
		if !cd1HandleRe.MatchString(k) {
			*errors = append(*errors, fmt.Sprintf("line %d: invalid ref handle name %q (must be 2-16 chars starting with ASCII letter)", lineNo, k))
			continue
		}
		if v == "" {
			*errors = append(*errors, fmt.Sprintf("line %d: empty target for ref handle %q", lineNo, k))
			continue
		}
		if ok, reason := cd1IsCanonicalOrNonTruncatedRef(v); !ok {
			*errors = append(*errors, fmt.Sprintf("line %d: TRUNCATED_REF_TARGET for %q: %s", lineNo, k, reason))
			continue
		}
		if prev, exists := refs[k]; exists && prev != v {
			*errors = append(*errors, fmt.Sprintf("line %d: conflicting redefinition of handle %q", lineNo, k))
			continue
		}
		refs[k] = v
	}
	if nonEmpty == 0 {
		*errors = append(*errors, fmt.Sprintf("line %d: empty ref declaration", lineNo))
	}
}

func cd1FindTruncatedUUIDs(txt string) []string {
	var out []string
	// Keyword forms: (re|from|to|ev|evidence|source|event|delivery|record|execution|agent)[:=]HEX7_12
	// or space-separated (event|delivery|record|execution|agent|evidence)\s+HEX7_12
	// or @[(delivery|event|record|execution|agent|evidence|source):]HEX7_12
	kwRe := regexp.MustCompile(`(?i)\b(?:re|from|to|ev|evidence|source|event|delivery|record|execution|agent)[:=]([0-9a-f]{7,12})|\b(?:event|delivery|record|execution|agent|evidence)\s+([0-9a-f]{7,12})`)
	for _, m := range kwRe.FindAllStringSubmatchIndex(txt, -1) {
		valStart, valEnd := m[2], m[3]
		if valStart < 0 {
			valStart, valEnd = m[4], m[5]
		}
		if valEnd < len(txt) && (cd1HexChar(txt[valEnd]) || txt[valEnd] == '-' || cd1WordChar(txt[valEnd])) {
			continue
		}
		out = append(out, txt[m[0]:m[1]])
	}
	atRe := regexp.MustCompile(`(?i)@(?:(?:delivery|event|record|execution|agent|evidence|source):)?([0-9a-f]{7,12})`)
	for _, m := range atRe.FindAllStringSubmatchIndex(txt, -1) {
		start := m[0]
		if start > 0 {
			prev := txt[start-1]
			if cd1WordChar(prev) || prev == '.' || prev == '/' || prev == '-' {
				continue
			}
		}
		valEnd := m[3]
		if valEnd < len(txt) && (cd1HexChar(txt[valEnd]) || txt[valEnd] == '-' || cd1WordChar(txt[valEnd])) {
			continue
		}
		out = append(out, txt[m[0]:m[1]])
	}
	return out
}

type cd1HandleMatch struct {
	handle string
	desc   string
}

func cd1FindHandleUses(txt string) []cd1HandleMatch {
	var out []cd1HandleMatch
	keyedRe := regexp.MustCompile(`\b(?:re|from|to|ev|evidence)=([a-zA-Z][a-zA-Z0-9_]{1,15})`)
	for _, m := range keyedRe.FindAllStringSubmatchIndex(txt, -1) {
		end := m[3]
		if end < len(txt) && cd1HandleTrailerForbidden(txt[end]) {
			continue
		}
		h := txt[m[2]:m[3]]
		if !cd1NonHandleLiterals[strings.ToLower(h)] {
			out = append(out, cd1HandleMatch{handle: h, desc: "used in header/field"})
		}
	}
	typedAtRe := regexp.MustCompile(`@(?:delivery|event|record|execution|agent|evidence|source):([a-zA-Z][a-zA-Z0-9_]{1,15})`)
	for _, m := range typedAtRe.FindAllStringSubmatchIndex(txt, -1) {
		end := m[3]
		if end < len(txt) && cd1HandleTrailerForbidden(txt[end]) {
			continue
		}
		out = append(out, cd1HandleMatch{handle: txt[m[2]:m[3]], desc: fmt.Sprintf("in %q", txt[m[0]:m[1]])})
	}
	bareAtRe := regexp.MustCompile(`@([a-zA-Z][a-zA-Z0-9_]{1,15})`)
	for _, m := range bareAtRe.FindAllStringSubmatchIndex(txt, -1) {
		if m[0] > 0 {
			prev := txt[m[0]-1]
			if cd1WordChar(prev) || prev == '.' || prev == '/' || prev == '-' {
				continue
			}
		}
		end := m[3]
		if end < len(txt) && cd1HandleTrailerForbidden(txt[end]) {
			continue
		}
		out = append(out, cd1HandleMatch{handle: txt[m[2]:m[3]], desc: fmt.Sprintf("in %q", txt[m[0]:m[1]])})
	}
	kwSpaceRe := regexp.MustCompile(`\b(?:delivery|event|record|execution)\s+([a-zA-Z][a-zA-Z0-9_]{1,15})`)
	for _, m := range kwSpaceRe.FindAllStringSubmatchIndex(txt, -1) {
		end := m[3]
		if end < len(txt) && cd1HandleTrailerForbidden(txt[end]) {
			continue
		}
		h := txt[m[2]:m[3]]
		if !cd1NonHandleLiterals[strings.ToLower(h)] {
			out = append(out, cd1HandleMatch{handle: h, desc: fmt.Sprintf("in %q", txt[m[0]:m[1]])})
		}
	}
	return out
}

func cd1HasUndefinedOrderOp(txt string) bool {
	for i := 1; i+1 < len(txt); i++ {
		if txt[i] != '<' {
			continue
		}
		prev := txt[i-1]
		next := txt[i+1]
		prevOK := cd1WordChar(prev) || prev == ')'
		nextOK := (next >= 'a' && next <= 'z') || (next >= 'A' && next <= 'Z') || next == '_'
		if prevOK && nextOK {
			return true
		}
	}
	return false
}

func cd1ValidateParentContext(ctx *CD1ParentContext, threadID string, now *time.Time, errors *[]string) bool {
	if ctx == nil {
		return true
	}
	if strings.TrimSpace(ctx.ThreadID) == "" {
		*errors = append(*errors, "INVALID_PARENT_CONTEXT: 'thread_id' must be a non-empty string")
		return false
	}
	if ctx.SourceVersion < 1 {
		*errors = append(*errors, "INVALID_PARENT_CONTEXT: 'source_version' must be a positive integer")
		return false
	}
	if strings.TrimSpace(threadID) != "" && strings.TrimSpace(threadID) != strings.TrimSpace(ctx.ThreadID) {
		*errors = append(*errors, fmt.Sprintf("CROSS_THREAD_HANDLE_REUSE: message thread %q != parent_context thread %q", strings.TrimSpace(threadID), strings.TrimSpace(ctx.ThreadID)))
		return false
	}
	if ctx.ExpiresAt != "" {
		exp, err := time.Parse(time.RFC3339Nano, ctx.ExpiresAt)
		if err != nil {
			*errors = append(*errors, fmt.Sprintf("INVALID_PARENT_CONTEXT: invalid expires_at %q: %v", ctx.ExpiresAt, err))
			return false
		}
		refNow := time.Now().UTC()
		if now != nil {
			refNow = now.UTC()
		}
		if !refNow.Before(exp.UTC()) {
			*errors = append(*errors, fmt.Sprintf("EXPIRED_PARENT_CONTEXT: parent context expired at %s", ctx.ExpiresAt))
			return false
		}
	}
	for h, target := range ctx.Refs {
		if !cd1HandleRe.MatchString(h) {
			*errors = append(*errors, fmt.Sprintf("INVALID_PARENT_CONTEXT: invalid handle name %q", h))
			continue
		}
		if strings.TrimSpace(target) == "" {
			*errors = append(*errors, fmt.Sprintf("INVALID_PARENT_CONTEXT: empty target for handle %q", h))
			continue
		}
		if ok, reason := cd1IsCanonicalOrNonTruncatedRef(strings.TrimSpace(target)); !ok {
			*errors = append(*errors, fmt.Sprintf("INVALID_PARENT_CONTEXT: non-canonical ref %q: %s", h, reason))
		}
	}
	return len(*errors) == 0
}

// BuildCD1ParentContext constructs a thread-scoped parent context from sequential messages.
func BuildCD1ParentContext(messages []string, threadID string, sourceVersion int, expiresAt string, now *time.Time) (*CD1ParentContext, error) {
	if strings.TrimSpace(threadID) == "" {
		return nil, fmt.Errorf("INVALID_PARENT_CONTEXT: thread_id required")
	}
	if sourceVersion < 1 {
		sourceVersion = 1
	}
	ctx := &CD1ParentContext{
		ThreadID:      strings.TrimSpace(threadID),
		Refs:          map[string]string{},
		SourceVersion: sourceVersion,
		ExpiresAt:     expiresAt,
	}
	for idx, msg := range messages {
		res := ValidateCD1Message(msg, CD1ValidateOptions{
			ParentContext: ctx,
			ThreadID:      ctx.ThreadID,
			Now:           now,
		})
		if !res.Valid || res.Parsed == nil {
			return nil, fmt.Errorf("CONFLICTING_PARENT_CONTEXT: message[%d] invalid: %s", idx, strings.Join(res.Errors, "; "))
		}
		for h, target := range res.Parsed.Refs {
			if prev, ok := ctx.Refs[h]; ok && prev != target {
				return nil, fmt.Errorf("CONFLICTING_PARENT_CONTEXT: handle %q redefined (%q vs %q)", h, prev, target)
			}
			ctx.Refs[h] = target
		}
	}
	return ctx, nil
}

type cd1Line struct {
	no   int
	text string
}

// ValidateCD1Message performs structural validation only (not proof of truth or authority).
func ValidateCD1Message(text string, opts CD1ValidateOptions) CD1ValidationResult {
	var errors []string
	proto, _ := DetectCompactProtocol(text)
	if !cd1ValidateParentContext(opts.ParentContext, opts.ThreadID, opts.Now, &errors) {
		return CD1ValidationResult{Valid: false, Proto: proto, Errors: errors}
	}
	var nonempty []cd1Line
	for idx, raw := range strings.Split(text, "\n") {
		if strings.TrimSpace(raw) != "" {
			nonempty = append(nonempty, cd1Line{no: idx + 1, text: strings.TrimRight(raw, " \t\r")})
		}
	}
	if len(nonempty) == 0 {
		return CD1ValidationResult{Valid: false, Errors: []string{"EMPTY_MESSAGE: message has no non-empty lines"}}
	}
	if proto == CairnC1Proto {
		return cd1ValidateC(nonempty, text, opts)
	}
	if proto == CD1Proto {
		return cd1ValidateD(nonempty, text, opts)
	}
	if strings.HasPrefix(proto, "cairn-c/") || strings.HasPrefix(proto, "cd") {
		return CD1ValidationResult{
			Valid:  false,
			Proto:  proto,
			Errors: []string{fmt.Sprintf("UNSUPPORTED_VERSION: unsupported protocol version %q; emit expand", proto)},
		}
	}
	return CD1ValidationResult{
		Valid:  false,
		Proto:  proto,
		Errors: []string{fmt.Sprintf("UNKNOWN_PROTOCOL: expected 'proto: %s' or '%s ...'", CairnC1Proto, CD1Proto)},
	}
}

func cd1HasStandaloneToken(txt, tok string) bool {
	idx := 0
	for {
		pos := strings.Index(txt[idx:], tok)
		if pos < 0 {
			return false
		}
		start := idx + pos
		end := start + len(tok)
		prevOK := start == 0 || (!cd1WordChar(txt[start-1]) && txt[start-1] != '/' && txt[start-1] != '.' && txt[start-1] != '-')
		nextOK := end == len(txt) || (!cd1WordChar(txt[end]) && txt[end] != '/' && txt[end] != '.' && txt[end] != '-')
		if prevOK && nextOK {
			return true
		}
		idx = end
	}
}

func cd1ChkOutcome(payload string, isD bool) (syntaxOK, isPass, isFail bool) {
	hasUntested := cd1HasStandaloneToken(payload, "untested")
	if m := cd1ChkTriplet.FindStringSubmatch(payload); len(m) == 4 {
		pTok, fTok := m[1], m[2]
		if fTok != "unknown" {
			if fVal, err := strconv.Atoi(fTok); err == nil && fVal > 0 {
				isFail = true
			}
		}
		if !hasUntested && pTok != "unknown" && fTok == "0" {
			if pVal, err := strconv.Atoi(pTok); err == nil && pVal > 0 {
				isPass = true
			}
		}
		return true, isPass, isFail
	}
	fields := strings.Fields(payload)
	if len(fields) >= 2 {
		tok := strings.TrimRight(fields[1], ";(")
		if tok == "pass" || tok == "fail" || tok == "skip" || tok == "unknown" {
			return true, !hasUntested && tok == "pass", tok == "fail"
		}
	}
	if !isD {
		passRe := regexp.MustCompile(`\bpass=(\d+)\b`)
		failRe := regexp.MustCompile(`\bfail=(\d+)\b`)
		pm := passRe.FindStringSubmatch(payload)
		fm := failRe.FindStringSubmatch(payload)
		if len(pm) == 2 && len(fm) == 2 {
			pVal, _ := strconv.Atoi(pm[1])
			fVal, _ := strconv.Atoi(fm[1])
			return true, !hasUntested && pVal > 0 && fVal == 0, fVal > 0
		}
	}
	return false, false, false
}

func cd1SplitLeadWord(s string) string {
	s = strings.TrimSpace(s)
	for i := 0; i < len(s); i++ {
		if s[i] == ' ' || s[i] == '\t' || s[i] == ';' || s[i] == '(' {
			return s[:i]
		}
	}
	return s
}

func cd1CheckCommonInvariants(parsed *CD1ParsedMessage, opts CD1ValidateOptions, errors, warnings *[]string, isD bool) {
	kindCanon := cd1KindCanonical[parsed.Kind]
	if kindCanon == "" {
		kindCanon = parsed.Kind
	}
	var contentCount int
	isPureExpand := true
	hasAuth := false
	for _, it := range parsed.Items {
		if it.Opcode == "auth" {
			hasAuth = true
		}
		if it.Opcode == "ref" || it.Opcode == "meta" {
			continue
		}
		contentCount++
		if it.Opcode != "expand" {
			isPureExpand = false
		}
	}
	if contentCount == 0 {
		*errors = append(*errors, "MISSING_CONTENT: message contains no substantive body items")
		isPureExpand = false
	}
	hasDirective := false
	for _, it := range parsed.Items {
		if it.Opcode == "req" || it.Opcode == "dec" {
			hasDirective = true
			break
		}
	}
	if (kindCanon == "request" || hasDirective) && !isPureExpand && !hasAuth {
		*errors = append(*errors, "MISSING_AUTHORITY: requests, directives, and decisions must declare explicit 'auth'")
	}

	if opts.EnvelopeKind != "" {
		envCanon := cd1KindCanonical[opts.EnvelopeKind]
		if envCanon == "" {
			envCanon = opts.EnvelopeKind
		}
		if kindCanon != envCanon {
			*errors = append(*errors, fmt.Sprintf("ENVELOPE_KIND_MISMATCH: message kind %q disagrees with envelope kind %q", parsed.Kind, opts.EnvelopeKind))
		}
	}
	isActNone := parsed.HeaderMeta["act"] == "none"
	if opts.EnvelopeAction != "" {
		if !cd1EnvelopeActions[opts.EnvelopeAction] {
			*errors = append(*errors, fmt.Sprintf("INVALID_ENVELOPE_ACTION: envelope action %q not in [decide none review]", opts.EnvelopeAction))
		} else if act, hasAct := parsed.HeaderMeta["act"]; hasAct {
			if mapped := CD1ActToEnvelopeAction[act]; mapped != opts.EnvelopeAction {
				*errors = append(*errors, fmt.Sprintf("ENVELOPE_ACTION_MISMATCH: cd1 act=%q maps to %q, disagreeing with envelope action=%q (envelope wins)", act, mapped, opts.EnvelopeAction))
			}
		}
		isActNone = opts.EnvelopeAction == "none"
	}
	if isActNone {
		if parsed.Kind == "req" || parsed.Kind == "handoff" {
			*errors = append(*errors, fmt.Sprintf("ACT_NONE_CONFLICT: act=none / action=none cannot combine with kind %q", parsed.Kind))
		}
		if parsed.Status == "blocked" {
			*errors = append(*errors, fmt.Sprintf("ACT_NONE_CONFLICT: act=none / action=none cannot combine with status %q", parsed.Status))
		}
		for _, it := range parsed.Items {
			if cd1ActNoneForbiddenOps[it.Opcode] {
				*errors = append(*errors, fmt.Sprintf("line %d: ACT_NONE_CONFLICT: act=none / action=none forbids opcode %q", it.LineNo, it.Opcode))
			}
		}
	}
	if parsed.Status == "partial" {
		hasRem := false
		for _, it := range parsed.Items {
			if it.Opcode == "blk" || it.Opcode == "q" || it.Opcode == "fail" || it.Opcode == "stat" {
				hasRem = true
				break
			}
		}
		if !hasRem {
			*errors = append(*errors, "PARTIAL_WITHOUT_REMAINDER: status='partial' requires blk/q/fail/stat detailing unmet scope")
		}
	}

	var parentRefs map[string]string
	if opts.ParentContext != nil {
		parentRefs = opts.ParentContext.Refs
	}
	for h, target := range parsed.Refs {
		if prev, ok := parentRefs[h]; ok && prev != target {
			*errors = append(*errors, fmt.Sprintf("CONFLICTING_PARENT_CONTEXT: handle %q target %q conflicts with parent binding %q", h, target, prev))
		}
	}
	knownHandles := make(map[string]bool, len(parsed.Refs)+len(parentRefs))
	for h := range parsed.Refs {
		knownHandles[h] = true
	}
	for h := range parentRefs {
		knownHandles[h] = true
	}
	checkHandle := func(h string, lineNo int, desc string) {
		if !knownHandles[h] {
			*errors = append(*errors, fmt.Sprintf("line %d: UNBOUND_HANDLE %q %s without 'ref' or valid parent_context binding", lineNo, h, desc))
		} else if opts.ParentContext != nil && opts.ParentContext.ConsumedThreads != nil {
			if origThread, ok := opts.ParentContext.ConsumedThreads[h]; ok && origThread != opts.ParentContext.ThreadID {
				if _, local := parsed.Refs[h]; !local {
					*errors = append(*errors, fmt.Sprintf("line %d: CROSS_THREAD_HANDLE_REUSE: handle %q belongs to thread %q", lineNo, h, origThread))
				}
			}
		}
	}

	type fieldCheck struct {
		lineNo int
		txt    string
	}
	var fields []fieldCheck
	hdrKeys := make([]string, 0, len(parsed.HeaderMeta))
	for k := range parsed.HeaderMeta {
		hdrKeys = append(hdrKeys, k)
	}
	sort.Strings(hdrKeys)
	for _, k := range hdrKeys {
		fields = append(fields, fieldCheck{1, k + "=" + parsed.HeaderMeta[k]})
	}
	for _, it := range parsed.Items {
		if it.Opcode != "ref" {
			fields = append(fields, fieldCheck{it.LineNo, it.Payload})
		}
	}
	for _, fc := range fields {
		for _, trunc := range cd1FindTruncatedUUIDs(fc.txt) {
			*errors = append(*errors, fmt.Sprintf("line %d: UNBOUND_TRUNCATED_UUID %q; use full 36-char UUID or bind handle in 'ref'", fc.lineNo, trunc))
		}
		for _, hm := range cd1FindHandleUses(fc.txt) {
			checkHandle(hm.handle, fc.lineNo, hm.desc)
		}
	}

	hasPassChk, hasFailChk := false, false
	for _, it := range parsed.Items {
		switch it.Opcode {
		case "base":
			firstSeg, _, _ := strings.Cut(it.Payload, ";")
			lead := cd1SplitLeadWord(firstSeg)
			if strings.Contains(lead, "@") {
				_, rev, _ := strings.Cut(lead, "@")
				if cd1ShortHexRe.MatchString(rev) && !knownHandles[rev] {
					*errors = append(*errors, fmt.Sprintf("line %d: TRUNCATED_COMMIT_SHA %q in 'base'; use full 40-char SHA or bound ref", it.LineNo, rev))
				} else if cd1HandleRe.MatchString(rev) && !cd1NonHandleLiterals[strings.ToLower(rev)] {
					checkHandle(rev, it.LineNo, "in 'base' revision")
				}
			} else if !strings.ContainsAny(lead, "=/.") {
				if cd1ShortHexRe.MatchString(lead) && !knownHandles[lead] {
					*errors = append(*errors, fmt.Sprintf("line %d: TRUNCATED_COMMIT_SHA %q in 'base'; use full 40-char SHA or bound ref", it.LineNo, lead))
				} else if cd1HandleRe.MatchString(lead) && !cd1NonHandleLiterals[strings.ToLower(lead)] {
					checkHandle(lead, it.LineNo, "in 'base'")
				}
			}
		case "pat":
			lead := cd1SplitLeadWord(it.Payload)
			if !strings.ContainsAny(lead, "/.=") && !strings.HasPrefix(lead, "+") {
				rev := lead
				if strings.Contains(lead, "@") {
					_, rev, _ = strings.Cut(lead, "@")
				}
				if cd1ShortHexRe.MatchString(rev) && !knownHandles[rev] {
					*errors = append(*errors, fmt.Sprintf("line %d: TRUNCATED_COMMIT_SHA %q in 'pat'; use full 40-char SHA or bound ref", it.LineNo, rev))
				}
			}
		case "ho", "dep":
			if cd1HasUndefinedOrderOp(it.Payload) {
				*errors = append(*errors, fmt.Sprintf("line %d: UNDEFINED_ORDER_OPERATOR '<' in %q; use '->' for causal/temporal sequence", it.LineNo, it.Opcode))
			}
		case "chk":
			ok, p, f := cd1ChkOutcome(it.Payload, isD)
			if !ok {
				*errors = append(*errors, fmt.Sprintf("line %d: malformed 'chk' payload %q; expected '<cmd> <pass>/<fail>/<skip>' or '<cmd> pass|fail|skip|unknown'", it.LineNo, it.Payload))
			}
			if p {
				hasPassChk = true
			}
			if f {
				hasFailChk = true
			}
		case "expand":
			if (!strings.Contains(it.Payload, "re=") && !strings.Contains(it.Payload, "target=")) || (!strings.Contains(it.Payload, "why=") && !strings.Contains(it.Payload, "reason=")) {
				*errors = append(*errors, fmt.Sprintf("line %d: malformed 'expand' payload %q; requires target/re and reason/why", it.LineNo, it.Payload))
			}
		}
	}
	if parsed.Status == "verified" && (!hasPassChk || hasFailChk) {
		*errors = append(*errors, "VERIFIED_WITHOUT_PASSING_CHK: status='verified' requires at least one passing 'chk' with zero failing checks")
	}
	_ = warnings
}

func cd1ValidateC(nonempty []cd1Line, rawText string, opts CD1ValidateOptions) CD1ValidationResult {
	var errors, warnings []string
	hdr := map[string]string{}
	refs := map[string]string{}
	var items []CD1ParsedItem

	for _, ln := range nonempty {
		m := cd1CLineRe.FindStringSubmatch(ln.text)
		if len(m) != 3 {
			errors = append(errors, fmt.Sprintf("line %d: syntax error %q; expected 'key: value'", ln.no, ln.text))
			continue
		}
		key, val := m[1], strings.TrimSpace(m[2])
		if !cd1CFields[key] {
			errors = append(errors, fmt.Sprintf("line %d: unknown field %q for %s", ln.no, key, CairnC1Proto))
			continue
		}
		switch key {
		case "proto", "kind", "proj", "task", "status", "re", "from", "to", "act":
			if _, dup := hdr[key]; dup {
				errors = append(errors, fmt.Sprintf("line %d: duplicate header key %q", ln.no, key))
			}
			hdr[key] = val
		case "ref":
			cd1ParseRefsStr(val, refs, &errors, ln.no)
			items = append(items, CD1ParsedItem{Opcode: "ref", Payload: val, LineNo: ln.no})
		default:
			items = append(items, CD1ParsedItem{Opcode: key, Payload: val, LineNo: ln.no})
		}
	}
	for _, reqK := range []string{"proto", "kind", "proj", "task", "status"} {
		if _, ok := hdr[reqK]; !ok {
			errors = append(errors, fmt.Sprintf("MISSING_HEADER: missing required field %q", reqK))
		}
	}
	kind, status := hdr["kind"], hdr["status"]
	if kind != "" && !cd1ValidKindsC[kind] {
		errors = append(errors, fmt.Sprintf("INVALID_KIND: %q", kind))
	}
	if status != "" && !cd1ValidStatusesC[status] {
		errors = append(errors, fmt.Sprintf("INVALID_STATUS: %q", status))
	}
	hdrMeta := map[string]string{}
	for _, k := range []string{"re", "from", "to", "act"} {
		if v, ok := hdr[k]; ok {
			hdrMeta[k] = v
		}
	}
	parsed := &CD1ParsedMessage{
		Proto: CairnC1Proto, Kind: kind, Proj: hdr["proj"], Task: hdr["task"],
		Status: status, HeaderMeta: hdrMeta, Refs: refs, Items: items, RawText: rawText,
	}
	cd1CheckCommonInvariants(parsed, opts, &errors, &warnings, false)
	if len(errors) > 0 {
		return CD1ValidationResult{Valid: false, Proto: CairnC1Proto, Errors: errors, Warnings: warnings}
	}
	return CD1ValidationResult{Valid: true, Proto: CairnC1Proto, Errors: nil, Warnings: warnings, Parsed: parsed}
}

func cd1ValidateD(nonempty []cd1Line, rawText string, opts CD1ValidateOptions) CD1ValidationResult {
	var errors, warnings []string
	tokens := strings.Fields(nonempty[0].text)
	if len(tokens) < 4 {
		return CD1ValidationResult{
			Valid:  false,
			Proto:  CD1Proto,
			Errors: []string{"line 1: MALFORMED_HEADER: expected 'cd1 <kind> <proj> <task> <status> [k=v ...]'"},
		}
	}
	var kind, proj, task, status string
	var extra []string
	allExtraKV := true
	for _, t := range tokens[4:] {
		if !strings.Contains(t, "=") {
			allExtraKV = false
			break
		}
	}
	if opts.EnvelopeKind != "" && !cd1ValidKindsD[tokens[1]] && !strings.Contains(tokens[3], "=") && allExtraKV {
		envCanon := cd1KindCanonical[opts.EnvelopeKind]
		if envCanon == "" {
			envCanon = opts.EnvelopeKind
		}
		kind = cd1CanonicalToD[envCanon]
		if kind == "" {
			kind = opts.EnvelopeKind
		}
		proj, task, status = tokens[1], tokens[2], tokens[3]
		extra = tokens[4:]
	} else {
		if len(tokens) < 5 {
			return CD1ValidationResult{
				Valid:  false,
				Proto:  CD1Proto,
				Errors: []string{"line 1: MALFORMED_HEADER: expected 'cd1 <kind> <proj> <task> <status> [k=v ...]'"},
			}
		}
		kind, proj, task, status = tokens[1], tokens[2], tokens[3], tokens[4]
		extra = tokens[5:]
	}

	if !cd1ValidKindsD[kind] {
		errors = append(errors, fmt.Sprintf("line 1: INVALID_KIND %q", kind))
	}
	if !cd1ValidStatusesD[status] {
		errors = append(errors, fmt.Sprintf("line 1: INVALID_STATUS %q", status))
	}
	hdrMeta := map[string]string{}
	for _, tok := range extra {
		eq := strings.IndexByte(tok, '=')
		if eq < 0 {
			errors = append(errors, fmt.Sprintf("line 1: malformed header attribute %q; expected key=value", tok))
			continue
		}
		k, v := tok[:eq], tok[eq+1:]
		if !cd1ValidHdrKeysD[k] {
			errors = append(errors, fmt.Sprintf("line 1: unknown header key %q", k))
			continue
		}
		if prev, dup := hdrMeta[k]; dup {
			errors = append(errors, fmt.Sprintf("line 1: DUPLICATE_HEADER_KEY duplicate header attribute %q (%q vs %q)", k, prev, v))
			continue
		}
		if k == "act" && !cd1ValidActions[v] {
			errors = append(errors, fmt.Sprintf("line 1: invalid act=%q", v))
		}
		if v == "" {
			errors = append(errors, fmt.Sprintf("line 1: empty value for header key %q", k))
		}
		hdrMeta[k] = v
	}

	refs := map[string]string{}
	var items []CD1ParsedItem
	for _, ln := range nonempty[1:] {
		sp := strings.IndexByte(ln.text, ' ')
		if sp < 0 {
			errors = append(errors, fmt.Sprintf("line %d: syntax error %q; expected '<opcode> <payload>'", ln.no, ln.text))
			continue
		}
		op, payload := ln.text[:sp], strings.TrimSpace(ln.text[sp+1:])
		if strings.HasSuffix(op, ":") || strings.HasSuffix(op, "|") || strings.Contains(op, "|") {
			errors = append(errors, fmt.Sprintf("line %d: FORBIDDEN_PUNCTUATION in opcode %q; cd1 uses '<opcode> <payload>' without ':' or '|'", ln.no, op))
			continue
		}
		if !cd1DOpcodes[op] {
			errors = append(errors, fmt.Sprintf("line %d: UNKNOWN_OPCODE %q", ln.no, op))
			continue
		}
		if payload == "" {
			errors = append(errors, fmt.Sprintf("line %d: EMPTY_PAYLOAD for opcode %q", ln.no, op))
			continue
		}
		if op == "ref" {
			cd1ParseRefsStr(payload, refs, &errors, ln.no)
		}
		items = append(items, CD1ParsedItem{Opcode: op, Payload: payload, LineNo: ln.no})
	}

	parsed := &CD1ParsedMessage{
		Proto: CD1Proto, Kind: kind, Proj: proj, Task: task,
		Status: status, HeaderMeta: hdrMeta, Refs: refs, Items: items, RawText: rawText,
	}
	cd1CheckCommonInvariants(parsed, opts, &errors, &warnings, true)
	if len(errors) > 0 {
		return CD1ValidationResult{Valid: false, Proto: CD1Proto, Errors: errors, Warnings: warnings}
	}
	return CD1ValidationResult{Valid: true, Proto: CD1Proto, Errors: nil, Warnings: warnings, Parsed: parsed}
}

func cd1ReplaceStandalone(txt, tok, replacement string, maxCount int) string {
	var b strings.Builder
	idx := 0
	replaced := 0
	for idx < len(txt) {
		if maxCount > 0 && replaced >= maxCount {
			b.WriteString(txt[idx:])
			break
		}
		pos := strings.Index(txt[idx:], tok)
		if pos < 0 {
			b.WriteString(txt[idx:])
			break
		}
		start := idx + pos
		end := start + len(tok)
		prevOK := start == 0 || !cd1WordChar(txt[start-1])
		nextOK := end == len(txt) || !cd1WordChar(txt[end])
		if prevOK && nextOK {
			b.WriteString(txt[idx:start])
			b.WriteString(replacement)
			idx = end
			replaced++
		} else {
			b.WriteString(txt[idx : start+1])
			idx = start + 1
		}
	}
	return b.String()
}

func cd1ExpandMacros(txt string, refs map[string]string) string {
	out := txt
	for _, m := range cd1Macros {
		idx := 0
		var b strings.Builder
		for idx < len(out) {
			pos := strings.Index(out[idx:], m.name)
			if pos < 0 {
				b.WriteString(out[idx:])
				break
			}
			start := idx + pos
			end := start + len(m.name)
			prevOK := start == 0 || (!cd1WordChar(out[start-1]) && out[start-1] != '/' && out[start-1] != '.' && out[start-1] != '-')
			nextOK := end == len(out) || (!cd1WordChar(out[end]) && out[end] != '/' && out[end] != '.' && out[end] != '-')
			if prevOK && nextOK {
				b.WriteString(out[idx:start])
				b.WriteString(m.name + " [" + m.exp + "]")
				idx = end
			} else {
				b.WriteString(out[idx : start+1])
				idx = start + 1
			}
		}
		out = b.String()
	}
	keys := make([]string, 0, len(refs))
	for k := range refs {
		if len(k) >= 2 {
			keys = append(keys, k)
		}
	}
	sort.Strings(keys)
	for _, h := range keys {
		out = cd1ReplaceStandalone(out, h, fmt.Sprintf("%s (%s)", h, refs[h]), 1)
	}
	return out
}

// RenderCD1Human renders a structurally validated cd1 / cairn-c/1 message to prose.
func RenderCD1Human(text string, opts CD1ValidateOptions) (string, error) {
	res := ValidateCD1Message(text, opts)
	if !res.Valid || res.Parsed == nil {
		return "", fmt.Errorf("Cannot render invalid message: %s", strings.Join(res.Errors, "; "))
	}
	p := res.Parsed
	effectiveRefs := map[string]string{}
	if opts.ParentContext != nil {
		for k, v := range opts.ParentContext.Refs {
			effectiveRefs[k] = v
		}
	}
	for k, v := range p.Refs {
		effectiveRefs[k] = v
	}

	kindCanon := cd1KindCanonical[p.Kind]
	if kindCanon == "" {
		kindCanon = p.Kind
	}
	hdrBits := []string{
		fmt.Sprintf("[%s]", strings.ToUpper(kindCanon)),
		"Project: " + p.Proj,
		"Task: " + p.Task,
		"Status: " + p.Status,
	}
	for _, k := range []string{"re", "from", "to", "act", "ttl"} {
		v, ok := p.HeaderMeta[k]
		if !ok {
			continue
		}
		if k == "act" && opts.EnvelopeAction != "" {
			continue
		}
		if target, ok := effectiveRefs[v]; ok {
			v = fmt.Sprintf("%s (%s)", v, target)
		}
		hdrBits = append(hdrBits, k+"="+v)
	}
	if opts.EnvelopeAction != "" {
		hdrBits = append(hdrBits, "action="+opts.EnvelopeAction)
	}
	if opts.ParentContext != nil {
		hdrBits = append(hdrBits, fmt.Sprintf("thread=%s (v%d)", opts.ParentContext.ThreadID, opts.ParentContext.SourceVersion))
	}
	lines := []string{strings.Join(hdrBits, " | ")}
	if len(effectiveRefs) > 0 {
		keys := make([]string, 0, len(effectiveRefs))
		for k := range effectiveRefs {
			keys = append(keys, k)
		}
		sort.Strings(keys)
		refPairs := make([]string, 0, len(keys))
		for _, k := range keys {
			refPairs = append(refPairs, fmt.Sprintf("%s -> %s", k, effectiveRefs[k]))
		}
		lines = append(lines, "References: "+strings.Join(refPairs, ", "))
	}

	labelMap := map[string]string{
		"auth": "Authority", "base": "Baseline", "rw": "Writable Scope", "ro": "Read-Only Scope",
		"no": "Prohibitions", "deny": "Prohibitions", "act": "Action", "req": "Directive",
		"if": "Condition", "dep": "Dependency", "stop": "Stopping Rule", "obs": "Observation",
		"hyp": "Hypothesis", "rec": "Recommendation", "dec": "Decision", "dis": "Disagreement",
		"claim": "Claim", "pat": "Patch/Changes", "chk": "Verification", "blk": "Blocker",
		"fail": "Failure/Timeout", "stat": "Lifecycle Status", "ho": "Handoff", "next": "Next Step",
		"q": "Open Question", "expand": "Expansion Request", "meta": "Telemetry",
	}
	for _, it := range p.Items {
		if it.Opcode == "ref" {
			continue
		}
		lbl := labelMap[it.Opcode]
		if lbl == "" {
			lbl = strings.ToUpper(it.Opcode)
		}
		body := it.Payload
		if p.Proto == CD1Proto {
			body = cd1ExpandMacros(body, effectiveRefs)
		}
		if it.Opcode == "chk" && p.Proto == CD1Proto {
			if loc := cd1ChkTriplet.FindStringSubmatchIndex(body); len(loc) == 8 {
				body = body[:loc[0]] + fmt.Sprintf("pass=%s fail=%s skip=%s", body[loc[2]:loc[3]], body[loc[4]:loc[5]], body[loc[6]:loc[7]]) + body[loc[1]:]
			} else {
				fields := strings.Fields(body)
				if len(fields) >= 2 {
					tok := strings.TrimRight(fields[1], ";(")
					if tok == "pass" || tok == "fail" || tok == "skip" || tok == "unknown" {
						pos := strings.Index(body, fields[0]) + len(fields[0])
						idx := strings.Index(body[pos:], tok)
						if idx >= 0 {
							start := pos + idx
							body = body[:start] + "status=" + tok + body[start+len(tok):]
						}
					}
				}
			}
		}
		lines = append(lines, fmt.Sprintf("- %s: %s", lbl, body))
	}
	lines = append(lines, "Note: Structural validation only (not proof of truth, source fidelity, or authority beyond the stated Authority source).")
	return strings.Join(lines, "\n"), nil
}

// BuildCD1ExpandRequest constructs a valid cd1 expand request.
func BuildCD1ExpandRequest(targetEventID string, items []string, reason, proj, task string) string {
	return fmt.Sprintf("cd1 req %s %s blocked re=%s act=expand\nexpand re=%s items=%s why=%s",
		proj, task, targetEventID, targetEventID, strings.Join(items, ","), reason)
}

// RenderCD1OrExpandFallback renders a cd1 message to prose or returns a structured expand fallback.
func RenderCD1OrExpandFallback(text string, opts CD1ValidateOptions, targetEventID, proj, task string) (string, bool) {
	if rendered, err := RenderCD1Human(text, opts); err == nil {
		return rendered, false
	}
	res := ValidateCD1Message(text, opts)
	reason := "ambiguous"
	var missing []string
	seen := map[string]bool{}
	reUnbound := regexp.MustCompile(`UNBOUND_HANDLE "([^"]+)"`)
	for _, e := range res.Errors {
		if strings.Contains(e, "UNBOUND_HANDLE") {
			reason = "missing_ref"
			if m := reUnbound.FindStringSubmatch(e); len(m) == 2 && !seen[m[1]] {
				seen[m[1]] = true
				missing = append(missing, m[1])
			}
		} else if strings.Contains(e, "EXPIRED_PARENT_CONTEXT") || strings.Contains(e, "CROSS_THREAD_HANDLE_REUSE") {
			reason = "missing_ref"
		} else if strings.Contains(e, "UNSUPPORTED_VERSION") {
			reason = "unknown_proto"
		}
	}
	if len(missing) == 0 {
		missing = []string{"parent_context"}
	}
	for _, raw := range strings.Split(text, "\n") {
		f := strings.Fields(strings.TrimSpace(raw))
		if len(f) >= 4 && f[0] == CD1Proto {
			if cd1ValidKindsD[f[1]] && len(f) >= 5 {
				proj, task = f[2], f[3]
			} else {
				proj, task = f[1], f[2]
			}
			break
		}
	}
	if targetEventID == "" {
		targetEventID = "00000000-0000-0000-0000-000000000000"
	}
	if proj == "" {
		proj = "cairn"
	}
	if task == "" {
		task = "UNKNOWN"
	}
	return BuildCD1ExpandRequest(targetEventID, missing, reason, proj, task), true
}

// PlainProseFromBody preserves plain prose unchanged and converts valid cd1 bodies to prose.
func PlainProseFromBody(body string, opts CD1ValidateOptions) (string, error) {
	if _, isCompact := DetectCompactProtocol(body); !isCompact {
		return body, nil
	}
	return RenderCD1Human(body, opts)
}
