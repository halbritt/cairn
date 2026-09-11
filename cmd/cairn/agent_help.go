package main

// commandHelp is human-readable output for an explicitly requested help path.
// Ordinary command results retain their JSON response envelope.
type commandHelp string

const agentHelp = `Usage: cairn agent [--token-file FILE] [--socket PATH] OPERATION

Connection flags precede OPERATION. Normal calls use the authenticated Unix API.
Help needs no credentials, connection, home directory or stdin.

Everyday operations:
  remember     Save a selected note using text, --stdin or a JSON draft
  search       Find relevant notes using a query or --browse
  pull         Read a selected note using the returned receipt and handle
  pull-evidence Read a selected supporting source
  version      Identify the CLI and running API builds

JSON operations (one request on stdin):
  revise       Replace only a note's body
  append       Add text verbatim, including your chosen paragraph separators
  replace      Replace one uniquely matching passage
  edit         Replace a complete draft; preserve metadata intentionally
  cite         Replace an ordinary note's evidence citations
  history      Inspect retained versions of one note
  assessments  Read owned task-review history
  assess-run   Append or correct a qualitative task review
  recompile    Reconstruct an owned historical receipt for inspection

Use cairn agent OPERATION --help for these JSON operations and their examples.
Save your request in a file, then execute:
  cairn agent --token-file TOKEN_FILE OPERATION < request.json

Use a new request UUID for each intended write; retry identical JSON after an
uncertain response. Read the complete current note before changing it.
The API still owns authorization, version checks and privacy.
`

func agentOperationHelp(operation string) (commandHelp, error) {
	var detail, example string
	switch operation {
	case "revise":
		detail = "Replace an active ordinary note's body, preserving its metadata and citations.\n"
		example = `{"request_id":"NEW_UUID","record_id":"RECORD_UUID","expected_version":1,"repo":"REPOSITORY","body":"Corrected note text"}`
	case "append":
		detail = "Append body verbatim to an active ordinary note. Include paragraph separators\nyourself; existing text, metadata and citations remain. The combined body must\nfit the 65536-byte note limit.\n"
		example = `{"request_id":"NEW_UUID","record_id":"RECORD_UUID","expected_version":1,"repo":"REPOSITORY","body":"\n\nSelected additional guidance."}`
	case "replace":
		detail = "Replace exactly one occurrence of old_text in an active ordinary note.\nNo match or multiple matches refuse. Supply enough surrounding text to identify\nthe passage. An explicit empty new_text removes it if the note stays nonblank.\nOther text, metadata and citations remain.\n"
		example = `{"request_id":"NEW_UUID","record_id":"RECORD_UUID","expected_version":1,"repo":"REPOSITORY","old_text":"Earlier guidance","new_text":"Corrected guidance"}`
	case "edit":
		detail = "Replace a complete draft of an active ordinary note. Unlike the native\ncairn_edit tool, this API operation requires draft; a top-level body is not\naccepted. Use revise, append or replace for text-only changes. Start from the\ncurrent draft and preserve its kind, scope, pins, entities and other metadata\nintentionally. Scope and sensitivity changes require separate authority.\n"
		example = `{"request_id":"NEW_UUID","record_id":"RECORD_UUID","expected_version":1,"draft":{"kind":"note","body":"Corrected note text","scope":{"repo":"REPOSITORY","task_id":"*","run_id":"*"},"claim_type":"self","sensitivity":"shareable"}}`
	case "cite":
		detail = "Replace an active ordinary note's evidence citations; an explicit [] clears\ncurrent citations while retaining earlier versions. Pull and verify selected\nevidence before attaching it. Each citation has evidence_id, expected_sha256 and\noptional spans [{offset,length}]. Text-only edits preserve these citations.\n"
		example = `{"request_id":"NEW_UUID","record_id":"RECORD_UUID","expected_version":1,"repo":"REPOSITORY","evidence_citations":[{"evidence_id":"EVIDENCE_UUID","expected_sha256":"SOURCE_SHA256"}]}`
	case "history":
		detail = "List newest-first retained version metadata (limit 1..100, default 20).\nFollow next_before_version as before_version. For one exact body, replace limit\nwith a positive version; do not combine version with paging fields. An exact\nversion also accepts span: {offset,length} in bytes. History is not current\nauthority; pull the current note before editing. No request UUID is required.\n"
		example = `{"record_id":"RECORD_UUID","limit":20}`
	case "assessments":
		detail = "Read retained task-review versions for an owned receipt, oldest first,\nincluding full reasons and evidence IDs. This is task-review history, not note\nhistory. Repository, owner and destination must still match the profile.\nNo request UUID is required.\n"
		example = `{"receipt_id":"RECEIPT_UUID"}`
	case "assess-run":
		detail = "Append a review of an owned receipt. Read assessments first; expected_version\nis 0 for the initial review, otherwise the latest reviewed version. Agent\naccounts remain testimony. Qualitative benefit can be described while outcome\nis unknown; a successful process or tool call does not establish acceptance.\nAny non-unknown outcome requires selected evidence. Exact retries return the\noriginal review even after a later correction.\n"
		example = `{"request_id":"NEW_UUID","receipt_id":"RECEIPT_UUID","expected_version":0,"task_outcome":"unknown","failure_domain":"unknown","failure_kind":"","method":"qualitative-review/1","evidence_ids":[],"reason":"Describe the task, recalled guidance, observed result, competing explanations and uncertainty."}`
	case "recompile":
		detail = "Inspect an owned receipt's retained read set using its original query and\nentities, if any. Use the original owner and destination profile. Later notes\ndo not join the old set, and current privacy and forgetting still apply.\nA saved query digest cannot recover missing query text. This is historical\ninspection, not permission to launch or deliver an old package.\n"
		example = `{"receipt_id":"RECEIPT_UUID","query":"original query"}`
	default:
		return "", invalid("no agent JSON-operation help for this name; use agent --help")
	}
	text := "Usage: cairn agent [--token-file FILE] [--socket PATH] " + operation + " < request.json\n\n" + detail
	text += "\nReplace example placeholders with actual values. For writes, generate a new\nrequest UUID and use the current expected_version; retry identical JSON after\nan uncertain response. VERSION_CONFLICT requires a fresh read and reconciliation.\nHelp does not read stdin or connect to the API.\n\nExample JSON:\n" + example + "\n"
	return commandHelp(text), nil
}
