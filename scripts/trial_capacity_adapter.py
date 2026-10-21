"""Offline capacity evidence adapter for the original weekly paired plan (CAIRN-107).

Turns already-collected account, absolute-allowance, reservation and quote
observations into the existing pinned ``cairn.paired-capacity-evidence/2`` and
``cairn.provider-account-quotes/1`` documents. It then validates them with the
existing ``capacity_check`` and ``project_provider_mapping``.

The existing helpers do all the demand arithmetic. ``provider_workload`` and
``provider_demands`` charge every job's full provider-request and token
counters. That covers actor, grader, selector, control, retry, dispute and
auxiliary (including memory) jobs, each charged once to every pool window
its route names. A pool that several jobs or accounts share is summed once
per window and metric. This module only checks that the observations bind the
exact accounts, pool, window and counter that the routes charge.

What this is not:

- Usage percentages and ``dedicated``/``interactive_use`` booleans are
  declarations, not absolute capacity, authorization or funded-credit proof.
  Percentages are refused; the booleans are carried through and the receipt
  marks account proof as declared and unauthenticated.
- It never fetches accounts, reads credentials, purchases capacity, calls a
  provider or model, or admits a runtime operation.
"""
import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import sys

import trial_paired_request as paired
import trial_provider_spend as spend
from trial_provider_mapping import project_provider_mapping

INPUT_SCHEMA = 'cairn.capacity-evidence-input/1'
RECEIPT_SCHEMA = 'cairn.capacity-adapter-receipt/1'
need = paired.need


def _timestamp(value, code):
    try:
        return spend.timestamp(value)
    except spend.SpendRefusal:
        raise paired.Refusal(code) from None


def _route_accounts(routes):
    """(pool_id, window_id) -> accounts whose routed jobs draw on that window."""
    owners = {}
    for route in routes['routes'].values():
        for window in route['windows']:
            owners.setdefault((window['pool_id'], window['window_id']), set()).add(route['account_id'])
    return owners


def _resources(allowances, totals, owners, now):
    need(type(allowances) is list and 0 < len(allowances) <= 1024, 'allowance_shape')
    resources, observed, until = {}, [], []
    for row in allowances:
        paired.shape(row, 'accounts pool_id window_id metric unit remaining observed_at valid_until basis',
                     'allowance_shape')
        need(row['unit'] == 'absolute', 'allowance_not_absolute')
        need(row['metric'] in paired.CAPACITY_METRICS and paired.capacity_integer(row['remaining']),
             'allowance_unknown_counter')
        key = (row['pool_id'], row['window_id'], row['metric'])
        need(key in totals, 'allowance_unmapped')
        need(key not in resources, 'allowance_double_counted')
        need(type(row['accounts']) is list and len(set(row['accounts'])) == len(row['accounts'])
             and set(row['accounts']) == owners[key[:2]], 'allowance_account_mismatch')
        start = _timestamp(row['observed_at'], 'allowance_stale')
        end = _timestamp(row['valid_until'], 'allowance_stale')
        need(start <= now < end, 'allowance_stale')
        paired.pin(row['basis'])
        resources[key] = dict(pool_id=key[0], window_id=key[1], metric=key[2],
                              remaining=row['remaining'], required=totals[key])
        observed.append((start, row['observed_at']))
        until.append((end, row['valid_until']))
    need(set(resources) == set(totals), 'allowance_missing')
    return [resources[key] for key in sorted(resources)], max(observed)[1], min(until)[1]


class _Output:
    """Exclusive new directory; removed with its files if adaptation refuses."""

    def __init__(self, directory):
        self.directory = Path(directory).absolute()
        need(not self.directory.exists() and not self.directory.is_symlink(), 'output_exists')
        self.directory.mkdir(mode=0o700)
        self.written = []

    def write(self, name, document):
        path = self.directory / name
        content = json.dumps(document, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False).encode()
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        self.written.append(path)
        with os.fdopen(descriptor, 'wb') as target:
            target.write(content)
            target.flush()
            os.fsync(target.fileno())
        return dict(path=str(path), sha256=hashlib.sha256(content).hexdigest())

    def discard(self):
        for path in self.written:
            path.unlink(missing_ok=True)
        self.directory.rmdir()


