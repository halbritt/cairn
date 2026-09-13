package main

import (
	"bytes"
	"flag"
	"io"
)

// Help is the only non-protocol output these commands write. Return ErrHelp
// after rendering so main can exit successfully without starting the command.
func parseHarnessFlags(f *flag.FlagSet, args []string, out io.Writer) error {
	if err := f.Parse(args); err != flag.ErrHelp {
		return err
	}
	var help bytes.Buffer
	help.WriteString("Usage: cairn " + f.Name() + " [flags]\n")
	previous := f.Output()
	f.SetOutput(&help)
	f.PrintDefaults()
	f.SetOutput(previous)
	if _, err := help.WriteTo(out); err != nil {
		return err
	}
	return flag.ErrHelp
}
