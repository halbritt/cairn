package native

import (
	"context"
	"errors"

	"github.com/halbritt/cairn/internal/lifecycle/coordination"
	"github.com/halbritt/cairn/internal/lifecycle/install"
)

func (m *publicationMaterial) checkRetained(ctx context.Context) error {
	old, err := coordination.InspectRetainedPackage(ctx, m.selection.Retained)
	if err != nil {
		return err
	}
	memory := coordination.WatchCensusPin{Path: m.selection.RetainedMemory.Path, SHA256: m.selection.RetainedMemory.SHA256}
	coord := coordination.WatchCensusPin{Path: m.selection.RetainedCoordination.Path, SHA256: m.selection.RetainedCoordination.SHA256}
	var config roleConfiguration
	if _, err := readPinnedObject(ctx, m.selection.RetainedMemory, &config); err != nil {
		return err
	}
	if old.Harness != "claude" || len(old.Members) != 1 || old.Memory != memory || old.Binding.Path != config.Inbox {
		return errors.New("retained native package does not select this complete Claude member")
	}
	for _, member := range old.Members {
		if member.Configuration != coord {
			return errors.New("retained native member profile differs")
		}
		for _, role := range []string{"memory", "coordination"} {
			commands := m.selection.ReplaceCommands[role]
			if len(commands) != 1 || len(member.Commands[role]) == 0 || commands[0] != shellJoin(member.Commands[role]) {
				return errors.New("native inverse command differs from retained package literal argv")
			}
		}
	}
	for _, pin := range old.Pins {
		snapshot, err := install.InspectSettings(ctx, pin.Path)
		if err != nil || !snapshot.Metadata().Present || snapshot.Metadata().SHA256 != pin.SHA256 {
			return errors.Join(err, errors.New("retained inverse material changed"))
		}
		m.pins[pin.Path] = snapshot
	}
	return nil
}
