package main

import (
	"flag"

	"github.com/halbritt/cairn/core"
)

func entityFlags(f *flag.FlagSet) *[]core.EntityRef {
	var refs []core.EntityRef
	for _, kind := range []string{"file", "symbol"} {
		f.Func("entity-"+kind, "explicit repository-relative file or qualified symbol association; repeat up to 16 total", func(name string) error {
			next := append(refs, core.EntityRef{Kind: kind, Name: name})
			if _, err := core.NormalizeEntities(next); err != nil {
				return err
			}
			refs = next
			return nil
		})
	}
	return &refs
}
