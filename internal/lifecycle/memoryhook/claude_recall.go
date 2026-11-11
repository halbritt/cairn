package memoryhook

import (
	"context"

	"github.com/halbritt/cairn/internal/lifecycle/memoryclient"
	"github.com/halbritt/cairn/internal/lifecycle/value"
)

// claudeRecall is the reached Claude Start/prompt consumer. Unbound calls retain
// ordinary Source recall; a binding must be resolved by the checked entry.
func claudeRecall(ctx context.Context, client *memoryclient.Client, config, event, state, status *value.Value, project string) (string, error) {
	return ordinaryRecall(ctx, client, config, event, state, status, project)
}
