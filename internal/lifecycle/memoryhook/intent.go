package memoryhook

import (
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"time"
	"unicode"
	"unicode/utf8"

	"github.com/halbritt/cairn/internal/lifecycle/admission"
	"github.com/halbritt/cairn/internal/lifecycle/value"
	"golang.org/x/text/cases"
	"golang.org/x/text/language"
)

func resolved(path, directory string) (string, error) {
	points, err := (admission.OSProbe{Directory: directory}).Resolve(value.Text(path))
	if err != nil {
		return "", err
	}
	return admission.PathBytes(points)
}
func projectRoot(event *value.Value, directory string) (string, error) {
	if p := event.Get("project_path"); value.Truth(p) {
		return resolved(text(p), directory)
	}
	path, err := resolved(text(event.Get("cwd")), directory)
	if err != nil {
		return "", err
	}
	for parent := path; ; parent = filepath.Dir(parent) {
		if _, err := os.Stat(filepath.Join(parent, ".git")); err == nil {
			return parent, nil
		} else if !os.IsNotExist(err) && !os.IsPermission(err) {
			return "", err
		}
		if parent == filepath.Dir(parent) {
			break
		}
	}
	return path, nil
}
func baseName(path string) string {
	if path == filepath.Dir(path) {
		return ""
	}
	return filepath.Base(path)
}
func clip(s string, n int) string {
	if len(s) <= n {
		return s
	}
	s = s[:n]
	for !utf8.ValidString(s) {
		s = s[:len(s)-1]
	}
	return s
}
func workstreamPrefix(project string) string {
	return "Handoff: " + clip(strings.ReplaceAll(baseName(project), "\"", ""), 40) + " / "
}
func pySpace(r rune) bool   { return unicode.IsSpace(r) || r >= 0x1c && r <= 0x1f }
func strip(s string) string { return strings.TrimFunc(s, pySpace) }

var wordsRE = regexp.MustCompile(`[\p{L}\p{N}_.-]+`)
var filesRE = regexp.MustCompile(`[\p{L}\p{N}_./-]+\.[A-Za-z0-9_]+`)
var nonspaceRE = regexp.MustCompile(`\S*(?:://|@)\S*`)
var continueRE = regexp.MustCompile(`(?i)^\s*(?:please\s+)?(?:continue|resume)\b`)
var stopWords = strings.Fields("a an and are as at be before can check continue could do does for from have how i in into is it its make me memory need next of on or please project task test tests that the then these this to use using want was we what when which with work would you your")

func contains(ss []string, s string) bool {
	for _, v := range ss {
		if v == s {
			return true
		}
	}
	return false
}
func terms(s string) []string {
	var out []string
	for _, word := range wordsRE.FindAllString(cases.Lower(language.Und).String(s), -1) {
		word = strings.Trim(word, ".-")
		if utf8.RuneCountInString(word) >= 3 && !contains(stopWords, word) {
			out = append(out, word)
		}
	}
	return out
}
func quotedPhrases(prompt string) []string {
	var phrases []string
	for pos := 0; pos < len(prompt); {
		// Fences are recognized only at line start after up to three spaces.
		lineStart := pos == 0 || prompt[pos-1] == '\n'
		if lineStart {
			start := pos
			for start < len(prompt) && start-pos < 3 && prompt[start] == ' ' {
				start++
			}
			if start < len(prompt) && (prompt[start] == '`' || prompt[start] == '~') {
				end := start
				for end < len(prompt) && prompt[end] == prompt[start] {
					end++
				}
				lineEnd := strings.IndexByte(prompt[end:], '\n')
				if lineEnd < 0 {
					lineEnd = len(prompt)
				} else {
					lineEnd += end
				}
				if end-start >= 3 && (prompt[start] != '`' || !strings.Contains(prompt[end:lineEnd], "`")) {
					marker := prompt[start]
					count := end - start
					pos = lineEnd
					for pos < len(prompt) {
						if prompt[pos] == '\n' {
							pos++
						}
						next := strings.IndexByte(prompt[pos:], '\n')
						if next < 0 {
							next = len(prompt)
						} else {
							next += pos
						}
						raw := prompt[pos:next]
						trimmed := strings.TrimLeft(raw, " ")
						indent := len(raw) - len(trimmed)
						n := 0
						for n < len(trimmed) && trimmed[n] == marker {
							n++
						}
						if indent <= 3 && n >= count && strings.Trim(trimmed[n:], " \t\r") == "" {
							pos = next
							break
						}
						pos = next
					}
					continue
				}
			}
		}
		if prompt[pos] == '`' {
			start := pos
			for pos < len(prompt) && prompt[pos] == '`' {
				pos++
			}
			size := pos - start
			found := false
			for next := pos; next < len(prompt); {
				i := strings.IndexByte(prompt[next:], '`')
				if i < 0 {
					break
				}
				i += next
				j := i
				for j < len(prompt) && prompt[j] == '`' {
					j++
				}
				if j-i == size {
					pos = j
					found = true
					break
				}
				next = j
			}
			if !found {
				if n := strings.IndexByte(prompt[pos:], '\n'); n >= 0 {
					pos += n + 1
				} else {
					pos = len(prompt)
				}
			}
			continue
		}
		if prompt[pos] == '"' {
			start := pos + 1
			end := strings.IndexByte(prompt[start:], '"')
			line := strings.IndexByte(prompt[start:], '\n')
			if end >= 0 && (line < 0 || end < line) {
				phrase := prompt[start : start+end]
				n := utf8.RuneCountInString(phrase)
				if n >= 3 && n <= 160 && len(terms(phrase)) > 0 {
					phrases = append(phrases, phrase)
				}
				pos = start + end + 1
			} else if line >= 0 {
				pos = start + line + 1
			} else {
				pos = len(prompt)
			}
			continue
		}
		pos++
	}
	return phrases
}
func recentNumber(v *value.Value) float64 {
	if v == nil {
		return 0
	}
	if v.Kind == value.Float {
		return v.Number
	}
	if v.Kind == value.Int {
		n, _ := strconv.ParseFloat(v.Integer, 64)
		return n
	}
	return 0
}

