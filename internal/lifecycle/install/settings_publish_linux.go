package install

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"
	"unicode/utf8"

	"golang.org/x/sys/unix"
)

// HookSettingsPublishPlan selects a verified hook generation and live target.
// Publication merges Cairn rows onto the current target bytes and exchanges via
// RENAME_EXCHANGE, preserving intervening foreign edits and refusing mid-write drift.
type HookSettingsPublishPlan struct {
	Target          string
	Role            string
	Selection       Generation
	ReplaceCommands map[string][]string
}

type HookSettingsPublishResult struct {
	Changed  bool                    `json:"changed"`
	Target   SettingsMetadata        `json:"target"`
	Exchange *SettingsExchangeResult `json:"exchange,omitempty"`
}

type hookSelectionRecord struct {
	Schema  string            `json:"schema"`
	Harness string            `json:"harness"`
	Layout  map[string]string `json:"layout"`
	Rows    []hookSelectedRow `json:"rows"`
}

type hookSelectedRow struct {
	Role            string          `json:"role"`
	Event           string          `json:"event"`
	Argv            []string        `json:"argv"`
	CheckedLauncher []string        `json:"checked_launcher"`
	Retained        []string        `json:"retained_launcher"`
	Hook            json.RawMessage `json:"hook"`
}

func PublishHookSettings(ctx context.Context, plan HookSettingsPublishPlan) (HookSettingsPublishResult, error) {
	return publishHookSettings(ctx, plan, nil)
}

func publishHookSettings(ctx context.Context, plan HookSettingsPublishPlan, observe func(string) error) (out HookSettingsPublishResult, err error) {
	if !canonical(plan.Target) || !utf8.ValidString(plan.Target) || os.Getuid() != os.Geteuid() {
		return out, errors.New("settings publication requires a canonical UTF-8 target path and unchanged UID")
	}
	if err := validateReplaceMap(plan.ReplaceCommands); err != nil {
		return out, err
	}
	files, err := ReadGeneration(ctx, plan.Selection)
	if err != nil {
		return out, err
	}
	members := map[string]File{}
	for _, f := range files {
		members[f.Name] = f
	}
	selFile, ok := members["hook-selection.json"]
	if !ok {
		return out, errors.New("hook generation has no hook-selection.json")
	}
	selRaw, err := readVerifiedGenerationMember(ctx, selFile)
	if err != nil {
		return out, err
	}
	var record hookSelectionRecord
	if err := agyStrictJSON(selRaw); err != nil {
		return out, err
	}
	if err := json.Unmarshal(selRaw, &record); err != nil || record.Schema != "cairn.lifecycle-hook-settings/1" {
		return out, errors.Join(err, errors.New("unsupported hook-selection.json schema"))
	}
	expectedName, err := expectedSettingsBasename(record, plan.Role, plan.ReplaceCommands)
	if err != nil {
		return out, err
	}
	if filepath.Base(plan.Target) != expectedName {
		return out, fmt.Errorf("target basename %s differs from staged layout %s", filepath.Base(plan.Target), expectedName)
	}
	current, err := InspectSettings(ctx, plan.Target)
	if err != nil {
		return out, err
	}
	merged, err := mergeSelectedHookSettings(ctx, record, members, plan.Role, plan.ReplaceCommands, current)
	if err != nil {
		return out, err
	}
	if current.Metadata().Present && bytes.Equal(merged, current.Bytes()) {
		if err := current.Recheck(ctx); err != nil {
			return out, err
		}
		return HookSettingsPublishResult{Changed: false, Target: current.Metadata()}, nil
	}
	exchange, err := PrepareSettingsExchange(ctx, current, merged)
	if err != nil {
		return out, err
	}
	if observe != nil {
		if err := observe("before:publish"); err != nil {
			return out, err
		}
	}
	res, err := exchange.Publish(ctx, func(ctx context.Context) error { return ctx.Err() })
	if err != nil {
		return out, err
	}
	return HookSettingsPublishResult{Changed: true, Target: res.Target, Exchange: &res}, nil
}

