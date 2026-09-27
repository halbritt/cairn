// Command notification_store_probe measures the committed Store.PublishEvent path.
// It is built and run only by bench-notification-latency.sh against its disposable DB.
package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/google/uuid"
	"github.com/halbritt/cairn/core"
)

func main() {
	samples := flag.Int("samples", 30, "measured publications")
	warmup := flag.Int("warmup", 5, "unmeasured publications")
	repo := flag.String("repo", "", "fixture collection")
	source := flag.String("source", "", "shareable source record UUID")
	receiver := flag.String("receiver", "", "fixture receiver principal")
	flag.Parse()
	root := os.Getenv("CAIRN_DISPOSABLE_BENCH_ROOT")
	dsn := os.Getenv("CAIRN_TEST_DATABASE_URL")
	if *samples < 1 || *samples > 200 || *warmup < 0 || *warmup > 50 ||
		*repo == "" || *receiver == "" || root == "" ||
		filepath.Dir(root) != "/tmp" || !strings.HasPrefix(filepath.Base(root), "cairn-notification-bench.") ||
		dsn != "host="+root+"/socket dbname=cairn_notification_bench sslmode=disable" ||
		os.Getenv("CAIRN_DATABASE_URL") != dsn {
		panic("notification probe requires its own disposable PostgreSQL fixture")
	}
	if _, err := uuid.Parse(*source); err != nil {
		panic(err)
	}
	ctx := context.Background()
	store, err := core.Open(ctx, dsn, core.Channel{Principal: "agent:bench-publisher", Repo: *repo})
	if err != nil {
		panic(err)
	}
	defer store.Close()
	encoder := json.NewEncoder(os.Stdout)
	for i := 0; i < *warmup+*samples; i++ {
		request := core.PublishEventRequest{RequestID: uuid.NewString(), Kind: "notice",
			Ref:         core.RecordVersionRef{RecordID: *source, Version: 1},
			Destination: core.EventDestination{Type: "agent", Name: *receiver}}
		start := time.Now()
		event, err := store.PublishEvent(ctx, request, core.Destination{Name: "hosted"})
		elapsed := time.Since(start)
		if err != nil || event.EventID == "" {
			panic(fmt.Sprintf("committed publication %d failed: %v", i, err))
		}
		if i >= *warmup {
			if err := encoder.Encode(map[string]any{"phase": "store_publish_commit", "ns": elapsed.Nanoseconds()}); err != nil {
				panic(err)
			}
		}
	}
}
