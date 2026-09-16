package main

import (
	"context"
	"fmt"
	"io"
	"time"

	"github.com/halbritt/cairn/core"
)

func serveSchedules(ctx context.Context, store *core.Store, args []string, log io.Writer) (any, error) {
	f := flags("schedule-serve")
	repo := f.String("repo", "", "collection repository")
	if err := f.Parse(args); err != nil {
		return nil, err
	}
	if f.NArg() != 0 || *repo == "" {
		return nil, invalid("schedule-serve requires --repo and no positional arguments")
	}
	ready := false
	for ctx.Err() == nil {
		tickCtx, cancel := context.WithTimeout(ctx, 20*time.Second)
		_, err := store.SweepRequestControls(tickCtx, core.RequestControlSweep{Repo: *repo})
		if err != nil {
			cancel()
			if ctx.Err() != nil {
				return nil, nil
			}
			return nil, err
		}
		result, err := store.TickSchedules(tickCtx, core.ScheduleTickRequest{Repo: *repo, Limit: 100})
		cancel()
		if err != nil {
			if ctx.Err() != nil {
				return nil, nil
			}
			return nil, err
		}
		if !ready {
			fmt.Fprintln(log, "scheduler ready")
			ready = true
		}
		for _, item := range result.Occurrences {
			fmt.Fprintf(log, "schedule occurrence=%s state=%s code=%s\n", item.OccurrenceID, item.State, item.Code)
		}
		timer := time.NewTimer(time.Second)
		select {
		case <-ctx.Done():
			timer.Stop()
			return nil, nil
		case <-timer.C:
		}
	}
	return nil, nil
}
