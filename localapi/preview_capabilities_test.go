package localapi

import (
	"context"
	"encoding/json"
	"strings"
	"testing"
)

func TestPreviewCapabilitiesClosedAndChoiceStable(t *testing.T) {
	good := json.RawMessage(`{"schema":"cairn.preview-capabilities/1","entities_omitted":true}`)
	for _, raw := range []string{"", "null", `{}`, `{"schema":"cairn.preview-capabilities/1"}`, `{"schema":"future","entities_omitted":true}`, `{"schema":"cairn.preview-capabilities/1","entities_omitted":null}`, `{"schema":"cairn.preview-capabilities/1","entities_omitted":"true"}`, `{"schema":"cairn.preview-capabilities/1","entities_omitted":true,"extra":1}`, `{"schema":"cairn.preview-capabilities/1","entities_omitted":false,"entities_omitted":true}`, string(good) + `{}`, string(good) + strings.Repeat(" ", 256)} {
		if RecognizesCompactPreviews(json.RawMessage(raw)) {
			t.Fatalf("accepted %q", raw)
		}
		choice := &PreviewCapabilityChoice{}
		choice.Observe(json.RawMessage(raw))
		choice.Observe(good)
		got, err := choice.Resolve(context.Background(), nil)
		if err != nil || got {
			t.Fatalf("false choice changed: %v %v", got, err)
		}
	}
	choice := &PreviewCapabilityChoice{}
	choice.Observe(good)
	choice.Observe(nil)
	got, err := choice.Resolve(context.Background(), nil)
	if err != nil || !got {
		t.Fatalf("positive choice changed: %v %v", got, err)
	}
}
