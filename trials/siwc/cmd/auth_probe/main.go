// Command auth_probe is an explicitly invoked SIWC account/model authorization
// check. Running it without --authorize is offline and never opens a browser.
package main

import (
	"context"
	"flag"
	"fmt"
	"os"
	"os/signal"
	"time"

	"github.com/halbritt/cairn/trials/siwc"
)

func main() {
	var authorize, entitlement bool
	var alias, bindingPath, removeAlias string
	flag.BoolVar(&authorize, "authorize", false, "explicitly start one browser authorization and account model-list check")
	flag.BoolVar(&entitlement, "probe-entitlement", false, "after authorization, make one bounded no-tool gpt-6-luna Responses request")
	flag.StringVar(&alias, "alias", "", "owner-chosen account label (lowercase letters, digits, _ or -)")
	flag.StringVar(&bindingPath, "binding-file", "", "owner-only metadata file (default: XDG config or ~/.config/cairn/siwc/bindings.json)")
	flag.StringVar(&removeAlias, "remove-alias", "", "offline: remove one saved alias so it can be explicitly re-enrolled")
	flag.Parse()
	if flag.NArg() != 0 {
		fatal("unexpected positional argument")
	}
	if removeAlias != "" {
		if authorize || entitlement || alias != "" {
			fatal("--remove-alias cannot be combined with authorization options")
		}
		if err := siwc.RemoveAccountBinding(bindingPath, removeAlias); err != nil {
			fatal(err.Error())
		}
		fmt.Printf("Removed SIWC account alias %s. The stable host ID and other aliases remain.\n", removeAlias)
		return
	}
	if !authorize {
		if entitlement {
			fatal("--probe-entitlement requires --authorize")
		}
		fmt.Fprintln(os.Stdout, "Offline mode: no OAuth, browser, or provider request. Pass --authorize to begin a consent flow.")
		return
	}
	if alias == "" {
		fatal("--alias is required with --authorize")
	}
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt)
	defer stop()
	ctx, cancel := context.WithTimeout(ctx, 5*time.Minute)
	defer cancel()
	result, err := siwc.RunAuthorizationProbe(ctx, siwc.AuthProbeOptions{
		Alias: alias, BindingPath: bindingPath, Authorize: authorize, ProbeEntitlement: entitlement,
	})
	if err != nil {
		fatal(err.Error())
	}
	fmt.Printf("SIWC account probe passed: alias=%s subject_fp=%s client_fp=%s model=%s listed=true\n",
		result.Alias, siwc.Fingerprint(result.Subject), siwc.Fingerprint(result.ClientID), siwc.AuthProbeModel)
	if result.EntitlementProbe {
		fmt.Printf("One bounded inference completed; usage input=%d output=%d total=%d. This confirms one request only, not general capacity.\n",
			result.Usage.InputTokens, result.Usage.OutputTokens, result.Usage.TotalTokens)
	}
}

func fatal(message string) {
	fmt.Fprintln(os.Stderr, message)
	os.Exit(2)
}
