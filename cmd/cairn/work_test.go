package main

import (
	"context"
	"fmt"
	"strings"
	"testing"

	"github.com/halbritt/cairn/core"
)

func TestWorkCommandRejectsRetiredServeSubcommandWithoutStore(t *testing.T) {
	t.Setenv("CAIRN_DATABASE_URL", "host=/absent-cairn-work-test dbname=missing sslmode=disable")
	ctx := context.Background()
	retired := []string{"work", "serve"}
	for _, args := range [][]string{retired, append(retired, "--help")} {
		_, err := run(ctx, args, strings.NewReader(""))
		want := fmt.Sprintf("unknown work subcommand %q", retired[1])
		if core.Code(err) != "INVALID_REQUEST" || !strings.Contains(err.Error(), want) {
			t.Fatalf("%v returned %v, want %q with INVALID_REQUEST", args, err, want)
		}
	}
	for _, args := range [][]string{{"work", "--help"}, {"--help"}} {
		out, err := run(ctx, args, strings.NewReader(""))
		help := fmt.Sprint(out)
		if err != nil || !strings.Contains(help, "work show") || strings.Contains(help, strings.Join(retired, " ")) {
			t.Fatalf("%v returned error %v and unexpected usage %q", args, err, help)
		}
	}
}