func readVerifiedGenerationMember(ctx context.Context, member File) ([]byte, error) {
	parent, err := openDirectory(filepath.Dir(member.Path), true)
	if err != nil {
		return nil, err
	}
	defer parent.Close()
	fd, err := unix.Openat(int(parent.Fd()), member.Name, unix.O_RDONLY|unix.O_NONBLOCK|unix.O_NOFOLLOW|unix.O_CLOEXEC, 0)
	if err != nil {
		return nil, err
	}
	f := os.NewFile(uintptr(fd), member.Path)
	defer f.Close()
	mode := uint32(0400)
	if member.Executable {
		mode = 0500
	}
	before, err := generationStat(f, mode)
	if err != nil || before.Size > agySettingsLimit {
		return nil, errors.Join(err, errors.New("invalid hook generation member"))
	}
	raw, readErr := io.ReadAll(io.LimitReader(contextReader{ctx, f}, agySettingsLimit+1))
	after, statErr := generationStat(f, mode)
	if readErr != nil || statErr != nil || *before != *after || int64(len(raw)) != before.Size || agyDigest(raw) != member.SHA256 {
		return nil, errors.Join(readErr, statErr, errors.New("hook generation member changed"))
	}
	return raw, nil
}

func validateReplaceMap(replacements map[string][]string) error {
	if len(replacements) != 2 || replacements["memory"] == nil || replacements["coordination"] == nil {
		return errors.New("replace_commands requires explicit memory and coordination arrays")
	}
	seen := map[string]bool{}
	for _, role := range []string{"memory", "coordination"} {
		for _, cmd := range replacements[role] {
			if cmd == "" || seen[cmd] {
				return errors.New("previous commands must be distinct nonempty strings")
			}
			seen[cmd] = true
		}
	}
	return nil
}

func expectedSettingsBasename(record hookSelectionRecord, role string, replacements map[string][]string) (string, error) {
	switch record.Harness {
	case "opencode":
		if len(replacements["memory"]) != 0 || len(replacements["coordination"]) != 0 {
			return "", errors.New("OpenCode settings publication takes empty replace_commands")
		}
		switch role {
		case "memory":
			return "cairn-lifecycle.json", nil
		case "coordination":
			return "cairn-coordination.json", nil
		default:
			return "", errors.New("OpenCode settings publication requires --role memory|coordination")
		}
	case "claude", "codex", "agy":
		if role != "" {
			return "", errors.New("--role is only supported for OpenCode plugin configurations")
		}
		if record.Harness == "agy" && len(replacements["memory"]) != 0 {
			return "", errors.New("Agy has no ordinary memory hook to replace")
		}
		name := record.Layout["hook-settings.json"]
		if name == "" {
			return "", errors.New("hook-selection.json has no hook-settings.json layout")
		}
		return name, nil
	default:
		return "", fmt.Errorf("unsupported hook-selection harness %q", record.Harness)
	}
}

func mergeSelectedHookSettings(ctx context.Context, record hookSelectionRecord, members map[string]File, role string, replacements map[string][]string, current *SettingsSnapshot) ([]byte, error) {
	base := []byte("{}")
	if current.Metadata().Present {
		base = current.Bytes()
	}
	switch record.Harness {
	case "agy":
		preparedFile, ok := members["hook-settings.json"]
		if !ok {
			return nil, errors.New("Agy hook generation has no hook-settings.json")
		}
		prepared, err := readVerifiedGenerationMember(ctx, preparedFile)
		if err != nil {
			return nil, err
		}
		return agyMergeSettings(base, prepared, replacements["coordination"])
	case "opencode":
		commands := map[string][]string{}
		for _, row := range record.Rows {
			if row.Role == role {
				commands[row.Event] = row.Argv
			}
		}
		if len(commands) == 0 {
			return nil, fmt.Errorf("no staged OpenCode commands for role %s", role)
		}
		rawCmds, err := json.Marshal(commands)
		if err != nil {
			return nil, err
		}
		return patchObjectMember(base, "commands", rawCmds)
	case "claude", "codex":
		return mergeShellHookSettings(base, record.Rows, replacements)
	default:
		return nil, fmt.Errorf("unsupported harness %q", record.Harness)
	}
}

