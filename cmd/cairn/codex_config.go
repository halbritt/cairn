package main

import (
	"encoding/json"
	"fmt"
	"io"
	"strings"
	"unicode/utf8"
)

func writeCodexConfig(out io.Writer, args []string, executable string) error {
	f := flags("codex-config")
	o := mcpFlags(f)
	required := f.Bool("required", false, "require Cairn to connect before Codex starts")
	if err := f.Parse(args); err != nil {
		return err
	}
	if err := o.validate(f.NArg()); err != nil {
		return err
	}
	command, err := o.command(executable)
	if err != nil {
		return err
	}
	quoted := make([]string, len(command))
	for i, arg := range command {
		if !utf8.ValidString(arg) {
			return invalid("Codex configuration requires UTF-8 arguments")
		}
		encoded, err := json.Marshal(arg)
		if err != nil {
			return err
		}
		// JSON's string escapes also work in TOML basic strings. TOML
		// additionally forbids a literal DEL character; Go quoting uses
		// escapes such as \x7f that TOML 1.0 does not accept.
		quoted[i] = strings.ReplaceAll(string(encoded), "\x7f", `\u007f`)
	}
	_, err = fmt.Fprintf(out, `[mcp_servers.cairn]
command = %s
args = [%s]
enabled_tools = ["cairn_search", "cairn_pull", "cairn_pull_evidence", "cairn_remember", "cairn_edit"]
required = %t
startup_timeout_sec = 15
`, quoted[0], strings.Join(quoted[1:], ", "), *required)
	return err
}
