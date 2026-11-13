package install

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"reflect"
	"sort"
	"strings"

	"github.com/halbritt/cairn/internal/jsontext"
)

type agyJSONMember struct {
	raw        json.RawMessage
	start, end int
}

func agyStrictJSON(raw []byte) error {
	if err := jsontext.CheckUnicode(raw); err != nil {
		return err
	}
	d := json.NewDecoder(bytes.NewReader(raw))
	d.UseNumber()
	var visit func(int) error
	visit = func(depth int) error {
		if depth > 100 {
			return errors.New("Agy settings nesting exceeds 100")
		}
		token, err := d.Token()
		if err != nil {
			return err
		}
		switch token {
		case json.Delim('{'):
			seen := map[string]bool{}
			for d.More() {
				key, err := d.Token()
				if err != nil {
					return err
				}
				name, ok := key.(string)
				if !ok || seen[name] {
					return errors.New("duplicate or invalid Agy settings key")
				}
				seen[name] = true
				if err := visit(depth + 1); err != nil {
					return err
				}
			}
			end, err := d.Token()
			if err != nil || end != json.Delim('}') {
				return errors.Join(err, errors.New("invalid Agy settings object"))
			}
		case json.Delim('['):
			for d.More() {
				if err := visit(depth + 1); err != nil {
					return err
				}
			}
			end, err := d.Token()
			if err != nil || end != json.Delim(']') {
				return errors.Join(err, errors.New("invalid Agy settings array"))
			}
		case json.Delim('}'), json.Delim(']'):
			return errors.New("unexpected Agy settings delimiter")
		}
		return nil
	}
	if err := visit(0); err != nil {
		return err
	}
	if _, err := d.Token(); err != io.EOF {
		return errors.New("Agy settings contain trailing input")
	}
	return nil
}

func agyObject(raw []byte) (map[string]agyJSONMember, int, error) {
	if err := agyStrictJSON(raw); err != nil {
		return nil, 0, err
	}
	d := json.NewDecoder(bytes.NewReader(raw))
	if first, err := d.Token(); err != nil || first != json.Delim('{') {
		return nil, 0, errors.New("Agy settings/group must be an object")
	}
	members := map[string]agyJSONMember{}
	for d.More() {
		key, err := d.Token()
		if err != nil {
			return nil, 0, err
		}
		var member json.RawMessage
		if err := d.Decode(&member); err != nil {
			return nil, 0, err
		}
		end := int(d.InputOffset())
		members[key.(string)] = agyJSONMember{raw: member, start: end - len(member), end: end}
	}
	if _, err := d.Token(); err != nil {
		return nil, 0, err
	}
	return members, int(d.InputOffset()) - 1, nil
}

func agyDispatch(raw []byte) (bool, error) {
	if err := agyStrictJSON(raw); err != nil {
		return false, err
	}
	var object map[string]json.RawMessage
	if err := json.Unmarshal(raw, &object); err != nil || object == nil {
		return false, errors.New("Agy settings must be an object")
	}
	if group, ok := object["cairn-coordination"]; ok {
		var events map[string]json.RawMessage
		if err := json.Unmarshal(group, &events); err != nil || events == nil {
			return false, errors.New("Agy Cairn group visibility is unknown")
		}
		for _, event := range events {
			var hooks []json.RawMessage
			if err := json.Unmarshal(event, &hooks); err != nil || bytes.Equal(bytes.TrimSpace(event), []byte("null")) {
				return false, errors.New("Agy Cairn event visibility is unknown")
			}
			if len(hooks) != 0 {
				return true, nil
			}
		}
	}
	var walk func(json.RawMessage) (bool, error)
	walk = func(body json.RawMessage) (bool, error) {
		var fields map[string]json.RawMessage
		if json.Unmarshal(body, &fields) == nil && fields != nil {
			for key, field := range fields {
				if key == "command" {
					var command string
					if err := json.Unmarshal(field, &command); err != nil || strings.TrimSpace(command) == "" {
						return false, errors.New("Agy hook command visibility is unknown")
					}
					if agyCairnCommand(command) {
						return true, nil
					}
				}
				if found, err := walk(field); found || err != nil {
					return found, err
				}
			}
		}
		var items []json.RawMessage
		if json.Unmarshal(body, &items) == nil {
			for _, item := range items {
				if found, err := walk(item); found || err != nil {
					return found, err
				}
			}
		}
		return false, nil
	}
	return walk(raw)
}

func agyCairnCommand(command string) bool {
	text := strings.ToLower(command)
	return strings.Contains(text, "cairn") || strings.Contains(text, "coordination.py")
}

