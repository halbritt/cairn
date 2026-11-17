package native

import (
	"encoding/json"
	"errors"
	"reflect"
)

func checkNativeSystem(event map[string]json.RawMessage) error {
	var subtype string
	if raw := event["subtype"]; raw != nil {
		if err := strictJSON(raw, &subtype); err != nil {
			return err
		}
	}
	if subtype != "hook_response" {
		return nil
	}
	var code int
	var outcome string
	if err := errors.Join(strictJSON(event["exit_code"], &code), strictJSON(event["outcome"], &outcome)); err != nil {
		return err
	}
	if code != 0 || outcome != "success" {
		return errors.New("actual native hook execution refused or failed")
	}
	return nil
}

func verifyNativeReadback(raw []byte, settings, listing map[string]json.RawMessage) error {
	wanted, err := ownedCommandRows(raw)
	if err != nil {
		return err
	}
	var expected, effective struct {
		Hooks map[string]any `json:"hooks"`
	}
	if err := errors.Join(strictJSON(raw, &expected), strictJSON(settings["effective"], &effective)); err != nil {
		return err
	}
	if len(expected.Hooks) != 0 || len(effective.Hooks) != 0 {
		if !reflect.DeepEqual(expected.Hooks, effective.Hooks) {
			return errors.New("native effective hooks differ from owned settings")
		}
	}
	var rows []struct {
		Event   string `json:"event"`
		Command string `json:"commandText"`
		Source  string `json:"source"`
	}
	if err := strictJSON(listing["hooks"], &rows); err != nil {
		return err
	}
	actual := map[nativeRowKey]int{}
	for _, row := range rows {
		if row.Source != "flagSettings" {
			return errors.New("native public hook listing contains a foreign settings source")
		}
		actual[nativeRowKey{row.Event, row.Command}]++
	}
	if !reflect.DeepEqual(actual, wanted) {
		return errors.New("native public hook listing differs from owned command rows")
	}
	return nil
}

type nativeRowKey struct{ event, command string }

func ownedCommandRows(raw []byte) (map[nativeRowKey]int, error) {
	var object map[string]json.RawMessage
	var expected struct {
		Hooks map[string]any `json:"hooks"`
	}
	if err := errors.Join(strictJSON(raw, &object), strictJSON(raw, &expected)); err != nil {
		return nil, err
	}
	if object == nil {
		return nil, errors.New("native settings object required")
	}
	if _, exists := object["hooks"]; exists && expected.Hooks == nil {
		return nil, errors.New("native hooks must be an object")
	}
	wanted := map[nativeRowKey]int{}
	for event, value := range expected.Hooks {
		groups, ok := value.([]any)
		if !ok {
			return nil, errors.New("unsupported native effective hook group")
		}
		for _, value := range groups {
			group, ok := value.(map[string]any)
			if !ok {
				return nil, errors.New("unsupported native effective hook group")
			}
			entries, ok := group["hooks"].([]any)
			if !ok {
				return nil, errors.New("unsupported native effective hook entries")
			}
			for _, value := range entries {
				entry, ok := value.(map[string]any)
				if !ok || entry["type"] != "command" {
					return nil, errors.New("native owner qualifies command hooks only")
				}
				command, ok := entry["command"].(string)
				if !ok || command == "" {
					return nil, errors.New("native command hook is empty or unsupported")
				}
				wanted[nativeRowKey{event, command}]++
			}
		}
	}
	return wanted, nil
}
