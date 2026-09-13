package semantic

import (
	"context"
	"testing"

	"github.com/halbritt/cairn/core"
)

func TestColdWorkerCanFinishBeforeNativeClientDeadline(t *testing.T) {
	for _, streaming := range []bool{false, true} {
		name := "command"
		if streaming {
			name = "stream"
		}
		t.Run(name, func(t *testing.T) {
			t.Parallel()
			// A cold corpus can take longer than the former 20-second budget.
			// Keep this below the native client's 30-second outer deadline.
			path := script(t, `exec /usr/bin/python3 -c '
import json,sys,time
first=True
for line in sys.stdin:
 request=json.loads(line)
 if first: time.sleep(21); first=False
 result=dict(model_sha256="test",algorithm="test/1",scores=[])
 print(json.dumps(dict(id=request["id"],result=result) if "id" in request else result),flush=True)
'`)
			var rank core.SemanticRanker
			var err error
			if streaming {
				var stop func()
				rank, stop, err = StreamCommand(context.Background(), path)
				if err == nil {
					defer stop()
				}
			} else {
				rank, err = Command(path)
			}
			if err != nil {
				t.Fatal(err)
			}
			result, err := rank(context.Background(), core.SemanticRankRequest{Query: "cold corpus"})
			if err != nil || result.Algorithm != "test/1" {
				t.Fatalf("cold response was lost: %+v %v", result, err)
			}
			if streaming {
				warm, err := rank(context.Background(), core.SemanticRankRequest{Query: "warm corpus"})
				if err != nil || warm.ModelSHA256 != result.ModelSHA256 {
					t.Fatalf("completed cold worker was not reusable: %+v %v", warm, err)
				}
			}
		})
	}
}
