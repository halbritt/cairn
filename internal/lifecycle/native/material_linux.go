package native

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"reflect"
	"regexp"
	"strconv"
	"strings"

	"github.com/halbritt/cairn/internal/lifecycle/coordination"
	"github.com/halbritt/cairn/internal/lifecycle/install"
	"github.com/halbritt/cairn/internal/lifecycle/transcript"
)

type PublicationSelection struct {
	Generation           install.Generation  `json:"selection"`
	Retained             install.Generation  `json:"retained_selection"`
	RetainedMemory       Pin                 `json:"retained_memory"`
	RetainedCoordination Pin                 `json:"retained_coordination"`
	ReplaceCommands      map[string][]string `json:"replace_commands"`
}

type roleConfiguration struct {
	Harness    string `json:"harness"`
	Account    string `json:"config_home"`
	Client     string `json:"cairn"`
	Socket     string `json:"socket"`
	TokenFile  string `json:"token_file"`
	Repo       string `json:"repo"`
	Binding    string `json:"binding"`
	StateDir   string `json:"state_dir"`
	RecallMode string `json:"recall_mode"`
	Inbox      string `json:"inbox_recall_binding"`
	Native     bool   `json:"native_delivery"`
}

type hookRow struct {
	Role           string   `json:"role"`
	Event          string   `json:"event"`
	Argv           []string `json:"argv"`
	Checked        []string `json:"checked_launcher"`
	Implementation string   `json:"implementation"`
	Hook           struct {
		Type    string `json:"type"`
		Command string `json:"command"`
		Timeout int    `json:"timeout"`
	} `json:"hook"`
}

type hookRecord struct {
	Schema    string             `json:"schema"`
	Harness   string             `json:"harness"`
	Account   string             `json:"account_home"`
	Source    Pin                `json:"source_settings"`
	Roles     map[string]Pin     `json:"role_configurations"`
	Rows      []hookRow          `json:"rows"`
	Lifecycle install.Generation `json:"lifecycle_selection"`
}

type publicationMaterial struct {
	selection     PublicationSelection
	record        hookRecord
	before        *install.SettingsSnapshot
	prepared      *install.SettingsSnapshot
	pins          map[string]*install.SettingsSnapshot
	memory        roleConfiguration
	coord         roleConfiguration
	lifecycle     install.Generation
	goImage       Pin
	compatibility []coordination.CompatibilityContext
}

func readPinnedObject(ctx context.Context, pin Pin, into any) (*install.SettingsSnapshot, error) {
	snapshot, err := install.InspectSettings(ctx, pin.Path)
	if err != nil || !snapshot.Metadata().Present || snapshot.Metadata().SHA256 != pin.SHA256 {
		return nil, errors.Join(err, errors.New("selected JSON bytes differ"))
	}
	if err := strictJSON(snapshot.Bytes(), into); err != nil {
		return nil, err
	}
	return snapshot, nil
}

