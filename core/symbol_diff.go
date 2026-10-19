package core

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"go/ast"
	"go/format"
	"go/parser"
	"go/token"
	"path"
	"regexp"
	"slices"
	"strconv"
	"strings"
)

// LineRange is a 1-indexed inclusive line span.
type LineRange struct {
	Start int `json:"start"`
	End   int `json:"end"`
}

// Overlaps reports whether two 1-indexed inclusive line ranges intersect.
func (r LineRange) Overlaps(other LineRange) bool {
	return r.Start <= other.End && other.Start <= r.End
}

// FileDiff summarizes unified diff hunks for a single repository-relative file.
type FileDiff struct {
	Path      string      `json:"path"`
	OldPath   string      `json:"old_path,omitempty"`
	Deleted   bool        `json:"deleted,omitempty"`
	OldRanges []LineRange `json:"old_ranges,omitempty"`
	NewRanges []LineRange `json:"new_ranges,omitempty"`
}

// SymbolSpan describes a top-level declaration or method extracted from source.
type SymbolSpan struct {
	Name            string    `json:"name"`
	QualifiedName   string    `json:"qualified_name,omitempty"`
	Kind            string    `json:"kind"`
	Span            LineRange `json:"span"`
	SignatureSHA256 string    `json:"signature_sha256"`
	BodySHA256      string    `json:"body_sha256"`
}

// EntityImpact describes the observed effect of a diff on one EntityRef.
// Results are diagnostic currentness signals, never automatic truth certification.
type EntityImpact struct {
	Entity  EntityRef `json:"entity"`
	Status  string    `json:"status"` // "unaffected", "file_modified", "body_modified", "signature_modified", "deleted"
	Matched string    `json:"matched,omitempty"`
}

// SymbolCurrentnessReport summarizes entity-level impact across a diff.
type SymbolCurrentnessReport struct {
	Impacts  []EntityImpact `json:"impacts"`
	Affected bool           `json:"affected"`
}

var unifiedHunkRE = regexp.MustCompile(`^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@`)

// ParseUnifiedDiff extracts per-file changed line ranges from a unified diff.
func ParseUnifiedDiff(diffText string) []FileDiff {
	var files []FileDiff
	var current *FileDiff
	for _, rawLine := range strings.Split(diffText, "\n") {
		line := strings.TrimRight(rawLine, "\r")
		switch {
		case strings.HasPrefix(line, "diff --git "):
			current = nil
		case strings.HasPrefix(line, "--- "):
			oldPath := cleanDiffPath(strings.TrimPrefix(line, "--- "))
			files = append(files, FileDiff{OldPath: oldPath, Path: oldPath})
			current = &files[len(files)-1]
		case strings.HasPrefix(line, "+++ ") && current != nil:
			newPath := cleanDiffPath(strings.TrimPrefix(line, "+++ "))
			if newPath == "" {
				current.Deleted = true
			} else {
				current.Path = newPath
			}
		case strings.HasPrefix(line, "@@ ") && current != nil:
			m := unifiedHunkRE.FindStringSubmatch(line)
			if m == nil {
				continue
			}
			oldStart, _ := strconv.Atoi(m[1])
			oldCount := parseHunkCount(m[2])
			newStart, _ := strconv.Atoi(m[3])
			newCount := parseHunkCount(m[4])
			if oldCount > 0 {
				current.OldRanges = append(current.OldRanges, LineRange{Start: oldStart, End: oldStart + oldCount - 1})
			} else if oldStart > 0 {
				current.OldRanges = append(current.OldRanges, LineRange{Start: oldStart, End: oldStart})
			}
			if newCount > 0 {
				current.NewRanges = append(current.NewRanges, LineRange{Start: newStart, End: newStart + newCount - 1})
			} else if newStart > 0 {
				current.NewRanges = append(current.NewRanges, LineRange{Start: newStart, End: newStart})
			}
		}
	}
	return files
}

func parseHunkCount(raw string) int {
	if raw == "" {
		return 1
	}
	n, _ := strconv.Atoi(raw)
	return n
}

func cleanDiffPath(raw string) string {
	if idx := strings.IndexByte(raw, '\t'); idx >= 0 {
		raw = raw[:idx]
	}
	raw = strings.TrimSpace(raw)
	if raw == "/dev/null" {
		return ""
	}
	raw = strings.TrimPrefix(raw, "a/")
	raw = strings.TrimPrefix(raw, "b/")
	return path.Clean(raw)
}

