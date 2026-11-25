// cairn-codex-launch starts an interactive Codex TUI on its own local
// app-server so Cairn's presence watcher can wake the idle session through
// the Codex queue. Install it behind the codex command:
//
//	exec cairn-codex-launch --codex /path/to/real/codex -- "$@"
//
// Non-interactive codex subcommands pass straight through.
package main

import (
	"os"

	"github.com/halbritt/cairn/internal/lifecycle/codexlaunch"
)

func main() {
	os.Exit(codexlaunch.Launcher{}.Run(os.Args[1:], os.Environ(), os.Stderr))
}
