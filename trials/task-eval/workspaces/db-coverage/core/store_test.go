package core

import (
	"os"
	"strings"
	"testing"
)

func TestActiveQueryDatabase(t *testing.T) {
	if os.Getenv("CAIRN_TEST_DATABASE_URL") == "" {
		t.Skip("CAIRN_TEST_DATABASE_URL not set")
	}
	if !strings.Contains(ActiveQuery("r"), "lifecycle = 'active'") {
		t.Fatal("query lost the lifecycle filter")
	}
}