// ExtractGoSymbols parses Go source and returns top-level symbol spans and digests.
func ExtractGoSymbols(filename string, src []byte) ([]SymbolSpan, error) {
	fset := token.NewFileSet()
	file, err := parser.ParseFile(fset, filename, src, 0)
	if err != nil {
		return nil, err
	}
	var out []SymbolSpan
	for _, decl := range file.Decls {
		switch d := decl.(type) {
		case *ast.FuncDecl:
			name := d.Name.Name
			qualified := name
			if d.Recv != nil && len(d.Recv.List) > 0 {
				if recv := receiverTypeName(d.Recv.List[0].Type); recv != "" {
					qualified = recv + "." + name
				}
			}
			sigCopy := *d
			sigCopy.Body = nil
			sigDigest := nodeSHA256(fset, &sigCopy)
			bodyDigest := sigDigest
			if d.Body != nil {
				bodyDigest = nodeSHA256(fset, d.Body)
			}
			out = append(out, SymbolSpan{
				Name:            name,
				QualifiedName:   qualified,
				Kind:            "func",
				Span:            nodeLineRange(fset, d),
				SignatureSHA256: sigDigest,
				BodySHA256:      bodyDigest,
			})
		case *ast.GenDecl:
			for _, spec := range d.Specs {
				switch s := spec.(type) {
				case *ast.TypeSpec:
					digest := nodeSHA256(fset, s)
					out = append(out, SymbolSpan{
						Name:            s.Name.Name,
						QualifiedName:   s.Name.Name,
						Kind:            "type",
						Span:            nodeLineRange(fset, s),
						SignatureSHA256: nodeSHA256(fset, s.Name),
						BodySHA256:      digest,
					})
				case *ast.ValueSpec:
					digest := nodeSHA256(fset, s)
					for _, ident := range s.Names {
						out = append(out, SymbolSpan{
							Name:            ident.Name,
							QualifiedName:   ident.Name,
							Kind:            strings.ToLower(d.Tok.String()),
							Span:            nodeLineRange(fset, s),
							SignatureSHA256: nodeSHA256(fset, ident),
							BodySHA256:      digest,
						})
					}
				}
			}
		}
	}
	return out, nil
}

func receiverTypeName(expr ast.Expr) string {
	switch t := expr.(type) {
	case *ast.Ident:
		return t.Name
	case *ast.StarExpr:
		return receiverTypeName(t.X)
	case *ast.IndexExpr:
		return receiverTypeName(t.X)
	case *ast.IndexListExpr:
		return receiverTypeName(t.X)
	default:
		return ""
	}
}

func nodeLineRange(fset *token.FileSet, n ast.Node) LineRange {
	return LineRange{
		Start: fset.Position(n.Pos()).Line,
		End:   fset.Position(n.End()).Line,
	}
}

func nodeSHA256(fset *token.FileSet, n ast.Node) string {
	var buf bytes.Buffer
	_ = format.Node(&buf, fset, n)
	sum := sha256.Sum256(buf.Bytes())
	return hex.EncodeToString(sum[:])
}

// CheckSymbolCurrentness evaluates how a unified diff and optional before/after
// Go source snapshots affect a record's EntityRef associations.
func CheckSymbolCurrentness(entities []EntityRef, diffs []FileDiff, beforeSrc, afterSrc map[string][]byte) SymbolCurrentnessReport {
	report := SymbolCurrentnessReport{Impacts: make([]EntityImpact, 0, len(entities))}
	beforeSyms := map[string][]SymbolSpan{}
	afterSyms := map[string][]SymbolSpan{}
	for file, src := range beforeSrc {
		if syms, err := ExtractGoSymbols(file, src); err == nil {
			beforeSyms[file] = syms
		}
	}
	for file, src := range afterSrc {
		if syms, err := ExtractGoSymbols(file, src); err == nil {
			afterSyms[file] = syms
		}
	}
	for _, ent := range entities {
		impact := evaluateEntityImpact(ent, diffs, beforeSyms, afterSyms)
		if impact.Status != "unaffected" {
			report.Affected = true
		}
		report.Impacts = append(report.Impacts, impact)
	}
	return report
}