func patchObjectMember(original []byte, field string, valueRaw []byte) ([]byte, error) {
	old, closePos, err := agyObject(original)
	if err != nil {
		return nil, err
	}
	if prior, ok := old[field]; ok {
		raw := append(bytes.Clone(original[:prior.start]), valueRaw...)
		return append(raw, original[prior.end:]...), nil
	}
	raw := bytes.Clone(original[:closePos])
	if len(old) != 0 {
		raw = append(raw, ',')
	}
	keyJSON, err := json.Marshal(field)
	if err != nil {
		return nil, err
	}
	raw = append(raw, keyJSON...)
	raw = append(raw, ':')
	raw = append(raw, valueRaw...)
	return append(raw, original[closePos:]...), nil
}

func shellQuoteWord(s string) string {
	if s == "" {
		return "''"
	}
	safe := true
	for _, r := range s {
		if !(r >= 'a' && r <= 'z' || r >= 'A' && r <= 'Z' || r >= '0' && r <= '9' || strings.ContainsRune("@%+=:,./-_", r)) {
			safe = false
			break
		}
	}
	if safe {
		return s
	}
	return "'" + strings.ReplaceAll(s, "'", "'\"'\"'") + "'"
}

func shellJoin(argv []string) string {
	parts := make([]string, len(argv))
	for i, a := range argv {
		parts[i] = shellQuoteWord(a)
	}
	return strings.Join(parts, " ")
}

func mergeShellHookSettings(base []byte, rows []hookSelectedRow, replacements map[string][]string) ([]byte, error) {
	selectedRoles := map[string]string{}
	rowCmds := make([]string, len(rows))
	for i, row := range rows {
		var hookObj map[string]json.RawMessage
		var cmd string
		if err := json.Unmarshal(row.Hook, &hookObj); err != nil || json.Unmarshal(hookObj["command"], &cmd) != nil || cmd == "" {
			return nil, errors.New("staged hook row has no command")
		}
		rowCmds[i] = cmd
		for _, c := range []string{cmd, shellJoin(row.CheckedLauncher), shellJoin(row.Retained)} {
			if c != "" {
				selectedRoles[c] = row.Role
			}
		}
	}
	for role, cmds := range replacements {
		for _, c := range cmds {
			if owner, ok := selectedRoles[c]; ok && owner != role {
				return nil, errors.New("previous command selects another current role")
			}
		}
	}
	top, _, err := agyObject(base)
	if err != nil {
		return nil, err
	}
	events := map[string]json.RawMessage{}
	if hooksMember, ok := top["hooks"]; ok {
		if _, _, err := agyObject(hooksMember.raw); err != nil {
			return nil, errors.New("hook groups must be an object")
		}
		if err := json.Unmarshal(hooksMember.raw, &events); err != nil || events == nil {
			return nil, errors.New("hook groups must be an object")
		}
	}
	for i, row := range rows {
		var rawGroups []json.RawMessage
		if rawEvent, exists := events[row.Event]; exists {
			if bytes.Equal(bytes.TrimSpace(rawEvent), []byte("null")) || json.Unmarshal(rawEvent, &rawGroups) != nil {
				return nil, errors.New("event hook groups must be a list")
			}
		}
		allowed := map[string]bool{rowCmds[i]: true}
		for _, c := range replacements[row.Role] {
			allowed[c] = true
		}
		type matchPos struct{ group, offset int }
		var matches []matchPos
		parsedGroups := make([]map[string]json.RawMessage, len(rawGroups))
		parsedHooks := make([][]json.RawMessage, len(rawGroups))
		for gIdx, rawGroup := range rawGroups {
			if _, _, err := agyObject(rawGroup); err != nil {
				return nil, errors.New("unsupported hook group shape")
			}
			var grp map[string]json.RawMessage
			if err := json.Unmarshal(rawGroup, &grp); err != nil || grp["hooks"] == nil {
				return nil, errors.New("unsupported hook group shape")
			}
			var hookList []json.RawMessage
			if err := json.Unmarshal(grp["hooks"], &hookList); err != nil || bytes.Equal(bytes.TrimSpace(grp["hooks"]), []byte("null")) {
				return nil, errors.New("unsupported hook group shape")
			}
			for hIdx, rawHook := range hookList {
				if _, _, err := agyObject(rawHook); err != nil {
					return nil, errors.New("unsupported hook definition")
				}
				var h map[string]json.RawMessage
				_ = json.Unmarshal(rawHook, &h)
				var cmd string
				if h["command"] != nil && json.Unmarshal(h["command"], &cmd) == nil && allowed[cmd] {
					matches = append(matches, matchPos{gIdx, hIdx})
				}
			}
			parsedGroups[gIdx] = grp
			parsedHooks[gIdx] = hookList
		}
		if len(matches) > 1 {
			return nil, errors.New("ambiguous previous hook definitions; select an unambiguous input")
		}
		if len(matches) == 1 {
			m := matches[0]
			parsedHooks[m.group][m.offset] = row.Hook
			updatedHooks, err := json.Marshal(parsedHooks[m.group])
			if err != nil {
				return nil, err
			}
			updatedGroup, err := patchObjectMember(rawGroups[m.group], "hooks", updatedHooks)
			if err != nil {
				return nil, err
			}
			rawGroups[m.group] = updatedGroup
		} else {
			newGroup, err := json.Marshal(map[string]any{"hooks": []json.RawMessage{row.Hook}})
			if err != nil {
				return nil, err
			}
			rawGroups = append(rawGroups, newGroup)
		}
		encodedGroups, err := json.Marshal(rawGroups)
		if err != nil {
			return nil, err
		}
		events[row.Event] = encodedGroups
	}
	hooksRaw, err := json.Marshal(events)
	if err != nil {
		return nil, err
	}
	prepared, err := json.Marshal(map[string]json.RawMessage{"hooks": hooksRaw})
	if err != nil {
		return nil, err
	}
	return PatchSettingsHooks(base, prepared)
}

