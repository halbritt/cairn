// cairn-lifecycle is the private receipt-selected activation image. It exposes
// no store or public command dispatcher and has no flag that enables cairn's
// separately disabled public lifecycle registration.
package main

import (
	"github.com/halbritt/cairn/internal/lifecycle/activationipc"
	"os"
)

func main() { os.Exit(activationipc.Run(os.Args[1:], os.Stdin, os.Stdout, os.Stderr)) }
