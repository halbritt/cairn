package memoryhook

import (
	"bufio"
	"bytes"
	"io"
	"os"
	"regexp"
	"strings"

	"github.com/halbritt/cairn/internal/lifecycle/binding"
	"github.com/halbritt/cairn/internal/lifecycle/value"
	"golang.org/x/text/cases"
	"golang.org/x/text/language"
)

func bindingTaskless(v *value.Value) bool { return binding.TasklessNotification(v) }
func captureBounded(messages *value.Value) (*value.Value, error) {
	if messages == nil || messages.Kind != value.Array {
		return nil, fail("invalid host dialogue")
	}
	for _, m := range messages.Items {
		if m == nil || m.Kind != value.Object || !contains([]string{"user", "assistant"}, text(m.Get("role"))) || m.Get("text") == nil || m.Get("text").Kind != value.String {
			return nil, fail("invalid host dialogue")
		}
	}
	selected := array()
	for i := len(messages.Items) - 1; i >= 0; i-- {
		message := messages.Items[i]
		candidate := array(append([]*value.Value{message}, selected.Items...)...)
		raw, err := encoded(candidate, false)
		if err != nil {
			return nil, err
		}
		if len(raw) <= 24000 {
			selected = candidate
			continue
		}
		points := message.Get("text").String
		for len(points) > 0 {
			points = points[:len(points)/2]
			clipped := clone(message)
			clipped.Set("text", value.Text("[excerpt truncated]\n"+string(points)))
			candidate = array(append([]*value.Value{clipped}, selected.Items...)...)
			raw, err = encoded(candidate, false)
			if err != nil {
				return nil, err
			}
			if len(raw) <= 24000 {
				selected = candidate
				break
			}
		}
		break
	}
	return selected, nil
}
func captureCourtesy(messages *value.Value) bool {
	if len(messages.Items) == 0 || len(messages.Items)%2 != 0 {
		return false
	}
	allowed := [][]string{{"thanks", "thank you", "thanks a lot", "thank you very much", "thx", "cheers"}, {"you're welcome", "you are welcome", "no problem", "happy to help", "glad to help", "anytime"}}
	for i, m := range messages.Items {
		role := "user"
		if i%2 == 1 {
			role = "assistant"
		}
		s := strings.TrimRight(strings.ReplaceAll(cases.Lower(language.Und).String(strip(text(m.Get("text")))), "’", "'"), ".!")
		if !value.SameText(m.Get("role"), role) || !contains(allowed[i%2], s) {
			return false
		}
	}
	return true
}

var captureInjectedBlock = func() *regexp.Regexp {
	tags := []string{"environment_context", "user_instructions", "INSTRUCTIONS", "turn_aborted", "recommended_plugins", "skills_instructions", "permissions instructions", "collaboration_mode", "multi_agent_mode", "multi_agent_role", "model_switch", "user_shell_command", "skill"}
	parts := make([]string, len(tags))
	for i, tag := range tags {
		q := regexp.QuoteMeta(tag)
		parts[i] = "<" + q + ">.*?</" + q + ">"
	}
	return regexp.MustCompile("(?s)" + strings.Join(parts, "|"))
}()

func captureOwnerParts(payload *value.Value) ([]string, error) {
	content := payload.Get("content")
	if !value.Truth(content) {
		return nil, nil
	}
	if content.Kind != value.Array {
		return nil, &value.Error{Class: "TypeError", Message: "invalid transcript content"}
	}
	var parts []*value.Value
	for _, part := range content.Items {
		if contains([]string{"input_text", "output_text"}, text(part.Get("type"))) {
			parts = append(parts, part)
		}
	}
	var result []string
	if value.SameText(payload.Get("role"), "assistant") {
		for _, part := range parts {
			if !validText(part.Get("text")) {
				return nil, &value.Error{Class: "TypeError", Message: "invalid transcript text"}
			}
			result = append(result, text(part.Get("text")))
		}
		return result, nil
	}
	kinds := payload.Get("internal_chat_message_metadata_passthrough").Get("content_item_kinds")
	if kinds != nil && kinds.Kind == value.Array && len(kinds.Items) == len(content.Items) {
		for i, part := range content.Items {
			if value.SameText(kinds.Items[i], "user.text") && value.SameText(part.Get("type"), "input_text") {
				result = append(result, text(part.Get("text")))
			}
		}
	} else {
		for _, part := range parts {
			s := text(part.Get("text"))
			if strings.HasPrefix(strings.TrimLeftFunc(s, pySpace), "# AGENTS.md instructions") {
				continue
			}
			s = captureInjectedBlock.ReplaceAllString(s, "")
			result = append(result, strip(s))
		}
	}
	var owner []string
	for _, s := range result {
		if !bindingTaskless(value.Text(s)) {
			owner = append(owner, s)
		}
	}
	return owner, nil
}

