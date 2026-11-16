package coordination

import (
	"errors"

	"github.com/halbritt/cairn/internal/lifecycle/value"
)

// An explicit absence is immutable selection data, never a missing descriptor.
func memoryAbsent(desc *value.Value) bool {
	return exactReceiptObject(desc, "absent") && desc.Get("absent").Kind == value.Bool && desc.Get("absent").Boolean
}

func validMemoryDescriptor(desc *value.Value) bool {
	return memoryAbsent(desc) || validReceiptDescriptor(desc, "path", "sha256")
}

func (r *watchDrainRead) memoryDescriptor(desc *value.Value) (*value.Value, error) {
	if memoryAbsent(desc) {
		return nil, nil
	}
	return r.descriptor(desc)
}

func (r *watchCensusRead) memoryDescriptor(desc *value.Value) (*value.Value, WatchCensusPin, error) {
	if memoryAbsent(desc) {
		return nil, WatchCensusPin{}, nil
	}
	return r.descriptor(desc)
}

func (r *watchCensusRead) unboundMemoryDescriptor(desc *value.Value) (*value.Value, WatchCensusPin, error) {
	if memoryAbsent(desc) {
		return nil, WatchCensusPin{}, nil
	}
	return r.unboundDescriptor(desc)
}

func coordinationOnlyProfiles(current, manifest *value.Value, coordinators []*value.Value) error {
	enabled := manifest.Get("enabled")
	if !memoryAbsent(manifest.Get("memory_config")) || enabled == nil || enabled.Kind != value.Bool || enabled.Boolean ||
		value.Truth(current.Get("inbox_recall_binding")) || len(coordinators) < 1 || len(coordinators) > 8 {
		return errors.New("coordination-only selection must be explicitly disabled and unbound")
	}
	matched := 0
	for _, profile := range coordinators {
		if _, err := ValidateConfig(profile); err != nil {
			return err
		}
		if value.Truth(profile.Get("inbox_recall_binding")) {
			return errors.New("coordination-only profile has a binding")
		}
		for _, key := range []string{"harness", "cairn", "socket", "token_file", "repo"} {
			if !nativeEqual(current.Get(key), profile.Get(key)) {
				return errors.New("coordination-only profile selectors differ")
			}
		}
		if nativeEqual(current, profile) {
			matched++
		}
	}
	if matched != 1 {
		return errors.New("coordination-only selection must name the exact current profile once")
	}
	return bootstrapLegacyProfileHomes(coordinators)
}
