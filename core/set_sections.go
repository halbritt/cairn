package core

import (
	"context"
	"maps"
	"slices"
	"strings"
	"unicode"
	"unicode/utf8"

	"github.com/jackc/pgx/v5"
)

// SetSectionsRequest updates or appends named Markdown "## <Heading>" sections
// in an active Class A note while preserving all outside text and metadata.
type SetSectionsRequest struct {
	RequestID       string            `json:"request_id"`
	RecordID        string            `json:"record_id"`
	ExpectedVersion int               `json:"expected_version"`
	Repo            string            `json:"repo"`
	Sections        map[string]string `json:"sections"`
}

func (s *Store) SetSections(ctx context.Context, req SetSectionsRequest) (Revision, error) {
	return s.setSections(ctx, req, Destination{Name: "local", AllowLocal: true})
}

func (s *Store) SetSectionsForDestination(ctx context.Context, req SetSectionsRequest, dest Destination) (Revision, error) {
	if err := validEventDestination(dest); err != nil {
		return Revision{}, err
	}
	return s.setSections(ctx, req, dest)
}

func (s *Store) setSections(ctx context.Context, req SetSectionsRequest, dest Destination) (Revision, error) {
	if err := validID(req.RecordID); err != nil {
		return Revision{}, err
	}
	if req.ExpectedVersion < 1 || strings.TrimSpace(req.Repo) == "" || req.Repo == "*" || len(req.Repo) > 256 || len(req.Sections) == 0 || len(req.Sections) > 16 {
		return Revision{}, failure("INVALID_REQUEST", "set-sections requires a repository, positive expected_version and 1-16 section updates")
	}
	for name, body := range req.Sections {
		if name == "" || len(name) > 128 || !utf8.ValidString(name) || strings.TrimSpace(name) != name || strings.HasPrefix(name, "#") || strings.ContainsFunc(name, unicode.IsControl) {
			return Revision{}, failure("INVALID_REQUEST", "section name requires 1-128 valid UTF-8 bytes without leading '#', surrounding whitespace or control characters")
		}
		if strings.TrimSpace(body) == "" || len(body) > 65536 || !utf8.ValidString(body) {
			return Revision{}, failure("INVALID_REQUEST", "section body requires 1-65536 nonblank UTF-8 bytes")
		}
		if hasUnclosedFence(body) {
			return Revision{}, failure("INVALID_REQUEST", "section body leaves a Markdown code fence open")
		}
	}
	if err := s.checkRepo(req.Repo); err != nil {
		return Revision{}, err
	}
	return privileged(ctx, s, "set-sections", req.RequestID, req, func(tx pgx.Tx) (Revision, error) {
		old, err := readRecord(ctx, tx, req.RecordID)
		if err != nil {
			return Revision{}, err
		}
		if old.Scope.Repo != req.Repo {
			return Revision{}, failure("AUTHORITY_DENIED", "record is outside the requested repository")
		}
		if old.Version != req.ExpectedVersion {
			return Revision{}, failure("VERSION_CONFLICT", "record changed; read the current version and reconcile the section updates")
		}
		if old.Class != "A" || old.Lifecycle != "active" {
			return Revision{}, failure("AUTHORITY_DENIED", "ordinary edit requires an active A record; use an audited transition")
		}
		merged, err := spliceSections(old.Body, req.Sections)
		if err != nil {
			return Revision{}, err
		}
		if strings.TrimSpace(merged) == "" || len(merged) > 65536 {
			return Revision{}, failure("INVALID_REQUEST", "section updates must leave a nonblank body of at most 65536 bytes")
		}
		draft := old.Draft
		draft.Body = merged
		draft.Sensitivity = old.Sensitivity
		record, err := s.editVersion(ctx, tx, EditRequest{req.RequestID, req.RecordID, req.ExpectedVersion, draft})
		if err != nil {
			return Revision{}, err
		}
		return Revision{record.RecordID, record.Version}, nil
	}, func(tx pgx.Tx) error {
		if dest.AllowLocal {
			return nil
		}
		var sensitivity string
		err := tx.QueryRow(ctx, `SELECT sensitivity FROM cairn.memory_record WHERE record_id=$1 FOR SHARE`, req.RecordID).Scan(&sensitivity)
		if err == pgx.ErrNoRows || err == nil && sensitivity == "local" {
			return failure("NOT_FOUND", "record not found")
		}
		return err
	})
}

type markdownHeading struct {
	name      string
	headStart int
	bodyStart int
	bodyEnd   int
}

