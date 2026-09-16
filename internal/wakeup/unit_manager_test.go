package wakeup

import (
	"context"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestStopRechecksCleanupAfterSystemdStopError(t *testing.T) {
	for _, state := range []string{"collected", "running", "unreadable"} {
		t.Run(state, func(t *testing.T) {
			dir := t.TempDir()
			// systemd may collect a deadline-expired unit between show and stop.
			// Exercise the command boundary, including an unsuccessful stop that
			// does not establish cleanup and a failed subsequent observation.
			script := `#!/bin/sh
if [ "$2" = stop ]; then
  touch "$CAIRN_STOP_TEST_DIR/stopped"
  exit 5
fi
if [ ! -e "$CAIRN_STOP_TEST_DIR/stopped" ]; then
  printf 'LoadState=loaded\nActiveState=active\nControlGroup=\n'
elif [ "$CAIRN_STOP_TEST_STATE" = collected ]; then
  printf 'LoadState=not-found\nActiveState=inactive\nControlGroup=\n'
elif [ "$CAIRN_STOP_TEST_STATE" = running ]; then
  printf 'LoadState=loaded\nActiveState=active\nControlGroup=\n'
else
  exit 1
fi
`
			if err := os.WriteFile(filepath.Join(dir, "systemctl"), []byte(script), 0700); err != nil {
				t.Fatal(err)
			}
			t.Setenv("PATH", dir+string(os.PathListSeparator)+os.Getenv("PATH"))
			t.Setenv("CAIRN_STOP_TEST_DIR", dir)
			t.Setenv("CAIRN_STOP_TEST_STATE", state)
			err := (UnitManager{}).stop(context.Background(), "test-attempt")
			if state == "collected" {
				if err != nil {
					t.Fatalf("confirmed collected unit stranded cleanup: %v", err)
				}
			} else if err == nil || !strings.Contains(err.Error(), "stop worker unit") {
				t.Fatalf("unconfirmed cleanup lost stop failure: %v", err)
			}
		})
	}
}