// This is memory.conversation's bounded dialogue view, not native custody or
// recovery evidence from the separate transcript package.
func captureConversation(path string) (*value.Value, []int64, error) {
	stream, err := os.Open(path)
	if err != nil {
		return nil, nil, err
	}
	defer stream.Close()
	size, err := stream.Seek(0, io.SeekEnd)
	if err != nil {
		return nil, nil, err
	}
	start := max(int64(0), size-2*1024*1024)
	if _, err = stream.Seek(start, io.SeekStart); err != nil {
		return nil, nil, err
	}
	reader := bufio.NewReader(stream)
	if size > 2*1024*1024 {
		line, e := reader.ReadBytes('\n')
		if e != nil && e != io.EOF {
			return nil, nil, e
		}
		start += int64(len(line))
	}
	raw, err := io.ReadAll(io.LimitReader(reader, 2*1024*1024))
	if err != nil {
		return nil, nil, err
	}
	messages := array()
	var offsets []int64
	position := start
	seen := map[string]bool{}
	for len(raw) > 0 {
		end := bytes.IndexAny(raw, "\r\n")
		if end < 0 {
			end = len(raw)
		} else {
			if raw[end] == '\r' && end+1 < len(raw) && raw[end+1] == '\n' {
				end++
			}
			end++
		}
		line := raw[:end]
		raw = raw[end:]
		position += int64(end)
		if len(bytes.TrimSpace(line)) == 0 {
			continue
		}
		record, e := value.DecodeBytes(line, 0)
		if e != nil {
			return nil, nil, e
		}
		if record.Kind != value.Object {
			return nil, nil, &value.Error{Class: "TypeError", Message: "invalid transcript record"}
		}
		var role, body string
		if value.SameText(record.Get("type"), "response_item") {
			payload := record.Get("payload")
			role = text(payload.Get("role"))
			if !value.SameText(payload.Get("type"), "message") || !contains([]string{"user", "assistant"}, role) {
				continue
			}
			parts, e := captureOwnerParts(payload)
			if e != nil {
				return nil, nil, e
			}
			var kept []string
			for _, part := range parts {
				if strip(part) != "" {
					kept = append(kept, part)
				}
			}
			body = strings.Join(kept, "\n")
		} else {
			role = text(record.Get("type"))
			if !contains([]string{"user", "assistant"}, role) || value.Truth(record.Get("isSidechain")) || value.Truth(record.Get("isCompactSummary")) || value.Truth(record.Get("isMeta")) {
				continue
			}
			content := record.Get("message").Get("content")
			if content != nil && content.Kind == value.String {
				body = text(content)
			} else if content != nil && content.Kind == value.Array {
				var parts []string
				for _, block := range content.Items {
					if value.SameText(block.Get("type"), "text") {
						if !validText(block.Get("text")) {
							return nil, nil, &value.Error{Class: "TypeError", Message: "invalid transcript text"}
						}
						parts = append(parts, text(block.Get("text")))
					}
				}
				body = strings.Join(parts, "\n")
			}
			if strings.HasPrefix(body, "<command-name>") || strings.HasPrefix(body, "<local-command-stdout>") || strings.HasPrefix(body, "<local-command-caveat>") {
				continue
			}
			if id := record.Get("uuid"); value.Truth(id) {
				key, e := encoded(id, false)
				if e != nil {
					return nil, nil, e
				}
				if seen[string(key)] {
					continue
				}
				seen[string(key)] = true
			}
		}
		if strip(body) != "" {
			messages.Items = append(messages.Items, value.Mapping(value.Field("role", value.Text(role)), value.Field("text", value.Text(body))))
			offsets = append(offsets, position)
		}
	}
	bounded, err := captureBounded(messages)
	if err != nil {
		return nil, nil, err
	}
	return bounded, offsets[len(offsets)-len(bounded.Items):], nil
}
