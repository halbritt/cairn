# Semantic retrieval across an idle gap

A follow-up search after 35 idle seconds took 0.063 seconds with a five-minute
worker lifetime, compared with 24.851 seconds under the existing 30-second
lifetime. Both returned the same complete candidate-score digest, sealed context
and selected source versions. The worker used about 220 MiB resident memory.
This is a reduction in observed retrieval cost, not evidence of improved answers
or completed coding tasks.

## Change and operating choice

Cairn `72e7c243ae211acc3c04b4520e5bb4d5676200e6` adds
`serve --semantic-idle-timeout DURATION` for an explicitly configured streaming
worker. The default remains 30 seconds. Nonpositive values and the option without
streaming mode refuse. The library exposes the same positive duration through
`StreamCommandWithIdleTimeout`; the existing constructor keeps its default.

The API retains the same single lazy worker, request deadline, cancellation and
shutdown paths. Only idle lifetime changes. The existing cache remains limited
to body hashes and vectors from the last successful eligible request; eligibility
runs before every score. There is no persistent cache, extra process, model,
thread-count, ranking or schema change.

This host now uses five minutes. Longer residency avoids repeated embedding when
unchanged notes recur within that interval, at the cost of retaining model memory
and vectors longer. Cold work remains expensive, different eligible sets can miss
the cache, and idle release still discards it. The previous binary and service
configuration are retained privately for reversal. Other harness settings,
identities, worker/model files and PostgreSQL were preserved.

## Actual API comparison

The fixed plan used the current hosted corpus and the actual investigation query
`retrieval query long notes`. Each condition made two identical ordinary CLI/API
searches with a 35-second pause after the first response. Timing includes CLI,
API, database and scoring work; process/RSS inspection occurs outside that timer.

| Condition | First request | Follow-up | Worker at follow-up |
| --- | ---: | ---: | --- |
| Installed `a848e3c`, 30-second idle | 20.696 s | 24.851 s | New child; previous child absent |
| Installed `72e7c24`, five-minute idle | 20.951 s | 0.063 s | Same child and vector cache |

All four calls returned the same sealed context and discovery metadata, including
the digest of every candidate score. Candidate RSS was 225,288 and 225,292 KiB.
The predeclared target was a follow-up below half the baseline wall time with
matching results and RSS below 300 MiB; this case met it.

These are sequential observations on a shared host, including development work,
not independent replicated trials or latency percentiles. The cold values vary;
no cold speedup is claimed. The earlier inference profile supports the mechanism
but predates the current worker-note curation and is not this exact input's
profile. The current probe directly observes the baseline child disappearing
and the candidate child surviving. It does not establish a representative user
traffic distribution or net task benefit.

## Verification and retained evidence

The public configurable idle-release test first failed without the entry point,
then passed actual child expiry/restart. Library duration validation and CLI mode
validation have before/after failures. Disposable PostgreSQL/race integration
passed, followed by the complete affected-package race suites and static checks
after the final CLI `INVALID_REQUEST` validation was added.

Clean `72e7c24` is installed as both CLI and API, binary SHA-256
`c046c983a48c04918a8c9ed8d102143faef8d0a917291b9985aeb0c6bbc21911`.
The API PID after installation was 2934824; PostgreSQL remained PID 163669 and
migration 033. The old and new service configuration hashes are in the metadata.
The existing source and model fingerprint stayed fixed throughout the comparison. After the last
semantic response, one-second process polling found the same worker alive at
299.872 seconds and absent at 300.872 seconds, measured from report-file time.
No semantic requests reset its timer during that observation. Feature CI run
34505340594 passed all checks.

Current worker guidance advanced v8→v9 to describe the host setting and preserved
default. Exact edit retry, fresh current pull, metadata preservation and the full
retained v8 body passed through MCP. All timings above precede that note edit.
Raw operational responses and note bodies remain outside Git under
`/tmp/cairn-semantic-idle-20260910/`.

[Verification metadata](semantic-idle-2026-09-10.json) retains source/build pins,
timings, result digests, check logs and the guidance revision. Validated doctrine
packet `pkt-77bf59d0753a1c94` informed bounded measurement, behavior preservation
and the no-change comparison. The typed decision receipt and citation closures
are retained privately. Five residual obligations concern broader repetition,
representativeness, environmental pinning and co-change; they remain outside
this observed-operation claim. No answering-model task was run.