func fenceRun(line string) (byte, int) {
	trimmed := strings.TrimLeft(line, " ")
	if len(line)-len(trimmed) > 3 || len(trimmed) < 3 {
		return 0, 0
	}
	ch := trimmed[0]
	if ch != '`' && ch != '~' {
		return 0, 0
	}
	n := 0
	for n < len(trimmed) && trimmed[n] == ch {
		n++
	}
	if ch == '`' && strings.ContainsRune(trimmed[n:], '`') {
		return 0, 0
	}
	return ch, n
}

func isClosingFence(line string, fenceCh byte, fenceLen int) bool {
	trimmed := strings.TrimLeft(line, " ")
	if len(line)-len(trimmed) > 3 || len(trimmed) < fenceLen {
		return false
	}
	n := 0
	for n < len(trimmed) && trimmed[n] == fenceCh {
		n++
	}
	if n < fenceLen {
		return false
	}
	return strings.TrimRight(trimmed[n:], " \t\r") == ""
}

func hasUnclosedFence(doc string) bool {
	inFence := false
	var fenceCh byte
	fenceLen := 0
	for _, rawLine := range strings.SplitAfter(doc, "\n") {
		line := strings.TrimSuffix(strings.TrimSuffix(rawLine, "\n"), "\r")
		if inFence {
			if isClosingFence(line, fenceCh, fenceLen) {
				inFence = false
			}
			continue
		}
		if ch, n := fenceRun(line); n > 0 {
			inFence = true
			fenceCh = ch
			fenceLen = n
		}
	}
	return inFence
}

func scanHeadings(doc string) ([]markdownHeading, error) {
	if hasUnclosedFence(doc) {
		return nil, failure("INVALID_REQUEST", "stored note leaves a Markdown code fence open; use revise or replace to repair it")
	}
	var out []markdownHeading
	inFence := false
	var fenceCh byte
	fenceLen := 0
	offset := 0
	for _, rawLine := range strings.SplitAfter(doc, "\n") {
		line := strings.TrimSuffix(strings.TrimSuffix(rawLine, "\n"), "\r")
		if inFence {
			if isClosingFence(line, fenceCh, fenceLen) {
				inFence = false
			}
		} else if ch, n := fenceRun(line); n > 0 {
			inFence = true
			fenceCh = ch
			fenceLen = n
		} else if strings.HasPrefix(line, "## ") {
			name := strings.TrimRight(line[3:], " \t")
			if name != "" {
				if len(out) > 0 {
					out[len(out)-1].bodyEnd = offset
				}
				out = append(out, markdownHeading{
					name:      name,
					headStart: offset,
					bodyStart: offset + len(rawLine),
					bodyEnd:   len(doc),
				})
			}
		}
		offset += len(rawLine)
	}
	return out, nil
}

func dominantEOL(doc string) string {
	crlf, lf := 0, 0
	for i := 0; i < len(doc); i++ {
		if doc[i] == '\n' {
			if i > 0 && doc[i-1] == '\r' {
				crlf++
			} else {
				lf++
			}
		}
	}
	if crlf > lf {
		return "\r\n"
	}
	return "\n"
}

func trailingBreaks(doc string) int {
	idx := len(doc)
	breaks := 0
	for idx > 0 && (doc[idx-1] == '\n' || doc[idx-1] == '\r') {
		wasLF := doc[idx-1] == '\n'
		idx--
		if wasLF && idx > 0 && doc[idx-1] == '\r' {
			idx--
		}
		breaks++
	}
	return breaks
}

func spliceSections(doc string, sections map[string]string) (string, error) {
	keys := slices.Sorted(maps.Keys(sections))
	merged := doc
	for _, name := range keys {
		headings, err := scanHeadings(merged)
		if err != nil {
			return "", err
		}
		matches := 0
		var target markdownHeading
		for _, h := range headings {
			if h.name == name {
				matches++
				target = h
			}
		}
		if matches > 1 {
			return "", failure("INVALID_REQUEST", "section heading occurs more than once in the note; use revise or replace to disambiguate")
		}
		cleanBody := strings.TrimRight(sections[name], "\r\n")
		eol := dominantEOL(merged)
		if matches == 1 {
			ws := target.bodyEnd
			for ws > target.bodyStart && (merged[ws-1] == '\n' || merged[ws-1] == '\r') {
				ws--
			}
			prefix := merged[:target.bodyStart]
			if len(prefix) > 0 && prefix[len(prefix)-1] != '\n' && prefix[len(prefix)-1] != '\r' {
				prefix += eol
			}
			merged = prefix + cleanBody + merged[ws:target.bodyEnd] + merged[target.bodyEnd:]
			continue
		}
		breaks := trailingBreaks(merged)
		var sep string
		if len(merged) > 0 {
			for i := breaks; i < 2; i++ {
				sep += eol
			}
		}
		merged = merged + sep + "## " + name + eol + cleanBody
	}
	return merged, nil
}
