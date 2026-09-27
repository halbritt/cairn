// Command cairn-contract writes or checks docs/api/openapi.json from the
// localapi source. Run it from the repository root (make contract).
package main

import (
	"bytes"
	"flag"
	"fmt"
	"os"

	"github.com/halbritt/cairn/internal/apicontract"
)

func main() {
	check := flag.Bool("check", false, "fail if the committed contract differs from the source")
	output := flag.String("o", "docs/api/openapi.json", "contract path")
	source := flag.String("source", "localapi", "localapi package directory")
	flag.Parse()
	generated, err := apicontract.Generate(*source)
	if err != nil {
		fmt.Fprintln(os.Stderr, "cairn-contract:", err)
		os.Exit(1)
	}
	if *check {
		committed, err := os.ReadFile(*output)
		if err != nil || !bytes.Equal(committed, generated) {
			fmt.Fprintf(os.Stderr, "cairn-contract: %s is stale; run make contract and review the diff\n", *output)
			os.Exit(1)
		}
		return
	}
	if err = os.WriteFile(*output, generated, 0644); err != nil {
		fmt.Fprintln(os.Stderr, "cairn-contract:", err)
		os.Exit(1)
	}
}
