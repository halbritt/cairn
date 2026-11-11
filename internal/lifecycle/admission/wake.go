package admission

import "github.com/halbritt/cairn/internal/lifecycle/value"

// CoordinatesWake is the memory consumer's pure bridge relation over checked
// coordinator snapshots. It neither claims an inbox nor delivers its context.
func CoordinatesWake(in Invocation, coordinators []*value.Value) (bool, error) {
	activeFor := func(harness *value.Value) ([]rune, error) {
		home, err := in.Home()
		if err != nil {
			return nil, err
		}
		variable, suffix := "CODEX_HOME", ".codex"
		if stringIs(harness, "claude") {
			variable, suffix = "CLAUDE_CONFIG_DIR", ".claude"
		}
		active := joinPath(home, []rune(suffix))
		if configured, present := in.Environment[variable]; present {
			active = makePath(configured)
		}
		return in.resolve(pathText(active))
	}
	active, err := activeFor(in.Config.Get("harness"))
	if err != nil {
		return false, err
	}
	matches := []*value.Value{}
	for _, coordinator := range coordinators {
		home := coordinator.Get("config_home")
		if !truth(home) {
			matches = append(matches, coordinator)
			continue
		}
		selected, err := in.resolve(home)
		if err != nil {
			return false, err
		}
		if equal(active, selected) {
			matches = append(matches, coordinator)
		}
	}
	if len(matches) > 1 {
		return false, sourceError("ValueError", "ambiguous coordination profile")
	}
	in.Kind = "inbox_recall"
	reason, err := disabled(in, false)
	if err != nil || len(matches) == 0 || reason != "" {
		return false, err
	}
	roots, err := privacyRoots(in, false)
	if err != nil {
		return false, err
	}
	for _, root := range roots {
		for _, directory := range ancestry(root) {
			present, err := in.exists(joinPath(directory, []rune(".cairn-no-coordination")))
			if err != nil || present {
				return false, err
			}
		}
	}
	selected := matches[0]
	if home := selected.Get("config_home"); truth(home) && oneOf(selected.Get("harness"), "codex", "claude") {
		left, err := activeFor(selected.Get("harness"))
		if err != nil {
			return false, err
		}
		right, err := in.resolve(home)
		if err != nil {
			return false, err
		}
		return equal(left, right), nil
	}
	return true, nil
}