// ReversePublishedSettings restores a published target from its pinned recovery
// record, stopping without mutation if any post-publication edit occurred.
func ReversePublishedSettings(ctx context.Context, selected SettingsRecovery) (result SettingsExchangeResult, err error) {
	result, target, candidate, err := inspectSettingsExchange(ctx, selected)
	if err != nil {
		return result, err
	}
	if result.State == "reversed" {
		return result, nil
	}
	if result.State != "published" {
		return result, errors.New("settings exchange is not in published state")
	}
	var data settingsExchangeRecord
	record, err := InspectSettings(ctx, selected.Path)
	if err != nil {
		return result, err
	}
	if err := json.Unmarshal(record.Bytes(), &data); err != nil {
		return result, err
	}
	parent, err := openDirectory(filepath.Dir(data.Target), false)
	if err != nil {
		return result, err
	}
	defer func() { err = errors.Join(err, parent.Close()) }()
	if err := errors.Join(target.Recheck(ctx), candidate.Recheck(ctx), record.Recheck(ctx), ctx.Err()); err != nil {
		return result, err
	}
	if data.Original.Present {
		if err := unix.Renameat2(int(parent.Fd()), filepath.Base(data.Candidate), int(parent.Fd()), filepath.Base(data.Target), unix.RENAME_EXCHANGE); err != nil {
			return result, err
		}
		result.State, result.Committed, result.Target = "inverse-unverified", true, SettingsMetadata{}
		restored, err := InspectSettings(ctx, data.Target)
		if err != nil || !matchingSettings(restored, data.Original) {
			return result, errors.Join(err, errors.New("settings reverse committed but preimage readback differs; retain recovery"))
		}
		candAfter, err := InspectSettings(ctx, data.Candidate)
		if err != nil || !matchingSettings(candAfter, data.Prepared) {
			return result, errors.Join(err, errors.New("settings reverse candidate drift; retain recovery"))
		}
		if err := candAfter.Recheck(ctx); err != nil {
			return result, err
		}
		if err := unix.Unlinkat(int(parent.Fd()), filepath.Base(data.Candidate), 0); err != nil {
			return result, err
		}
	} else {
		if err := unix.Unlinkat(int(parent.Fd()), filepath.Base(data.Target), 0); err != nil {
			return result, err
		}
		result.State, result.Committed, result.Target = "inverse-unverified", true, SettingsMetadata{}
	}
	if err := parent.Sync(); err != nil {
		return result, err
	}
	return InspectSettingsExchange(ctx, selected)
}