func (o *Owner) selectPublication(ctx context.Context, expected uint64, selection PublicationSelection) (Observation, error) {
	if o == nil {
		return Observation{}, errors.New("native owner unavailable")
	}
	o.mu.Lock()
	defer o.mu.Unlock()
	if o.origin != o || o.closed || expected == 0 || expected != o.generation || o.client != nil {
		return Observation{}, errors.New("native selection owner is busy, stale or unavailable")
	}
	if o.pendingMutation {
		return Observation{}, errors.New("native recovery is pending")
	}
	if o.exchange != nil {
		state, err := install.InspectSettingsExchange(ctx, o.exchange.Recovery())
		if err != nil || state.State != "reversed" {
			return Observation{}, errors.Join(err, errors.New("native publication must be reversed before reselection"))
		}
	}
	files, err := install.ReadGeneration(ctx, selection.Generation)
	if err != nil {
		return Observation{}, err
	}
	selected := map[string]Pin{}
	for _, file := range files {
		selected[file.Name] = Pin{file.Path, file.SHA256}
	}
	if len(selected) != 3 || selected["hook-selection.json"].Path == "" || selected["hook-settings.json"].Path == "" || selected["previous-settings.json"].Path == "" {
		return Observation{}, errors.New("native Claude selection requires the complete three-file rendered generation")
	}
	m := &publicationMaterial{selection: selection, pins: map[string]*install.SettingsSnapshot{}}
	record, err := readPinnedObject(ctx, selected["hook-selection.json"], &m.record)
	if err != nil {
		return Observation{}, err
	}
	m.pins[selected["hook-selection.json"].Path] = record
	if m.record.Schema != "cairn.lifecycle-hook-settings/1" || m.record.Harness != "claude" || m.record.Account != o.config.Account || m.record.Source.Path != filepath.Join(o.root, "settings.json") || m.record.Source.SHA256 != o.settings.Metadata().SHA256 {
		return Observation{}, errors.New("native rendered selection does not name this owner/account/preimage")
	}
	var object map[string]any
	m.before, err = readPinnedObject(ctx, selected["previous-settings.json"], &object)
	if err != nil || !bytes.Equal(m.before.Bytes(), o.settings.Bytes()) {
		return Observation{}, errors.Join(err, errors.New("rendered preimage differs from owned settings"))
	}
	m.prepared, err = readPinnedObject(ctx, selected["hook-settings.json"], &object)
	if err != nil {
		return Observation{}, err
	}
	if len(m.record.Roles) != 2 || m.record.Roles["memory"].Path == "" || m.record.Roles["coordination"].Path == "" {
		return Observation{}, errors.New("Claude slice requires both bound memory and coordination profiles")
	}
	for role, pin := range m.record.Roles {
		var config roleConfiguration
		snapshot, err := readPinnedObject(ctx, pin, &config)
		if err != nil {
			return Observation{}, err
		}
		m.pins[pin.Path] = snapshot
		if role == "memory" {
			m.memory = config
		} else {
			m.coord = config
		}
	}
	for role, pair := range map[string]struct {
		pin     Pin
		forward roleConfiguration
	}{"memory": {selection.RetainedMemory, m.memory}, "coordination": {selection.RetainedCoordination, m.coord}} {
		var old roleConfiguration
		snapshot, err := readPinnedObject(ctx, pair.pin, &old)
		if err != nil {
			return Observation{}, err
		}
		m.pins[pair.pin.Path] = snapshot
		next := pair.forward
		if old.Harness != "claude" || next.Harness != "claude" || old.Inbox == "" || next.Inbox == "" ||
			old.Client != next.Client || old.Socket != next.Socket || old.TokenFile != next.TokenFile || old.Repo != next.Repo ||
			old.StateDir != next.StateDir || old.Binding != next.Binding || old.Account != next.Account ||
			role == "memory" && (old.RecallMode != "agent_tools" || next.RecallMode != "agent_tools") {
			return Observation{}, errors.New("retained/forward profile identities or durable state differ")
		}
		if role == "coordination" && (!old.Native || !next.Native || old.Account != o.config.Account || old.Binding == "") {
			return Observation{}, errors.New("selected native coordination profile/account is unsupported")
		}
		var activation *bool
		for version, path := range map[string]string{"2": old.Inbox, "3": next.Inbox} {
			snapshot, err := install.InspectSettings(ctx, path)
			if err != nil || !snapshot.Metadata().Present {
				return Observation{}, errors.Join(err, errors.New("bound profile visibility is unknown"))
			}
			var binding struct {
				Schema  string `json:"schema"`
				Enabled *bool  `json:"enabled"`
			}
			if err := strictJSON(snapshot.Bytes(), &binding); err != nil || binding.Schema != "cairn.inbox-recall-binding/"+version || binding.Enabled == nil {
				return Observation{}, errors.Join(err, errors.New("native binding schema or activation is unknown"))
			}
			if activation != nil && *activation != *binding.Enabled {
				return Observation{}, errors.New("native publication cannot silently change binding activation")
			}
			activation = binding.Enabled
			m.pins[path] = snapshot
		}
	}
	if err := m.checkRetained(ctx); err != nil {
		return Observation{}, err
	}
	if m.memory.Client != m.coord.Client || m.memory.Socket != m.coord.Socket || m.memory.TokenFile != m.coord.TokenFile || m.memory.Repo != m.coord.Repo || m.memory.StateDir == m.coord.StateDir {
		return Observation{}, errors.New("selected memory/coordination route or state domains differ")
	}
	for _, row := range m.record.Rows {
		if row.Role != "memory" && row.Role != "coordination" || row.Implementation != "go" || len(row.Checked) != 1 || len(row.Argv) == 0 || row.Argv[0] != row.Checked[0] || row.Hook.Type != "command" || row.Hook.Command != shellJoin(row.Argv) || row.Hook.Timeout < 1 || row.Hook.Timeout > 150 {
			return Observation{}, errors.New("native selection contains an unsupported or mismatched Go hook row")
		}
		launcher, err := install.InspectSettings(ctx, row.Checked[0])
		if err != nil || !launcher.Metadata().Present || !bytes.HasPrefix(launcher.Bytes(), []byte("#!/")) || !bytes.Contains(launcher.Bytes(), []byte(" --receipt ")) {
			return Observation{}, errors.Join(err, errors.New("checked native launcher visibility is unknown"))
		}
		m.pins[row.Checked[0]] = launcher
	}
	if len(m.record.Rows) == 0 {
		return Observation{}, errors.New("native selection has no checked rows")
	}
	if err := m.checkLaunchers(ctx); err != nil {
		return Observation{}, err
	}
	for i := range m.compatibility {
		m.compatibility[i].Profile = transcript.NewCoordinationProfile(o.config.Work, os.Getenv, os.LookupEnv)
	}
	if err := validateSettingsMutation(o.settings.Bytes(), m.prepared.Bytes(), m.record.Rows, selection.ReplaceCommands); err != nil {
		return Observation{}, err
	}
	if err := m.recheck(ctx); err != nil {
		return Observation{}, err
	}
	o.material = m
	o.exchange = nil
	o.generation++
	return o.inspectLocked(ctx), nil
}