func retrievalIntent(event, state *value.Value, project string) (string, []string, error) {
	prompt := text(event.Get("prompt"))
	paths := []string{}
	cwd := text(event.Get("project_path"))
	if cwd == "" {
		cwd = text(event.Get("cwd"))
	}
	fileText := nonspaceRE.ReplaceAllString(prompt, " ")
	for _, part := range filesRE.FindAllString(fileText, -1) {
		part = strings.TrimRight(strings.Trim(part, "`\"',;:()[]"), ".")
		if part == "" || strings.Contains(part, "\n") {
			continue
		}
		candidate := part
		if !filepath.IsAbs(candidate) {
			candidate = filepath.Join(cwd, candidate)
		}
		full, err := resolved(candidate, cwd)
		if err != nil {
			return "", nil, err
		}
		rel, err := filepath.Rel(project, full)
		if err != nil || rel == ".." || strings.HasPrefix(rel, "../") || rel == "." || len(rel) > 512 {
			continue
		}
		explicit := strings.Contains(part, "/") && (strings.HasPrefix(part, "./") || strings.HasPrefix(part, "../") || strings.HasPrefix(part, "/") || !strings.Contains(strings.SplitN(part, "/", 2)[0], "."))
		if !explicit {
			info, err := os.Stat(filepath.Join(project, rel))
			if err != nil || !info.Mode().IsRegular() {
				continue
			}
		}
		if !contains(paths, rel) {
			paths = append(paths, rel)
		}
	}
	now := float64(time.Now().UnixNano()) / 1e9
	hints := state.Get("hints")
	var recent []string
	if files := hints.Get("files"); files != nil && files.Kind == value.Object {
		for _, m := range files.Members {
			name := string(m.Key)
			if now-recentNumber(m.Value) < 900 && !contains(paths, name) {
				recent = append(recent, name)
			}
		}
	}
	if len(paths) > 16 {
		paths = paths[len(paths)-16:]
	}
	remaining := 16 - len(paths)
	if remaining > 0 {
		if len(recent) > remaining {
			recent = recent[len(recent)-remaining:]
		}
		paths = append(paths, recent...)
	}
	phrases := quotedPhrases(prompt)
	var errorTerms, searchTerms []string
	if now-recentNumber(hints.Get("error_at")) < 900 {
		if errors := hints.Get("errors"); errors != nil && errors.Kind == value.Array {
			for _, v := range errors.Items {
				errorTerms = append(errorTerms, text(v))
			}
		}
	}
	if searches := hints.Get("searches"); searches != nil && searches.Kind == value.Object {
		for _, m := range searches.Members {
			if now-recentNumber(m.Value) < 900 {
				searchTerms = append(searchTerms, string(m.Key))
			}
		}
	}
	projectWords := terms(baseName(project))
	var keywords []string
	for _, word := range append(terms(prompt), searchTerms...) {
		if !contains(keywords, word) && !contains(projectWords, word) && len(word) <= 128 {
			keywords = append(keywords, word)
		}
	}
	var anchors []string
	for _, anchor := range append(append(append([]string{}, paths...), phrases...), errorTerms...) {
		if !contains(anchors, anchor) {
			anchors = append(anchors, anchor)
		}
	}
	if topic := text(event.Get("workstream")); topic != "" {
		anchors = append([]string{workstreamPrefix(project) + topic}, anchors...)
	}
	if source := text(event.Get("source")); source == "resume" || source == "compact" {
		title := text(state.Get("workstream"))
		if state.Get("workstream") == nil {
			title = "Handoff: " + clip(strings.ReplaceAll(baseName(project), "\"", ""), 70) + " / Claude session " + text(event.Get("session_id"))
		}
		anchors = append([]string{title}, anchors...)
		if state.Get("workstream") == nil {
			anchors = append(anchors, strings.TrimRightFunc(workstreamPrefix(project), pySpace))
		}
	}
	var filtered []string
	for _, a := range anchors {
		if len(a) <= 256 && !strings.Contains(a, "\"") {
			filtered = append(filtered, a)
			if len(filtered) == 8 {
				break
			}
		}
	}
	anchors = filtered
	if continueRE.MatchString(prompt) {
		anchors = append([]string{strings.TrimRightFunc(workstreamPrefix(project), pySpace)}, anchors...)
		if len(anchors) > 8 {
			anchors = anchors[:8]
		}
	}
	query := baseName(project)
	var parts []string
	for _, a := range anchors {
		parts = append(parts, "\""+a+"\"")
	}
	parts = append(parts, errorTerms...)
	parts = append(parts, keywords...)
	for _, part := range parts {
		if len(query)+1+len(part) <= 4000 {
			query += " " + part
		}
	}
	return query, paths, nil
}
