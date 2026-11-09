package coordination

import (
	"strconv"
	"strings"
)

type cliHelpAction struct{ name, text string }

func (c *CLI) help(prog string) string {
	options := []cliHelpAction{{"-h, --help", "show this help message and exit"}}
	positions := []cliHelpAction{}
	description := ""
	switch prog {
	case "coordination.py inbox drain":
		options = append(options, cliHelpAction{"--config CONFIG", ""}, cliHelpAction{"--agent-id AGENT_ID", ""}, cliHelpAction{"--execution-id EXECUTION_ID", ""}, cliHelpAction{"--lease-seconds LEASE_SECONDS", ""})
	case "coordination.py inbox":
		positions = append(positions, cliHelpAction{"{complete,ack,respond,finish-reply}", ""})
		options = append(options, cliHelpAction{"--context CONTEXT", ""}, cliHelpAction{"--version VERSION RECORD_UUID", ""}, cliHelpAction{"--disposition {handled,ignored,failed}", ""}, cliHelpAction{"--code {unsupported_kind,source_unavailable,processing_failed}", ""})
	case "cairn-inbox":
		positions = append(positions, cliHelpAction{"{read,inspect,settle,advance}", ""}, cliHelpAction{"handle", ""}, cliHelpAction{"{handled,ignored,failed}", ""})
		options = append(options, cliHelpAction{"--code {unsupported_kind,source_unavailable,processing_failed}", ""})
	default:
		description = "Associate native sessions with Cairn UUIDs; watch their host process liveness."
		positions = append(positions, cliHelpAction{"{hook,watch,recover-refused-wake,recover-native-refused-wake,recover-codex-refused-wake,recover-codex-ended-turn,recover-daemon-start-claim,check-codex-native-health,reconcile-codex-queue-wake,inspect-held-refusal}", ""})
		options = append(options, cliHelpAction{"--config CONFIG", ""}, cliHelpAction{"--event EVENT", ""}, cliHelpAction{"--config-dir CONFIG_DIR", ""}, cliHelpAction{"--once", ""}, cliHelpAction{"--native-session-id NATIVE_SESSION_ID", "recover-refused-wake: the held native session"}, cliHelpAction{"--attempt-id ATTEMPT_ID", "recover-refused-wake: the held attempt"}, cliHelpAction{"--native-turn-id NATIVE_TURN_ID", "recover-refused-wake: the refused claude-channel prompt"}, cliHelpAction{"--delivery-id DELIVERY_ID", "reconcile-codex-queue-wake: the retained delivery UUID"}, cliHelpAction{"--queued-submission-id QUEUED_SUBMISSION_ID", "reconcile-codex-queue-wake: the retained queue submission ID"}, cliHelpAction{"--outcome {admitted_active,completed,cancelled,never_admitted,unknown}", "reconcile-codex-queue-wake: expected reconciliation outcome"}, cliHelpAction{"--record RECORD", "reconcile-codex-queue-wake: contiguous record locator OFFSET:LENGTH:SHA256"})
		for _, name := range []string{"enqueue", "dequeue", "refusal"} {
			options = append(options, cliHelpAction{"--" + name + "-record " + strings.ToUpper(name) + "_RECORD", "recover-native-refused-wake: " + name + " record OFFSET:LENGTH:SHA256"})
		}
		for _, name := range []string{"start", "context", "end"} {
			options = append(options, cliHelpAction{"--" + name + "-record " + strings.ToUpper(name) + "_RECORD", "recover-codex-refused-wake: " + name + " record OFFSET:LENGTH:SHA256"})
		}
		options = append(options, cliHelpAction{"--check-only", "validate Codex recovery evidence without settlement"})
	}
	width := 78
	if n, err := strconv.Atoi(c.env("COLUMNS")); err == nil && n > 0 {
		width = n - 2
	}
	helpPosition := 24
	if helpPosition > width-20 {
		helpPosition = width - 20
	}
	if helpPosition < 4 {
		helpPosition = 4
	}
	longest := 0
	for _, group := range [][]cliHelpAction{positions, options} {
		for _, a := range group {
			if len(a.name)+4 > longest {
				longest = len(a.name) + 4
			}
		}
	}
	if longest < helpPosition {
		helpPosition = longest
	}
	var out strings.Builder
	writeWrapped := func(text, indent string) {
		words := strings.Fields(text)
		line := indent
		for _, word := range words {
			sep := " "
			if line == indent {
				sep = ""
			}
			if len(line)+len(sep)+len(word) > width && line != indent {
				out.WriteString(line + "\n")
				line = indent
				sep = ""
			}
			line += sep + word
		}
		out.WriteString(line + "\n")
	}
	if description != "" {
		out.WriteByte('\n')
		writeWrapped(description, "")
	}
	for i, group := range [][]cliHelpAction{positions, options} {
		if len(group) == 0 {
			continue
		}
		out.WriteByte('\n')
		if i == 0 {
			out.WriteString("positional arguments:\n")
		} else {
			out.WriteString("options:\n")
		}
		for _, action := range group {
			header := "  " + action.name
			if action.text == "" {
				out.WriteString(header + "\n")
				continue
			}
			if len(header)+2 > helpPosition {
				out.WriteString(header + "\n")
				writeWrapped(action.text, strings.Repeat(" ", helpPosition))
			} else {
				words := strings.Fields(action.text)
				line := header + strings.Repeat(" ", helpPosition-len(header))
				for _, word := range words {
					sep := " "
					if len(line) == helpPosition {
						sep = ""
					}
					if len(line)+len(sep)+len(word) > width && len(line) > helpPosition {
						out.WriteString(line + "\n")
						line = strings.Repeat(" ", helpPosition)
						sep = ""
					}
					line += sep + word
				}
				out.WriteString(line + "\n")
			}
		}
	}
	return out.String()
}
