package transcript

import (
	"context"
	"fmt"
	"io"
	"time"
)

// RunCoordinationBootstrap retains the ordinary concrete reader and authenticated
// host. Effectful operations capture a real lifecycle-only fence in this
// process. No serialized result or scope substitutes for that receipt.
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
