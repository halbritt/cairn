"""Opt-in comparison of real prepared-model scoring; no database or model download."""
import argparse
import hashlib
import importlib.util
import json
import time
from pathlib import Path


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', required=True, type=Path)
    parser.add_argument('--model-dir', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    args.output.mkdir(mode=0o700)
    source = Path(__file__).with_name('semantic_rank.py')
    workload_path = source.parent.parent / 'core/testdata/retrieval-quality.json'
    workload = json.loads(workload_path.read_text())
    notes = [dict(record_id=n['id'], version=1, body=n['body'], body_sha256=n['sha256'])
             for n in workload['notes']]
    queries = [q['query'] for q in workload['queries'][:6]]
    plan = dict(schema='cairn.semantic-cache-comparison/1',
                baseline_sha256=hashlib.sha256(args.baseline.read_bytes()).hexdigest(),
                candidate_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                workload_sha256=hashlib.sha256(workload_path.read_bytes()).hexdigest(),
                notes=len(notes), queries=queries, target='warm repeated scoring below half baseline wall time',
                comparison='same initialized model, identical requests, alternating execution order',
                checks=['complete response equality', 'changed body', 'removed/reintroduced note',
                        'reordered input', 'duplicate body', 'long-note chunk reuse'],
                metric='time.monotonic seconds per synchronous score call, after model initialization',
                limits='development corpus; six dependent repeated pairs; no percentile or task-value claim')
    (args.output / 'plan.json').write_text(json.dumps(plan, indent=2)+'\n')
    modules = [load(args.baseline, 'cache_baseline'), load(source, 'cache_candidate')]
    scorers = [m.Scorer(args.model_dir) for m in modules]
    measurements = []

    def compare(label, request):
        for module in modules:
            module.validate_request(request)
        outputs, times = {}, {}
        order = [0, 1] if len(measurements) % 2 == 0 else [1, 0]
        for which in order:
            start = time.monotonic()
            outputs[which] = scorers[which].score(request)
            times[which] = time.monotonic() - start
        (args.output / (label + '-responses.json')).write_text(json.dumps(outputs, indent=2)+'\n')
        assert outputs[0] == outputs[1], label + ': complete scoring response changed'
        row = dict(label=label, baseline_seconds=times[0], candidate_seconds=times[1],
                   response_sha256=hashlib.sha256(json.dumps(outputs[0], sort_keys=True).encode()).hexdigest(),
                   order=order, retained_vector_bytes=sum(v.nbytes for v in scorers[1].chunk_vectors.values()))
        assert row['retained_vector_bytes'] <= 128 * 384 * 8
        measurements.append(row)
        (args.output / 'measurements.json').write_text(json.dumps(measurements, indent=2)+'\n')
        print(json.dumps(row), flush=True)

    compare('cold', dict(query=queries[0], notes=notes))
    for i, query in enumerate(queries):
        compare('warm-'+str(i), dict(query=query, notes=notes))
    edited = [dict(n) for n in notes]
    edited[0]['body'] += '\nAn explicit updated verification step.'
    edited[0]['body_sha256'] = hashlib.sha256(edited[0]['body'].encode()).hexdigest()
    edited[0]['version'] += 1
    compare('changed', dict(query=queries[0], notes=edited))
    compare('removed', dict(query=queries[1], notes=edited[1:]))
    compare('reintroduced-reordered', dict(query=queries[2], notes=list(reversed(edited))))
    compare('duplicate', dict(query=queries[3], notes=edited + [dict(edited[0], record_id='copy')]))
    long = 'Routine service administration follows the selected runbook. ' * 240
    long += '\nThe zephyr service credential is stored at /run/zephyr/access-key.'
    long_note = dict(record_id='long', version=1, body=long,
                     body_sha256=hashlib.sha256(long.encode()).hexdigest())
    compare('long-cold', dict(query='zephyr credential location', notes=[long_note]))
    compare('long-warm', dict(query='where is the service credential?', notes=[long_note]))
    warm = [r for r in measurements if r['label'].startswith('warm-')]
    assert all(r['candidate_seconds'] < r['baseline_seconds'] / 2 for r in warm), 'warm target not met'
    (args.output / 'summary.json').write_text(json.dumps(dict(
        model_sha256=scorers[1].model_hash, comparisons=len(measurements), complete_responses_equal=True,
        warm_target_met=True, max_retained_vector_bytes=max(r['retained_vector_bytes'] for r in measurements)), indent=2)+'\n')


if __name__ == '__main__':
    main()