func (m *publicationMaterial) recheck(ctx context.Context) error {
	if _, err := coordination.InspectRetainedPackage(ctx, m.selection.Retained); err != nil {
		return err
	}
	for _, selected := range []install.Generation{m.selection.Generation, m.lifecycle} {
		if _, err := install.ReadGeneration(ctx, selected); err != nil {
			return err
		}
	}
	if err := checkPinnedImage(ctx, m.goImage); err != nil {
		return err
	}
	for _, selected := range m.compatibility {
		// Fresh source reader instances check the complete receipt, closure and
		// bound configurations. No cached validation survives a G2 pass.
		if err := selected.VerifyReceipt(nil); err != nil {
			return err
		}
		if _, err := selected.Config(nil, selected.ConfigPath); err != nil {
			return err
		}
		if err := selected.CheckedBinding(nil); err != nil {
			return err
		}
	}
	for _, snapshot := range m.pins {
		if err := snapshot.Recheck(ctx); err != nil {
			return err
		}
	}
	return errors.Join(m.before.Recheck(ctx), m.prepared.Recheck(ctx))
}

var shellWord = regexp.MustCompile(`^[a-zA-Z0-9_@%+=:,./-]+$`)

func shellJoin(words []string) string {
	quoted := make([]string, len(words))
	for i, word := range words {
		if shellWord.MatchString(word) {
			quoted[i] = word
		} else {
			quoted[i] = "'" + strings.ReplaceAll(word, "'", "'\"'\"'") + "'"
		}
	}
	return strings.Join(quoted, " ")
}

func validateSettingsMutation(before, after []byte, rows []hookRow, replacements map[string][]string) error {
	if len(replacements) != 2 || replacements["memory"] == nil || replacements["coordination"] == nil {
		return errors.New("explicit old commands for both selected roles required")
	}
	var expected, prepared map[string]any
	if err := errors.Join(strictJSON(before, &expected), strictJSON(after, &prepared)); err != nil {
		return err
	}
	if expected == nil || prepared == nil {
		return errors.New("native settings must be objects")
	}
	if expected == nil || prepared == nil {
		return errors.New("native settings must be JSON objects")
	}
	hooks, ok := expected["hooks"].(map[string]any)
	if !ok {
		if _, present := expected["hooks"]; present {
			return errors.New("original native hook object is unsupported")
		}
		hooks = map[string]any{}
		expected["hooks"] = hooks
	}
	seen := map[string]bool{}
	for _, commands := range replacements {
		for _, command := range commands {
			if command == "" || seen[command] {
				return errors.New("native old commands are empty or duplicated")
			}
			seen[command] = true
		}
	}
	for _, row := range rows {
		var groups []any
		if value, present := hooks[row.Event]; present {
			var ok bool
			groups, ok = value.([]any)
			if !ok {
				return errors.New("native event groups are unsupported")
			}
		}
		commands := map[string]bool{row.Hook.Command: true}
		for _, command := range replacements[row.Role] {
			commands[command] = true
		}
		var selected map[string]any
		selectedIndex, matches := 0, 0
		for _, entry := range groups {
			group, ok := entry.(map[string]any)
			if !ok {
				return errors.New("native hook group is unsupported")
			}
			entries, ok := group["hooks"].([]any)
			if !ok {
				return errors.New("native hook entries are unsupported")
			}
			for index, entry := range entries {
				hook, ok := entry.(map[string]any)
				if !ok {
					return errors.New("native hook entry is unsupported")
				}
				command, _ := hook["command"].(string)
				if commands[command] {
					selected, selectedIndex, matches = group, index, matches+1
				}
			}
		}
		if matches > 1 {
			return errors.New("native selected command is ambiguous")
		}
		newHook := map[string]any{"type": row.Hook.Type, "command": row.Hook.Command, "timeout": json.Number(strconv.Itoa(row.Hook.Timeout))}
		if selected != nil {
			entries := selected["hooks"].([]any)
			entries[selectedIndex] = newHook
		} else {
			groups = append(groups, map[string]any{"matcher": "", "hooks": []any{newHook}})
		}
		hooks[row.Event] = groups
	}
	if !reflect.DeepEqual(expected, prepared) {
		return errors.New("native rendered selection changes nonselected hooks/settings or omits selected rows")
	}
	return nil
}