func evaluateEntityImpact(ent EntityRef, diffs []FileDiff, beforeSyms, afterSyms map[string][]SymbolSpan) EntityImpact {
	if ent.Kind == "file" {
		for _, d := range diffs {
			if d.Path == ent.Name || d.OldPath == ent.Name {
				if d.Deleted {
					return EntityImpact{Entity: ent, Status: "deleted", Matched: d.OldPath}
				}
				return EntityImpact{Entity: ent, Status: "file_modified", Matched: d.Path}
			}
		}
		return EntityImpact{Entity: ent, Status: "unaffected"}
	}
	for file, oldList := range beforeSyms {
		oldSym, ok := findMatchingSymbol(oldList, ent.Name)
		if !ok {
			continue
		}
		newList, hasAfter := afterSyms[file]
		if !hasAfter {
			return EntityImpact{Entity: ent, Status: "deleted", Matched: file}
		}
		newSym, ok := findMatchingSymbol(newList, ent.Name)
		if !ok {
			return EntityImpact{Entity: ent, Status: "deleted", Matched: file}
		}
		if oldSym.SignatureSHA256 != newSym.SignatureSHA256 {
			return EntityImpact{Entity: ent, Status: "signature_modified", Matched: file}
		}
		if oldSym.BodySHA256 != newSym.BodySHA256 {
			return EntityImpact{Entity: ent, Status: "body_modified", Matched: file}
		}
		for _, d := range diffs {
			if (d.Path == file || d.OldPath == file) && rangesOverlap(d.OldRanges, oldSym.Span) {
				return EntityImpact{Entity: ent, Status: "body_modified", Matched: file}
			}
		}
		return EntityImpact{Entity: ent, Status: "unaffected", Matched: file}
	}
	return EntityImpact{Entity: ent, Status: "unaffected"}
}

func findMatchingSymbol(list []SymbolSpan, query string) (SymbolSpan, bool) {
	for _, s := range list {
		if s.QualifiedName == query || s.Name == query {
			return s, true
		}
	}
	return SymbolSpan{}, false
}

func rangesOverlap(ranges []LineRange, target LineRange) bool {
	for _, r := range ranges {
		if r.Overlaps(target) {
			return true
		}
	}
	return false
}

var (
	noteInlineCodeRE = regexp.MustCompile("`([^`\\r\\n]{2,256})`")
	noteFileTokenRE  = regexp.MustCompile(`(?:^|[\s("'\x60])([A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+\.[A-Za-z0-9_]{1,16})`)
	noteSymbolRE     = regexp.MustCompile(`^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?$`)
)

// SuggestNoteEntities extracts fallible file and symbol EntityRef candidates from a
// Markdown note body, optionally cross-checked against known workspace files/symbols.
// Returned suggestions are never certified workspace observations.
func SuggestNoteEntities(body string, knownFiles, knownSymbols []string) []EntityRef {
	fileSet := make(map[string]bool, len(knownFiles))
	for _, f := range knownFiles {
		fileSet[f] = true
	}
	symSet := make(map[string]bool, len(knownSymbols))
	for _, s := range knownSymbols {
		symSet[s] = true
	}
	var out []EntityRef
	seen := map[EntityRef]bool{}
	add := func(ref EntityRef) {
		if len(out) >= 16 || seen[ref] {
			return
		}
		if normalized, err := NormalizeEntities([]EntityRef{ref}); err == nil && len(normalized) == 1 {
			seen[normalized[0]] = true
			out = append(out, normalized[0])
		}
	}
	for _, m := range noteFileTokenRE.FindAllStringSubmatch(body, -1) {
		cand := path.Clean(strings.TrimPrefix(m[1], "./"))
		if strings.Contains(cand, "://") || strings.Contains(cand, "..") {
			continue
		}
		if len(fileSet) == 0 || fileSet[cand] {
			add(EntityRef{Kind: "file", Name: cand})
		}
	}
	for _, m := range noteInlineCodeRE.FindAllStringSubmatch(body, -1) {
		tok := strings.TrimSpace(m[1])
		tok = strings.TrimSuffix(tok, "()")
		if fileSet[tok] {
			add(EntityRef{Kind: "file", Name: tok})
			continue
		}
		if !noteSymbolRE.MatchString(tok) {
			continue
		}
		if len(symSet) > 0 {
			if symSet[tok] {
				add(EntityRef{Kind: "symbol", Name: tok})
			}
			continue
		}
		if strings.Contains(tok, ".") || strings.Contains(tok, "_") || hasMixedCase(tok) {
			add(EntityRef{Kind: "symbol", Name: tok})
		}
	}
	slices.SortFunc(out, func(a, b EntityRef) int {
		if c := strings.Compare(a.Kind, b.Kind); c != 0 {
			return c
		}
		return strings.Compare(a.Name, b.Name)
	})
	return out
}

func hasMixedCase(s string) bool {
	hasUpper, hasLower := false, false
	for i := 0; i < len(s); i++ {
		if s[i] >= 'A' && s[i] <= 'Z' {
			hasUpper = true
		} else if s[i] >= 'a' && s[i] <= 'z' {
			hasLower = true
		}
	}
	return hasUpper && hasLower
}
