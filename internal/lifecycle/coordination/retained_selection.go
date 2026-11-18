package coordination

import (
	"context"
	"errors"
	"os"
	"sort"

	"github.com/halbritt/cairn/internal/lifecycle/install"
	"github.com/halbritt/cairn/internal/lifecycle/value"
)

// RetainedPackageObservation is selected data from the existing strict /2
// package reader. It conveys no native admission or publication authority.
type RetainedPackageObservation struct {
	Harness         string
	Binding, Memory WatchCensusPin
	Members         map[string]RetainedMemberObservation
	Pins            []WatchCensusPin
}

type RetainedMemberObservation struct {
	Configuration WatchCensusPin
	Commands      map[string][]string
}

// InspectRetainedPackage reuses the census/bootstrap package validator without
// running a watcher, producer, Python program, lock or mutation.
func InspectRetainedPackage(ctx context.Context, selection install.Generation) (RetainedPackageObservation, error) {
	var out RetainedPackageObservation
	pins := map[string]WatchCensusPin{}
	read := func(pin WatchCensusPin) ([]byte, error) {
		if err := ctx.Err(); err != nil {
			return nil, err
		}
		raw, digest, err := compatibilityOpened(pin.Path, 1048576, uint32(os.Getuid()))
		if err != nil || digest != pin.SHA256 {
			return nil, errors.Join(err, retainedPackageError())
		}
		pins[pin.Path] = pin
		return raw, nil
	}
	profileRead := func(pin WatchCensusPin) ([]byte, error) {
		if err := ctx.Err(); err != nil {
			return nil, err
		}
		observed, err := bootstrapLegacyProfile(pin.Path)
		if err != nil || observed.SHA256 != pin.SHA256 {
			return nil, errors.Join(err, retainedPackageError())
		}
		pins[pin.Path] = pin
		return observed.Raw, nil
	}
	sourceRead := func(pin WatchCensusPin) ([]byte, error) {
		if err := ctx.Err(); err != nil {
			return nil, err
		}
		raw, digest, err := compatibilityOpenedModes(pin.Path, 1048576, uint32(os.Getuid()), 0o700, 0o755)
		if err != nil || digest != pin.SHA256 {
			return nil, errors.Join(err, retainedPackageError())
		}
		pins[pin.Path] = pin
		return raw, nil
	}
	pkg, err := readRetainedPackage(ctx, WatchCensusPin{Path: selection.Path, SHA256: selection.SHA256}, read, profileRead, sourceRead)
	if err != nil {
		return out, err
	}
	out.Harness = nativeText(pkg.Object.Get("harness"))
	out.Binding, err = retainedPackagePin(pkg.Object.Get("binding"))
	if err != nil {
		return RetainedPackageObservation{}, err
	}
	out.Memory, err = retainedPackagePin(pkg.Binding.Get("memory_config"))
	if err != nil {
		return RetainedPackageObservation{}, err
	}
	out.Members = map[string]RetainedMemberObservation{}
	for _, member := range pkg.Object.Get("members").Members {
		configuration, err := retainedPackagePin(member.Value.Get("config"))
		if err != nil {
			return RetainedPackageObservation{}, err
		}
		row := RetainedMemberObservation{Configuration: configuration, Commands: map[string][]string{}}
		for _, role := range member.Value.Get("argv").Members {
			if role.Value.Kind != value.Array {
				return RetainedPackageObservation{}, retainedPackageError()
			}
			for _, word := range role.Value.Items {
				if word.Kind != value.String {
					return RetainedPackageObservation{}, retainedPackageError()
				}
				row.Commands[string(role.Key)] = append(row.Commands[string(role.Key)], string(word.String))
			}
		}
		out.Members[string(member.Key)] = row
	}
	for _, pin := range pins {
		out.Pins = append(out.Pins, pin)
	}
	sort.Slice(out.Pins, func(i, j int) bool { return out.Pins[i].Path < out.Pins[j].Path })
	return out, ctx.Err()
}
