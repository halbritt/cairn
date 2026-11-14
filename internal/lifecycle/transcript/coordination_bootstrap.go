package transcript

import (
	"context"
	"fmt"
	"io"
	"time"
)

// RunCoordinationBootstrap retains the ordinary concrete reader and authenticated
// host. No serialized result or scope grants permission to publish a selection.
func RunCoordinationBootstrap(ctx context.Context, args []string, out, diagnostic io.Writer) int {
	ctx, cancel := context.WithTimeout(ctx, 30*time.Second)
	defer cancel()
	cli, err := NewCoordinationCLI(CoordinationCLIOptions{ErrOut: diagnostic})
	if err != nil {
		_, _ = fmt.Fprintf(diagnostic, "Cairn unit bootstrap: %v\n", err)
		return 1
	}
	return cli.RunUnitBootstrap(ctx, args, out)
}
