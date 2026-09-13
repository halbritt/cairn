package main

import (
	"strings"
	"testing"

	"github.com/halbritt/cairn/core"
)

func TestRejectsIngressOverridesAndTrailingRequests(t *testing.T) {
	for _, body := range []string{
		`{"observed_writer":"operator"}`,
		`{"draft":{"witness":"instrumented"}}`,
		`{"draft":{"class":"C"}}`,
		`{"draft":{"written_at":"2000-01-01"}}`,
		`{} {}`,
		strings.Repeat(" ", 128*1024+1),
	} {
		var req core.CreateRequest
		if err := decode(strings.NewReader(body), &req); err == nil {
			t.Fatal("invalid ingress accepted")
		}
	}
}
