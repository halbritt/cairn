package transcript

import (
	"context"
	"fmt"
	"io"
	"time"
)

// RunCoordinationUnitControl uses the existing concrete source profile and
// authenticated file host. It adds no Store operation or public enable flag.
func RunCoordinationUnitControl(ctx context.Context, args []string, out, diagnostic io.Writer) int {
	ctx, cancel := context.WithTimeout(ctx, 30*time.Second)
	defer cancel()
	cli, err := NewCoordinationCLI(CoordinationCLIOptions{ErrOut: diagnostic})
	if err != nil {
		_, _ = fmt.Fprintf(diagnostic, "Cairn unit control: %v\n", err)
		return 1
	}
	return cli.RunUnitControl(ctx, args, out)
}
