package memoryhook

import (
	"context"
	"errors"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"syscall"
	"time"
	"unicode"
	"unicode/utf8"

	"github.com/halbritt/cairn/internal/lifecycle/value"
	"github.com/halbritt/cairn/internal/statefile"
)

func toolObservationEvent(event *value.Value) bool {
	return contains([]string{"PreToolUse", "PostToolUse", "PostToolUseFailure"}, text(event.Get("hook_event_name")))
}

// runToolObservation is reached only after the ordinary entry's shared admission
// and unbound guard. It never constructs an API client or reaches recall/capture.
func runToolObservation(ctx context.Context, config, event *value.Value, directory string, trace map[string]any) Result {
	boundary := func(err error) Result { return failure(event, err) }
	if !contains([]string{"opencode", "agy", "codex", "claude", "hermes"}, text(config.Get("harness"))) {
		return boundary(fail("ordinary tool observation harness remains unavailable"))
	}
	if !value.SameText(event.Get("hook_event_name"), "PostToolUseFailure") && !value.SameText(config.Get("harness"), "hermes") && !value.Truth(event.Get("project_path")) && !value.Truth(event.Get("workstream")) {
		files, searches, err := observationHints(event, directory)
		if err != nil {
			return boundary(err)
		}
		if len(files) == 0 && len(searches) == 0 {
			return Result{Stdout: "{}\n"}
		}
	}
	for _, key := range []string{"repo", "cairn", "socket", "token_file", "state_dir"} {
		if config.Get(key) == nil {
			return boundary(&value.Error{Class: "KeyError", Message: key})
		}
	}
	dir, err := pathValue(config.Get("state_dir"))
	if err != nil {
		return boundary(err)
	}
	if err = os.MkdirAll(dir, 0700); err != nil {
		return boundary(err)
	}
	session := text(event.Get("session_id"))
	if value.SameText(config.Get("harness"), "hermes") {
		session = hashText(session)
	}
	path := filepath.Join(dir, session+".json")
	trace["stage"] = "lock_attempt"
	locked := false
	output := value.Mapping()
	err = statefile.LegacyLock(ctx, filepath.Join(dir, session+".lock"), 0, func() error {
		locked = true
		trace["stage"] = "locked"
		state, err := captureState(path)
		if err != nil {
			return err
		}
		project := ""
		if value.Truth(event.Get("workstream")) {
			project, err = projectRoot(event, directory)
			if err != nil {
				return err
			}
		}
		state = captureEventState(state, event, project)
		if err = observeToolState(event, state, directory); err != nil {
			return err
		}
		if err = saveState(path, state); err != nil {
			return err
		}
		trace["stage"] = "saved"
		if value.SameText(config.Get("harness"), "hermes") {
			status := value.Mapping()
			for _, key := range []string{"last_recall", "last_capture", "workstream"} {
				if v := state.Get(key); v != nil {
					status.Set(key, v)
				}
			}
			output.Set("cairn_status", status)
		}
		return nil
	})
	if !locked && errors.Is(err, syscall.EAGAIN) {
		trace["cause"] = "lock_busy"
		err = fail("a memory hook is already running for this session")
	}
	if err != nil {
		return boundary(err)
	}
	raw, err := encoded(output, false)
	if err != nil {
		return boundary(err)
	}
	return Result{Stdout: string(raw) + "\n"}
}

func observeToolState(event, state *value.Value, directory string) error {
	hints := state.Get("hints")
	if hints == nil {
		hints = value.Mapping()
		state.Set("hints", hints)
	}
	if hints.Kind != value.Object {
		return fail("stored tool hints shape remains unavailable")
	}
	if value.SameText(event.Get("hook_event_name"), "PostToolUseFailure") {
		failureText := event.Get("error")
		if failureText == nil {
			failureText = value.Text("")
		}
		if failureText.Kind != value.String {
			return &value.Error{Class: "TypeError", Message: "tool error must be text"}
		}
		identifiers := array()
		for _, identifier := range observationErrors(text(failureText)) {
			identifiers.Items = append(identifiers.Items, value.Text(identifier))
		}
		hints.Set("errors", identifiers)
		hints.Set("error_at", floating(float64(time.Now().UnixNano())/1e9))
		return nil
	}
	files, searches, err := observationHints(event, directory)
	if err != nil {
		return err
	}
	now := floating(float64(time.Now().UnixNano()) / 1e9)
	for _, group := range []struct {
		key   string
		names []string
	}{{"files", files}, {"searches", searches}} {
		if len(group.names) == 0 {
			continue
		}
		entries := hints.Get(group.key)
		if entries == nil {
			entries = value.Mapping()
		}
		if entries.Kind != value.Object {
			if entries.Kind == value.Array {
				return &value.Error{Class: "TypeError", Message: "stored tool hint list cannot pop a named entry"}
			}
			return fail("stored tool hint entries shape remains unavailable")
		}
		for _, name := range group.names {
			entries = entries.Without(name)
			entries.Set(name, now)
		}
		if len(entries.Members) > 16 {
			entries.Members = entries.Members[len(entries.Members)-16:]
		}
		hints.Set(group.key, entries)
	}
	return nil
}

