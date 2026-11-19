package main

import (
	"context"
	"fmt"
	"io"
	"time"

	"github.com/halbritt/cairn/core"
)

type ScheduleServeOnceResult struct {
	Repo                         string `json:"repo"`
	SweptTimeouts                int64  `json:"swept_timeouts"`
	PublishedNotices             int64  `json:"published_notices"`
	ScheduleOccurrences          int    `json:"schedule_occurrences"`
	DeferredSchedules            int    `json:"deferred_schedules,omitempty"`
	OverdueNotices               int    `json:"overdue_notices,omitempty"`
	CoalescedOverdue             int    `json:"coalesced_overdue,omitempty"`
	UnrecordedRefusalDiagnostics int    `json:"unrecorded_refusal_diagnostics,omitempty"`
	DiagnosticWarning            string `json:"diagnostic_warning,omitempty"`
}

func serveSchedules(ctx context.Context, store *core.Store, args []string, log io.Writer) (any, error) {
	f := flags("schedule-serve")
	repo := f.String("repo", "", "collection repository")
	once := f.Bool("once", false, "run one complete scheduler tick and exit")
	overdueGrace := f.Int("overdue-grace-seconds", core.DefaultOverdueGraceSeconds, "grace period before overdue checkpoint or expect_by notice")
	if err := f.Parse(args); err != nil {
		return nil, err
	}
	if f.NArg() != 0 || *repo == "" {
		return nil, invalid("schedule-serve requires --repo and no positional arguments")
	}
	if *overdueGrace < 0 || *overdueGrace > core.MaxOverdueGraceSeconds {
		return nil, invalid("--overdue-grace-seconds must be 0-86400")
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
		_, err = store.SweepNoticeExpiries(tickCtx, core.NoticeExpirySweepRequest{Repo: *repo})
		if err != nil {
			cancel()
			if ctx.Err() != nil {
				return nil, nil
			}
			return nil, err
		}
		result, err := store.TickSchedules(tickCtx, core.ScheduleTickRequest{Repo: *repo, Limit: 100})
		if err != nil {
			cancel()
			if ctx.Err() != nil {
				return nil, nil
			}
			return nil, err
		}
		sweptTimeouts, err := store.SweepWaitTimeouts(tickCtx, *repo)
		if err != nil {
			cancel()
			if ctx.Err() != nil {
				return nil, nil
			}
			return nil, err
		}
		publishedNotices, err := store.DrainWaitOutbox(tickCtx, *repo, 50)
		if err != nil {
			cancel()
			if ctx.Err() != nil {
				return nil, nil
			}
			return nil, err
		}
		graceVal := *overdueGrace
		overdueRes, err := store.SweepOverdueDeadlines(tickCtx, core.OverdueSweepRequest{Repo: *repo, GraceSeconds: &graceVal})
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
		unrecordedDiag := result.UnrecordedRefusalDiagnostics + overdueRes.UnrecordedRefusalDiagnostics
		diagWarn := result.DiagnosticWarning
		if diagWarn == "" {
			diagWarn = overdueRes.DiagnosticWarning
		}
		if unrecordedDiag > 0 {
			fmt.Fprintf(log, "schedule refusal_diagnostics_unrecorded=%d warning=%q\n", unrecordedDiag, diagWarn)
		}
		if *once {
			return ScheduleServeOnceResult{
				Repo:                         *repo,
				SweptTimeouts:                sweptTimeouts,
				PublishedNotices:             publishedNotices,
				ScheduleOccurrences:          len(result.Occurrences),
				DeferredSchedules:            result.Deferred,
				OverdueNotices:               overdueRes.EmittedNotices,
				CoalescedOverdue:             overdueRes.CoalescedItems,
				UnrecordedRefusalDiagnostics: unrecordedDiag,
				DiagnosticWarning:            diagWarn,
			}, nil
		}
		for _, item := range result.Occurrences {
			fmt.Fprintf(log, "schedule occurrence=%s state=%s code=%s\n", item.OccurrenceID, item.State, item.Code)
		}
		if sweptTimeouts > 0 {
			fmt.Fprintf(log, "wait timeouts swept=%d\n", sweptTimeouts)
		}
		if publishedNotices > 0 {
			fmt.Fprintf(log, "wait outbox published=%d\n", publishedNotices)
		}
		if overdueRes.EmittedNotices > 0 || overdueRes.CoalescedItems > 0 {
			fmt.Fprintf(log, "overdue notices published=%d coalesced=%d\n", overdueRes.EmittedNotices, overdueRes.CoalescedItems)
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
