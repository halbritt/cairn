// cairn-term-launch runs an interactive terminal agent on a pseudo-terminal it
// owns, so Cairn's presence watcher can type a wake prompt into an idle
// session without a terminal multiplexer. Install it behind the agent command:
//
//	exec cairn-term-launch -- /path/to/real/agy "$@"
//
// Runs whose stdin or stdout is not a terminal exec the command unchanged.
package main

import (
	"os"

	"github.com/halbritt/cairn/internal/lifecycle/termlaunch"
)

func main() {
	os.Exit(termlaunch.Launcher{}.Run(os.Args[1:], os.Environ(), os.Stderr))
}