def _adapt(document, output, now):
    paired.shape(document, 'schema plan models accounts routes allowances reservation '
                 'spend_request spend_binding prices quotes', 'adapter_input_shape')
    need(document['schema'] == INPUT_SCHEMA, 'adapter_input_schema')
    plan, models = document['plan'], document['models']
    current = _timestamp(now, 'adapter_now')
    accounts_ref = output.write('accounts.json', document['accounts'])
    routes_ref = output.write('routes.json', document['routes'])
    jobs = paired.provider_workload(plan, models)
    accounts = paired.capacity_accounts(accounts_ref)
    totals = paired.provider_demands(routes_ref, plan, jobs, accounts)
    resources, observed_at, valid_until = _resources(
        document['allowances'], totals, _route_accounts(document['routes']), current)
    hold = document['reservation']
    paired.shape(hold, 'state valid_until basis', 'reservation_shape')
    paired.pin(hold['basis'])
    reservation_ref = output.write('reservation.json', dict(
        schema='cairn.paired-reservation/2', plan_sha256=plan['sha256'],
        resources_sha256=paired.canonical_sha(resources), state=hold['state'], valid_until=hold['valid_until']))
    capacity_ref = output.write('capacity.json', dict(
        schema='cairn.paired-capacity-evidence/2', plan_sha256=plan['sha256'], state='declared',
        observed_at=observed_at, valid_until=valid_until, account_binding=accounts_ref,
        route_binding=routes_ref, reservation=reservation_ref, resources=resources))
    mapping_ref = output.write('mapping.json', dict(
        schema='cairn.provider-account-quotes/1', plan_sha256=plan['sha256'],
        capacity_sha256=capacity_ref['sha256'], spend_request_sha256=document['spend_request']['sha256'],
        observed_at=observed_at, valid_until=valid_until, coverage='complete', unlisted_routes='forbidden',
        spend_binding=document['spend_binding'], prices=document['prices'], quotes=document['quotes']))
    try:
        projection = project_provider_mapping(plan, models, capacity_ref, document['spend_request'],
                                              mapping_ref, now=now)
    except spend.SpendRefusal as refusal:
        raise paired.Refusal(str(refusal)) from None
    receipt = dict(schema=RECEIPT_SCHEMA, plan_sha256=plan['sha256'], adapted_at=now,
                   account_binding=accounts_ref, route_binding=routes_ref, reservation=reservation_ref,
                   capacity=capacity_ref, mapping=mapping_ref, projection=projection,
                   account_proof='declared_unauthenticated', runtime_admission=False,
                   limits='Offline declarations checked for consistency and whole-run sufficiency; '
                          'not account, authorization, funding or provider-side proof.')
    output.write('receipt.json', receipt)
    return receipt


def adapt(document, output_directory, *, now):
    """Write pinned capacity/2 and quote mapping for ``document``; refuse with a fixed code."""
    output = _Output(output_directory)
    try:
        return _adapt(document, output, now)
    except BaseException:
        output.discard()
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--input', nargs=2, metavar=('PATH', 'SHA256'), required=True,
                        help=f'pinned {INPUT_SCHEMA} document')
    parser.add_argument('--output', required=True, help='absent directory for the adapted evidence')
    parser.add_argument('--now', help='UTC YYYY-MM-DDTHH:MM:SSZ (default: current time)')
    args = parser.parse_args(argv)
    now = args.now or dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    try:
        document = paired.read_json_pin(dict(path=str(Path(args.input[0]).absolute()), sha256=args.input[1]))
        receipt = adapt(document, args.output, now=now)
    except (paired.Refusal, OSError) as error:
        code = str(error) if isinstance(error, paired.Refusal) else 'evidence_unreadable'
        print(json.dumps(dict(schema=RECEIPT_SCHEMA, status='refused', code=code, runtime_admission=False)))
        return 1
    print(json.dumps(dict(status='adapted', capacity=receipt['capacity'], mapping=receipt['mapping'],
                          runtime_admission=False), sort_keys=True))
    return 0


if __name__ == '__main__':
    sys.exit(main())
