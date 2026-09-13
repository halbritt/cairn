package buildinfo

import (
	"encoding/json"
	"runtime/debug"
	"strings"
	"testing"
)

func TestBuildIdentityExcludesConfigurationAndPreservesUnknowns(t *testing.T) {
	unknown := fromBuildInfo(nil)
	if unknown.Modified != nil || unknown.Revision != "" || unknown.GoVersion == "" || unknown.Label() != "unknown" {
		t.Fatalf("missing metadata looked known: %+v", unknown)
	}
	for _, state := range []string{"true", "false", "unavailable"} {
		info := fromBuildInfo(&debug.BuildInfo{GoVersion: "go-test", Main: debug.Module{Version: "(devel)", Path: "private/module"}, Settings: []debug.BuildSetting{
			{Key: "vcs", Value: "git"}, {Key: "vcs.revision", Value: "revision"}, {Key: "vcs.modified", Value: state},
			{Key: "-ldflags", Value: "private-flag-value"}, {Key: "CGO_CFLAGS", Value: "private-build-path"},
		}})
		encoded, err := json.Marshal(info)
		if err != nil {
			t.Fatal(err)
		}
		if strings.Contains(string(encoded), "private") {
			t.Fatalf("configuration escaped into build identity: %s", encoded)
		}
		want := map[string]string{"true": "revision-modified", "false": "revision", "unavailable": "revision-unknown"}[state]
		if info.Label() != want || (state == "unavailable") != (info.Modified == nil) {
			t.Fatalf("wrong source-state label: %+v", info)
		}
	}
}