func observationHints(event *value.Value, directory string) ([]string, []string, error) {
	input := event.Get("tool_input")
	if input == nil || input.Kind != value.Object {
		return nil, nil, nil
	}
	cwd, err := pathValue(event.Get("cwd"))
	if err != nil {
		return nil, nil, err
	}
	base := cwd
	if value.Truth(event.Get("project_path")) {
		base, err = pathValue(event.Get("project_path"))
		if err != nil {
			return nil, nil, err
		}
	}
	root, err := projectRoot(event, directory)
	if err != nil {
		return nil, nil, err
	}
	var files, searches []string
	switch text(event.Get("tool_name")) {
	case "Read", "Edit", "Write":
		v := input.Get("file_path")
		if v == nil {
			v = value.Text("")
		}
		if v.Kind != value.String {
			return nil, nil, fail("nontext tool file hint remains unavailable")
		}
		name, err := observationFileHint(v, cwd, root, directory)
		if err != nil {
			return nil, nil, err
		}
		if name != "" {
			files = append(files, name)
		}
	case "Grep", "Glob":
		if path := input.Get("path"); path != nil && path.Kind == value.String && value.Truth(path) {
			name, err := observationFileHint(path, base, root, directory)
			if err != nil {
				return nil, nil, err
			}
			if observationIsFile(root, name) {
				files = append(files, name)
			}
		}
		if pattern := input.Get("pattern"); pattern != nil && pattern.Kind == value.String && value.Truth(pattern) {
			if value.SameText(event.Get("tool_name"), "Glob") && !strings.ContainsAny(text(pattern), "*?[") {
				name, err := observationFileHint(pattern, base, root, directory)
				if err != nil {
					return nil, nil, err
				}
				if observationIsFile(root, name) && !contains(files, name) {
					files = append(files, name)
				}
			}
			searches = observationPatternTerms(text(pattern))
		}
	case "Bash":
		files, searches, err = observationBash(input.Get("command"), base, root, directory)
	}
	return files[:min(8, len(files))], searches[:min(8, len(searches))], err
}

func observationFileHint(v *value.Value, cwd, root, directory string) (string, error) {
	path, err := pathValue(v)
	if err != nil {
		return "", err
	}
	path = strings.TrimRight(strings.Trim(path, "`\"',;:()[]"), ".")
	if path == "" || strings.Contains(path, "\n") {
		return "", nil
	}
	if !filepath.IsAbs(path) {
		// Preserve parent components until resolved follows preceding symlinks.
		path = cwd + string(filepath.Separator) + path
	}
	full, err := resolved(path, directory)
	if err != nil {
		return "", err
	}
	name, err := filepath.Rel(root, full)
	if err != nil || name == "." || name == ".." || strings.HasPrefix(name, "../") || len(name) > 512 {
		return "", nil
	}
	return filepath.ToSlash(name), nil
}

func observationIsFile(root, name string) bool {
	if name == "" {
		return false
	}
	info, err := os.Stat(filepath.Join(root, name))
	return err == nil && info.Mode().IsRegular()
}

var observationGlobExtension = regexp.MustCompile(`\*\.[A-Za-z0-9_]+`)
var observationPatternCleanup = regexp.MustCompile(`\\[bBdsSwWdD]|[^A-Za-z0-9_.-]+`)
var observationErrorIdentifier = regexp.MustCompile(`^(?:E[A-Z_]{3,40}|[A-Za-z]{3,40}(?:Error|Exception))$`)

func observationPatternTerms(pattern string) []string {
	if utf8.RuneCountInString(pattern) > 512 {
		return nil
	}
	cleaned := observationGlobExtension.ReplaceAllString(pattern, " ")
	cleaned = observationPatternCleanup.ReplaceAllString(cleaned, " ")
	var out []string
	for _, term := range terms(cleaned) {
		numeric := term != ""
		for _, r := range term {
			if r < '0' || r > '9' {
				numeric = false
				break
			}
		}
		if !numeric && len(term) <= 64 && !contains(out, term) {
			out = append(out, term)
			if len(out) == 8 {
				break
			}
		}
	}
	return out
}

