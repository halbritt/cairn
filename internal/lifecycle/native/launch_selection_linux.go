package native

import (
	"bytes"
	"context"
	"errors"
	"os"
	"reflect"
	"strconv"

	"github.com/halbritt/cairn/internal/lifecycle/coordination"
	"github.com/halbritt/cairn/internal/lifecycle/install"
)

func checkPinnedImage(ctx context.Context, pin Pin) (err error) {
	file, err := os.Open(pin.Path)
	if err != nil {
		return err
	}
	defer func() { err = errors.Join(err, file.Close()) }()
	info, err := file.Stat()
	if err != nil || !info.Mode().IsRegular() || info.Mode().Perm() != 0500 {
		return errors.Join(err, errors.New("checked Go image mode/type differs"))
	}
	return checkImage(ctx, file, info, pin)
}

func (m *publicationMaterial) checkLaunchers(ctx context.Context) error {
	m.lifecycle = m.record.Lifecycle
	files, err := install.ReadGeneration(ctx, m.lifecycle)
	if err != nil {
		return err
	}
	pins := map[string]Pin{}
	for _, file := range files {
		pins[file.Name] = Pin{file.Path, file.SHA256}
	}
	var receipt struct {
		Binary       Pin   `json:"binary"`
		Memory       Pin   `json:"memory_config"`
		Coordinators []Pin `json:"coordination_configs"`
	}
	if _, err := readPinnedObject(ctx, pins["receipt.json"], &receipt); err != nil {
		return err
	}
	m.goImage = receipt.Binary
	if err := checkPinnedImage(ctx, receipt.Binary); err != nil {
		return err
	}
	if receipt.Memory != m.record.Roles["memory"] {
		return errors.New("native memory profile differs from checked receipt")
	}
	coordIndex, matches := -1, 0
	for i, config := range receipt.Coordinators {
		if config == m.record.Roles["coordination"] {
			coordIndex, matches = i+1, matches+1
		}
	}
	if matches != 1 {
		return errors.New("native coordination profile is missing or ambiguous in receipt")
	}
	var metadata struct {
		Anchors map[string]struct {
			Vector []string `json:"vector"`
		} `json:"anchors"`
	}
	if _, err := readPinnedObject(ctx, pins["selection.json"], &metadata); err != nil {
		return err
	}
	events := map[string]map[string]int{
		"memory":       {"SessionStart": 13, "UserPromptSubmit": 13, "PreCompact": 150, "SessionEnd": 150, "PostToolUse": 13, "PostToolUseFailure": 13},
		"coordination": {"SessionStart": 15, "UserPromptSubmit": 15, "Stop": 15, "SessionEnd": 2, "PostToolUse": 2},
	}
	seen := map[string]bool{}
	checkedRoles := map[string]bool{}
	for _, row := range m.record.Rows {
		key := row.Role + ":" + row.Event
		if seen[key] || events[row.Role][row.Event] != row.Hook.Timeout {
			return errors.New("native row event/timeout is duplicated or unsupported")
		}
		seen[key] = true
		index := 0
		if row.Role == "coordination" {
			index = coordIndex
		}
		label := row.Role + ":" + strconv.Itoa(index) + ":" + strconv.Itoa(row.Hook.Timeout)
		vector := metadata.Anchors[label].Vector
		if len(vector) != 21 {
			return errors.New("checked native launch vector has unexpected shape")
		}
		values := map[string]string{}
		for i, flag := range []string{"--receipt", "--receipt-sha256", "--role", "--entrypoint", "--config", "--hook-timeout", "--launch-root", "--shell"} {
			if vector[5+2*i] != flag {
				return errors.New("checked native launch flags differ")
			}
			values[flag] = vector[6+2*i]
		}
		if values["--receipt"] != pins["receipt.json"].Path || values["--receipt-sha256"] != pins["receipt.json"].SHA256 ||
			values["--role"] != row.Role || values["--config"] != m.record.Roles[row.Role].Path || values["--hook-timeout"] != strconv.Itoa(row.Hook.Timeout) {
			return errors.New("checked native launch vector selects different receipt/profile/role")
		}
		if !checkedRoles[row.Role] {
			checkedRoles[row.Role] = true
			m.compatibility = append(m.compatibility, coordination.CompatibilityContext{
				Role: row.Role, Entrypoint: values["--entrypoint"], ConfigPath: values["--config"],
				HookTimeout: float64(row.Hook.Timeout), LaunchRoot: values["--launch-root"], Shell: values["--shell"],
				ReceiptPath: values["--receipt"], ReceiptSHA256: values["--receipt-sha256"],
			})
		}
		launcher := m.pins[row.Checked[0]]
		expected := "#!" + values["--shell"] + "\nexec " + shellJoin(append([]string{receipt.Binary.Path, row.Role}, vector[5:]...)) + " -- \"$@\"\n"
		if launcher == nil || !bytes.Equal(launcher.Bytes(), []byte(expected)) || launcher.Metadata().Mode&0777 != 0500 {
			return errors.New("native launcher bytes differ from independently derived Go receipt vector")
		}
		argv := []string{row.Checked[0]}
		if row.Role == "coordination" {
			argv = append(argv, "hook", "--config", m.record.Roles[row.Role].Path)
		}
		if !reflect.DeepEqual(argv, row.Argv) {
			return errors.New("native row argv differs from selected role")
		}
	}
	if len(seen) != 11 {
		return errors.New("native Claude selection is missing required rows")
	}
	return nil
}
