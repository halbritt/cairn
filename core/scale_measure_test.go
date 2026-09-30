package core

// CAIRN-116 search scale measurement (agent-235, 2026-09-30). Runs only with
// CAIRN_SCALE_MEASURE=1 against a disposable cluster; procedure frozen in
// docs/verification/search-scale-2026-09-30.md.

import (
	"context"
	"encoding/json"
	"fmt"
	"math/rand/v2"
	"os"
	"runtime/pprof"
	"slices"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"
)

var scaleCommon = strings.Fields(`retry backoff scheduler queue worker lease timeout deploy release rollback migration
schema postgres index search ranking preview budget receipt handle pull expand token socket api hook session inbox
delivery notice request response agent codex claude opencode hermes surveyor audit report finding coverage config
binary build test integration check lint format commit branch worktree merge rebase push remote origin main review
approval owner decision lesson procedure observation claim evidence citation source revision version currentness
eligibility scope project repository task run workspace file symbol entity literal quoted semantic embedding model
vector cosine threshold latency throughput memory cache disk gpu cpu process service systemd restart reload journal
log error failure exit status refusal integrity seal hash digest body summary span offset length bytes utf8 escape
json cbor openapi contract compatibility legacy replay recompile historical cohort weight frequency idf lexical
tokenizer term query room optional mandatory instruction conflict advisory policy grant authority retention delete
forget purge backup restore fence generation watcher presence heartbeat wake channel queue listener native turn
prompt stop continuation completion acknowledgement journal intent replay settle reconcile hold lease expire renew
cleanup stale orphan socket permission ownership sandbox fixture disposable cluster database transaction serializable
retry conflict deadlock chunk batch scan limit bounded window page cursor offset next previous first last newest
oldest ordering stable deterministic seed random sample median percentile benchmark profile flame allocation garbage
the a to of and in is for on with that this it as be are was by not do from at or but if when then must should never
always only after before during while because however instead unless except without between within across through`)

func scaleBody(r *rand.Rand, zipf *rand.Zipf, i int) string {
	var target int
	switch p := r.IntN(100); {
	case p < 40:
		target = 200 + r.IntN(400)
	case p < 75:
		target = 600 + r.IntN(900)
	case p < 95:
		target = 1500 + r.IntN(2500)
	default:
		target = 4000 + r.IntN(8000)
	}
	var b strings.Builder
	fmt.Fprintf(&b, "scale-project: generated note %05d\n\nProject: /work/scale. ", i)
	for words := 0; b.Len() < target; words++ {
		k := int(zipf.Uint64())
		if k < len(scaleCommon) {
			b.WriteString(scaleCommon[k])
		} else {
			fmt.Fprintf(&b, "w%04d", k-len(scaleCommon))
		}
		if words%14 == 13 {
			b.WriteString(". ")
		} else {
			b.WriteByte(' ')
		}
	}
	return b.String()
}

type scaleSample struct {
	Notes      int                  `json:"notes"`
	CreateSec  float64              `json:"create_seconds_increment"`
	BodyBytes  map[string]int       `json:"body_bytes"`
	Overall    map[string]float64   `json:"overall_ms"`
	PerQuery   map[string][]float64 `json:"per_query_ms"`
	Returned   map[string]int       `json:"returned_index_entries"`
	IDFN       map[string]int       `json:"idf_n"`
	Measured   int                  `json:"measured_searches"`
	Warmups    int                  `json:"warmup_searches"`
	Incomplete string               `json:"incomplete,omitempty"`
}

func pct(xs []float64, p float64) float64 {
	s := slices.Clone(xs)
	slices.Sort(s)
	i := int(p*float64(len(s)-1) + 0.5)
	return s[i]
}