func observationWord(r rune) bool { return unicode.IsLetter(r) || unicode.IsNumber(r) || r == '_' }
func observationErrors(message string) []string {
	var out []string
	// Both alternatives contain only Source word characters. Matching complete
	// Unicode-word tokens keeps the boundary inside alternative selection.
	for _, identifier := range strings.FieldsFunc(message, func(r rune) bool { return !observationWord(r) }) {
		if observationErrorIdentifier.MatchString(identifier) && !contains(out, identifier) {
			out = append(out, identifier)
			if len(out) == 8 {
				break
			}
		}
	}
	return out
}

var observationBashValueFlags = strings.Fields("-f --file -g --glob -t --type --type-not -T -A -B -C --after-context --before-context --context -m --max-count --max-depth --maxdepth -maxdepth -mindepth --iglob -E --encoding --path-separator -j --threads")
var observationBashPatternFlags = strings.Fields("-e --regexp -name -iname -path -ipath")

func observationBash(command *value.Value, cwd, root, directory string) ([]string, []string, error) {
	if command == nil || command.Kind != value.String || !value.Truth(command) || len(command.String) > 1024 {
		return nil, nil, nil
	}
	raw := strip(text(command))
	if strings.ContainsAny(raw, ";|&`$><\n\r") || !observationSearchCommand(raw) {
		return nil, nil, nil
	}
	tokens, ok := observationShellWords(raw)
	if !ok || len(tokens) == 0 {
		return nil, nil, nil
	}
	index := 1
	if len(tokens) > 1 && tokens[0] == "git" && tokens[1] == "grep" {
		index = 2
	}
	var files, searches []string
	expect := ""
	endFlags := false
	for ; index < len(tokens); index++ {
		token := tokens[index]
		if expect != "" {
			if expect == "pattern" {
				for _, term := range observationPatternTerms(token) {
					if !contains(searches, term) {
						searches = append(searches, term)
					}
				}
			}
			expect = ""
			continue
		}
		if !endFlags && token == "--" {
			endFlags = true
			continue
		}
		if !endFlags && strings.HasPrefix(token, "-") {
			switch {
			case contains(observationBashPatternFlags, token):
				expect = "pattern"
			case contains(observationBashValueFlags, token):
				expect = "skip"
			case strings.HasPrefix(token, "--regexp="), strings.HasPrefix(token, "-e"):
				pattern := token[2:]
				if _, right, found := strings.Cut(token, "="); found {
					pattern = right
				}
				for _, term := range observationPatternTerms(pattern) {
					if !contains(searches, term) {
						searches = append(searches, term)
					}
				}
			}
			continue
		}
		name, err := observationFileHint(value.Text(token), cwd, root, directory)
		if err != nil {
			return nil, nil, err
		}
		if observationIsFile(root, name) {
			if !contains(files, name) {
				files = append(files, name)
			}
			continue
		}
		for _, term := range observationPatternTerms(token) {
			if !contains(searches, term) {
				searches = append(searches, term)
			}
		}
	}
	return files, searches, nil
}

func observationSearchCommand(raw string) bool {
	for _, command := range []string{"rg", "grep", "find", "git"} {
		if !strings.HasPrefix(raw, command) {
			continue
		}
		rest := raw[len(command):]
		if command == "git" {
			trimmed := strings.TrimLeftFunc(rest, pySpace)
			if trimmed == rest || !strings.HasPrefix(trimmed, "grep") {
				continue
			}
			rest = trimmed[len("grep"):]
		}
		if rest == "" {
			return true
		}
		next, _ := utf8.DecodeRuneInString(rest)
		if !observationWord(next) {
			return true
		}
	}
	return false
}

// This bounded POSIX word reader is used only after the Source's shell-meta
// refusal. It never executes a command, expands a variable, or parses a shell.
func observationShellWords(raw string) ([]string, bool) {
	var out []string
	var token strings.Builder
	quote := rune(0)
	started := false
	points := []rune(raw)
	for i := 0; i < len(points); i++ {
		r := points[i]
		if quote == '\'' {
			if r == '\'' {
				quote = 0
			} else {
				token.WriteRune(r)
			}
			continue
		}
		if r == '\\' {
			if i+1 == len(points) {
				return nil, false
			}
			i++
			next := points[i]
			if quote == '"' && next != '"' && next != '\\' {
				token.WriteRune('\\')
			}
			token.WriteRune(next)
			started = true
			continue
		}
		if quote == '"' {
			if r == '"' {
				quote = 0
			} else {
				token.WriteRune(r)
			}
			continue
		}
		if r == '\'' || r == '"' {
			quote = r
			started = true
		} else if strings.ContainsRune(" \t\r\n", r) {
			if started {
				out = append(out, token.String())
				token.Reset()
				started = false
			}
		} else {
			token.WriteRune(r)
			started = true
		}
	}
	if quote != 0 {
		return nil, false
	}
	if started {
		out = append(out, token.String())
	}
	return out, true
}
