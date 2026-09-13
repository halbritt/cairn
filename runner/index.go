package runner

import (
	"encoding/json"
	"fmt"
	"regexp"
	"time"

	"github.com/halbritt/cairn/core"
	"github.com/halbritt/cairn/internal/indexview"
)

// IndexTools declares existing harness tools; it does not install or authorize them.
type IndexTools struct {
	Pull   string
	Search string
}

var nativeToolName = regexp.MustCompile(`^[A-Za-z_][A-Za-z0-9_-]{0,127}$`)

func (tools IndexTools) Guidance() (string, error) {
	if !nativeToolName.MatchString(tools.Pull) || !nativeToolName.MatchString(tools.Search) || tools.Pull == tools.Search {
		return "", &core.Error{Code: "INVALID_REQUEST", Message: "compact input requires distinct pull and search tool names"}
	}
	return fmt.Sprintf("CAIRN MEMORY\nScoped reference context follows. Ordinary A notes and previews are fallible; verify relevant sources. Read required selected context. To read an optional source, call %s with its complete pull_arguments. Respect tool permissions. If a handle is stale or its budget is exhausted, use %s for a fresh search. This launcher supplies initial context; it does not establish task success.\n", tools.Pull, tools.Search), nil
}

func renderRunIndex(index core.IndexResult, req Request) (string, error) {
	if !index.ExpiresAt.After(time.Now()) {
		return "", &core.Error{Code: "STALE_HANDLE", Message: "index expired before launch; retrieve a fresh index"}
	}
	if index.ExpansionReader != req.Compile.ExpansionReader {
		return "", &core.Error{Code: "INVALID_REQUEST", Message: "index expansion reader differs from the run declaration"}
	}
	prefix, err := req.IndexTools.Guidance()
	if err != nil {
		return "", err
	}
	view, err := indexview.Present(index, req.Compile.RequestID, nil, req.Compile.AvailableTokens-len(prefix))
	if err != nil {
		return "", err
	}
	body, err := json.Marshal(view)
	if err != nil {
		return "", err
	}
	input := prefix + string(body) + "\nTASK\n" + req.Prompt
	if len(input) > MaxArgumentBytes {
		return "", &core.Error{Code: "BUDGET_REFUSED", Message: "compact memory and task exceed initial input limit"}
	}
	return input, nil
}

func sameIndexOffset(page *core.BrowsePage, offset *int) bool {
	return (page == nil && offset == nil) || (page != nil && offset != nil && page.Offset == *offset)
}
