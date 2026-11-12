package coordination

import (
	"errors"
	"os"
	"path/filepath"
	"sort"
	"strings"

	"github.com/halbritt/cairn/internal/lifecycle/journal"
	"github.com/halbritt/cairn/internal/lifecycle/value"
)

// WatchJournalFacts preserves observations rather than promoting the legacy
// settled Boolean to a release-drain verdict. In particular ReplyPossible and
// missing indexed files remain visible. Lock/lease ownership needs its separate
// authoritative observation; inspection never acquires or creates lock files.
type WatchJournalFacts struct {
	AttemptID string
	Status    journal.JournalStatus
}

type WatchJournalInspection struct {
	DirectoryExists bool
	Journals        []WatchJournalFacts
}

// InspectWatchJournals includes all files in a shared state's intents directory,
// plus missing indexed references. It never flushes, sends commands or locks.
// The caller still needs actual lock facts and a unit barrier before using
// these observations to authorize a transition.
func InspectWatchJournals(stateDir string, indexed []string) (WatchJournalInspection, error) {
	var inspection WatchJournalInspection
	if !compatibilityCanonical(stateDir) {
		return inspection, errors.New("journal inspection requires a canonical state directory")
	}
	entries, err := os.ReadDir(filepath.Join(stateDir, "intents"))
	if err != nil && !errors.Is(err, os.ErrNotExist) {
		return inspection, err
	}
	inspection.DirectoryExists = err == nil
	ids := make(map[string]bool, len(indexed)+len(entries))
	for _, id := range indexed {
		ids[id] = true
	}
	for _, entry := range entries {
		if strings.HasSuffix(entry.Name(), ".json") {
			ids[strings.TrimSuffix(entry.Name(), ".json")] = true
		}
	}
	ordered := make([]string, 0, len(ids))
	for id := range ids {
		if !boundedName(value.Text(id), true) || id == "." || id == ".." {
			return inspection, errors.New("invalid journal reference")
		}
		ordered = append(ordered, id)
	}
	sort.Strings(ordered)
	for _, id := range ordered {
		inspection.Journals = append(inspection.Journals, WatchJournalFacts{
			AttemptID: id, Status: journal.Inspect(stateDir, id),
		})
	}
	return inspection, nil
}