func TestScaleMeasure(t *testing.T) {
	if os.Getenv("CAIRN_SCALE_MEASURE") != "1" {
		t.Skip("set CAIRN_SCALE_MEASURE=1 with a disposable CAIRN_TEST_DATABASE_URL")
	}
	out := os.Getenv("CAIRN_SCALE_OUT")
	ctx := context.Background()
	s := testStore(t, Channel{Principal: "scale-agent235"})
	repo := "scale:" + uuid.NewString()
	r := rand.New(rand.NewPCG(235, 116))
	zipf := rand.NewZipf(r, 1.1, 1, uint64(len(scaleCommon)+3000-1))
	var long []string
	for i := 0; len(long) < 48; i += 7 {
		long = append(long, scaleCommon[i%len(scaleCommon)])
	}
	queries := map[string]string{
		"common":  "retry backoff scheduler",
		"task":    "postgres migration rollback procedure before deploy",
		"rare":    "w2871 w1450 w0999",
		"literal": `"bounded window" scheduler lease`,
		"hook48":  strings.Join(long, " "),
		"nomatch": "zzqx nothingmatches",
	}
	names := []string{"common", "task", "rare", "literal", "hook48", "nomatch"}
	var results []scaleSample
	created := 0
	var all []int
	deadline := time.Now().Add(13 * time.Minute)
	for _, size := range []int{1000, 5000, 10000} {
		start := time.Now()
		for created < size {
			if time.Now().After(deadline) {
				results = append(results, scaleSample{Notes: size, Incomplete: fmt.Sprintf("time bound reached after creating %d notes", created)})
				goto done
			}
			kinds := []string{"note", "lesson", "procedure", "decision"}
			d := Draft{Kind: kinds[created%4], Body: scaleBody(r, zipf, created), Sensitivity: "shareable", Scope: Scope{repo, "*", "*"}, ClaimType: "self"}
			all = append(all, len(d.Body))
			if _, err := s.Create(ctx, CreateRequest{RequestID: uuid.NewString(), Draft: d}); err != nil {
				t.Fatal(err)
			}
			created++
		}
		sample := scaleSample{Notes: size, CreateSec: time.Since(start).Seconds(), PerQuery: map[string][]float64{}, Returned: map[string]int{}, IDFN: map[string]int{}}
		fs := make([]float64, len(all))
		for i, v := range all {
			fs[i] = float64(v)
		}
		sample.BodyBytes = map[string]int{"min": int(pct(fs, 0)), "median": int(pct(fs, 0.5)), "p95": int(pct(fs, 0.95)), "max": int(pct(fs, 1))}
		if size == 10000 && out != "" {
			f, err := os.Create(out + "/cpu-10000.pprof")
			if err != nil {
				t.Fatal(err)
			}
			defer f.Close()
			if err = pprof.StartCPUProfile(f); err != nil {
				t.Fatal(err)
			}
		}
		var overall []float64
		for _, name := range names {
			for run := 0; run < 6; run++ {
				req := CompileRequest{RequestID: uuid.NewString(), Scope: Scope{repo, "task", "run"}, Query: queries[name], Purpose: "context", AvailableTokens: 8000}
				t0 := time.Now()
				res, err := s.Index(ctx, req, Destination{Name: "hosted"})
				ms := float64(time.Since(t0).Microseconds()) / 1000
				if err != nil {
					t.Fatalf("%d %s: %v", size, name, err)
				}
				if run == 0 {
					sample.Warmups++
					continue
				}
				sample.Measured++
				overall = append(overall, ms)
				sample.PerQuery[name] = append(sample.PerQuery[name], ms)
				sample.Returned[name] = len(res.Package.Semantic.Index)
				if res.Package.Semantic.IDF != nil {
					sample.IDFN[name] = res.Package.Semantic.IDF.N
				}
			}
		}
		if size == 10000 && out != "" {
			pprof.StopCPUProfile()
		}
		sample.Overall = map[string]float64{"median": pct(overall, 0.5), "p95": pct(overall, 0.95), "max": pct(overall, 1)}
		results = append(results, sample)
		b, _ := json.Marshal(sample)
		t.Log(string(b))
		if out != "" {
			data, _ := json.MarshalIndent(results, "", " ")
			os.WriteFile(out+"/results.json", data, 0o600)
		}
	}
done:
	if out != "" {
		data, _ := json.MarshalIndent(results, "", " ")
		os.WriteFile(out+"/results.json", data, 0o600)
	}
}