func agyHookMembers(raw []byte) ([]agyJSONMember, error) {
	d := json.NewDecoder(bytes.NewReader(raw))
	if token, err := d.Token(); err != nil || token != json.Delim('[') {
		return nil, errors.New("Agy event hooks must be an array")
	}
	var hooks []agyJSONMember
	for d.More() {
		var hook json.RawMessage
		if err := d.Decode(&hook); err != nil {
			return nil, err
		}
		var entry struct{ Command string }
		if err := json.Unmarshal(hook, &entry); err != nil || entry.Command == "" {
			return nil, errors.New("Agy hook command visibility is unknown")
		}
		end := int(d.InputOffset())
		hooks = append(hooks, agyJSONMember{raw: hook, start: end - len(hook), end: end})
	}
	return hooks, nil
}

func agySameJSON(a, b []byte) bool {
	decode := func(raw []byte) (any, error) {
		d := json.NewDecoder(bytes.NewReader(raw))
		d.UseNumber()
		var value any
		err := d.Decode(&value)
		return value, err
	}
	x, xe := decode(a)
	y, ye := decode(b)
	return xe == nil && ye == nil && reflect.DeepEqual(x, y)
}

func agyMergeSettings(old, prepared []byte, replaceCommands []string) ([]byte, error) {
	removable := map[string]bool{}
	for _, command := range replaceCommands {
		if strings.TrimSpace(command) == "" {
			return nil, errors.New("Agy replacement commands require nonempty exact strings")
		}
		removable[command] = true
	}
	want, _, err := agyObject(prepared)
	if err != nil {
		return nil, err
	}
	group, ok := want["cairn-coordination"]
	if !ok {
		return nil, errors.New("prepared Agy settings require cairn-coordination")
	}
	newEvents, _, err := agyObject(group.raw)
	if err != nil || len(newEvents) == 0 {
		return nil, errors.Join(err, errors.New("prepared Agy group requires command events"))
	}
	if len(old) == 0 {
		old = []byte("{}\n")
	}
	current, closing, err := agyObject(old)
	if err != nil {
		return nil, err
	}
	// Substitute the original unrelated hook bytes into the prepared group.
	// Formatting by the bridge cannot rewrite an unrelated hook or big integer.
	var edits []agyJSONMember
	if prior, exists := current["cairn-coordination"]; exists {
		oldEvents, _, err := agyObject(prior.raw)
		if err != nil {
			return nil, err
		}
		for event, oldValue := range oldEvents {
			oldHooks, err := agyHookMembers(oldValue.raw)
			if err != nil {
				return nil, err
			}
			replacement, exists := newEvents[event]
			var newHooks []agyJSONMember
			if exists {
				newHooks, err = agyHookMembers(replacement.raw)
				if err != nil {
					return nil, err
				}
			}
			next := 0
			for _, hook := range oldHooks {
				var entry struct{ Command string }
				if err := json.Unmarshal(hook.raw, &entry); err != nil {
					return nil, err
				}
				if removable[entry.Command] {
					continue
				}
				found := false
				for i := next; i < len(newHooks); i++ {
					candidate := newHooks[i]
					if agySameJSON(hook.raw, candidate.raw) {
						edits = append(edits, agyJSONMember{raw: hook.raw, start: replacement.start + candidate.start, end: replacement.start + candidate.end})
						next, found = i+1, true
						break
					}
				}
				if !found {
					return nil, fmt.Errorf("prepared Agy event %s changes an unselected hook; exact replace_commands intent required", event)
				}
			}
		}
	}
	for event, member := range newEvents {
		var hooks []map[string]json.RawMessage
		if err := json.Unmarshal(member.raw, &hooks); err != nil || len(hooks) == 0 {
			return nil, fmt.Errorf("prepared Agy event %s requires hook objects", event)
		}
		for _, hook := range hooks {
			var kind, command string
			if err := json.Unmarshal(hook["type"], &kind); err != nil || kind != "command" {
				return nil, errors.New("prepared Agy hook requires command type")
			}
			if err := json.Unmarshal(hook["command"], &command); err != nil || strings.TrimSpace(command) == "" {
				return nil, errors.New("prepared Agy hook requires command text")
			}
		}
	}
	sort.Slice(edits, func(i, j int) bool { return edits[i].start > edits[j].start })
	groupBytes := append([]byte(nil), group.raw...)
	for _, edit := range edits {
		groupBytes = append(append(append([]byte(nil), groupBytes[:edit.start]...), edit.raw...), groupBytes[edit.end:]...)
	}
	var result []byte
	if existing, exists := current["cairn-coordination"]; exists {
		result = append(result, old[:existing.start]...)
		result = append(result, groupBytes...)
		result = append(result, old[existing.end:]...)
	} else {
		result = append(result, old[:closing]...)
		if len(current) != 0 {
			result = append(result, ',')
		}
		result = append(result, []byte(`"cairn-coordination":`)...)
		result = append(result, groupBytes...)
		result = append(result, old[closing:]...)
	}
	return result, agyStrictJSON(result)
}
