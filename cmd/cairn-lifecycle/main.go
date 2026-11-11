// cairn-lifecycle is the private receipt-selected activation and coordination
// image. It exposes no store or public command dispatcher and has no flag that
// enables cairn's separately disabled public lifecycle registration.
package main

import (
	"context"
	"io"
	"os"

	"github.com/halbritt/cairn/internal/lifecycle/activationipc"
	"github.com/halbritt/cairn/internal/lifecycle/inboxipc"
	"github.com/halbritt/cairn/internal/lifecycle/transcript"
)

func main() { os.Exit(run(os.Args[1:], os.Stdin, os.Stdout, os.Stderr)) }

func run(args []string, input io.Reader, output, diagnostic io.Writer) int {
	if len(args) >= 2 && args[0] == "lifecycle" && args[1] == "inbox" {
		return inboxipc.Run(args, input, output, diagnostic)
	}
	if len(args) > 0 && args[0] == "coordination" {
		return transcript.RunCoordinationProcess(context.Background(), args[1:], input, output, diagnostic)
	}
	return activationipc.Run(args, input, output, diagnostic)
}
