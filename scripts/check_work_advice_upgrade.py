#!/usr/bin/env python3
"""Actual first-four schema078 -> combined079 upgrade, using owned PostgreSQL.

Explicit immutable sources/build receipts/binaries only. No
build, production DB, provider generation, SQL mutation or fallback route.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import uuid
from datetime import datetime, timezone

sys.dont_write_bytecode = True
OLD_COMMIT = '42de49de8345754a7e9da72018c1ee661ff0b9e4'


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for version in ('old', 'new'):
        parser.add_argument('--' + version + '-source', type=Path, required=True)
        parser.add_argument('--' + version + '-capture-manifest', type=Path, required=True)
        parser.add_argument('--' + version + '-binary', type=Path, required=True)
        parser.add_argument('--' + version + '-binary-report', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    old_source, new_source = args.old_source.resolve(), args.new_source.resolve()
    sys.path.insert(0, str(new_source / 'scripts'))
    import disposable_host as hostlib
    import work_coordination_ui_fixture as ui
    require = hostlib.require
    for module in (hostlib, ui):
        require(Path(module.__file__).resolve().parent == new_source / 'scripts', 'fixture helper escaped captured candidate')
    inputs = {}
    for label in ('old', 'new'):
        source = getattr(args, label + '_source').resolve()
        capture = getattr(args, label + '_capture_manifest').resolve()
        binary = getattr(args, label + '_binary').resolve()
        receipt = getattr(args, label + '_binary_report').resolve()
        identity = ui.source_identity(source, capture)
        build = json.loads(receipt.read_text())
        require(build.get('schema') == 'cairn.physical-restore-build/1'
                and build.get('source_input_sha256') == identity['input_sha256']
                and build.get('source_commit') == identity['revision']
                and build.get('source_unchanged') is True and build.get('build_returncode') == 0
                and build.get('binary_sha256') == digest(binary), label + ' build receipt does not bind exact source/binary')
        migrations = sorted((source / 'core/schema').glob('*.sql'))
        count = 78 if label == 'old' else 79
        require(len(migrations) == count and [int(p.name[:3]) for p in migrations] == list(range(1, count + 1)),
                label + ' source does not contain the exact contiguous migration range')
        inputs[label] = dict(source=str(source), source_identity=identity, capture=str(capture), binary=str(binary),
            binary_sha256=digest(binary), binary_report=str(receipt), binary_report_sha256=digest(receipt),
            migrations=[dict(version=i + 1, name=p.name, sha256=digest(p)) for i, p in enumerate(migrations)])
    require(inputs['old']['source_identity']['revision'] == OLD_COMMIT, 'old candidate is not exact landed first-four42de49d')
    require(inputs['old']['migrations'] == inputs['new']['migrations'][:78], 'new source changed an already-shipped migration')
    output = args.output.resolve()
    require(not output.is_relative_to(old_source) and not output.is_relative_to(new_source)
            and not output.is_relative_to(Path(__file__).resolve().parent), 'output must be outside captured sources/fixture packet')
    require(not output.exists(), 'upgrade output must be fresh')
    output.mkdir(mode=0o700, parents=True)
    fixture_sha = digest(__file__)
    report = dict(schema='cairn.advice-078-079-upgrade-proof/1', status='running', inputs=inputs,
        fixture_sha256=fixture_sha, provider_called=False, production_database_used=False,
        cluster_cleaned=False, stages=[])
    root = Path(tempfile.mkdtemp(prefix='cairn-upgrade-078-079-'))
    report['disposable_root'] = str(root)
    private_home = root / 'private-home'; private_home.mkdir(mode=0o700)
    safe_env = dict(PATH=os.environ.get('PATH', '/usr/bin:/bin'), LANG='C.UTF-8', TZ='UTC',
        HOME=str(private_home), XDG_CONFIG_HOME=str(private_home / '.config'), TMPDIR=str(root))
    if os.environ.get('CAIRN_PG_BIN'):
        safe_env['CAIRN_PG_BIN'] = os.environ['CAIRN_PG_BIN']
    os.environ.clear(); os.environ.update(safe_env)
    cluster, host, original_failure = hostlib.OwnedCluster(root), None, None
    failures = []
    try:
        cluster = hostlib.OwnedCluster.start(root)
        # New helper owns setup/cleanup, but all initialization/migration and
        # application state below comes from the exact old78 executable.
        host = hostlib.DisposableHost.start(cluster, binary=Path(inputs['old']['binary']),
            repo='fixture:advice-upgrade:' + str(uuid.uuid4()), database='advice_upgrade',
            label='advice_upgrade', serve=False)
        plane_home = host.home / 'synthetic-plane'; plane_home.mkdir(mode=0o700)
        key, config, state = plane_home / 'key', plane_home / 'config.toml', plane_home / 'state.json'
        key.write_text('synthetic-fixture-key-never-sent\n'); key.chmod(0o600)
        config.write_text(f'default_instance = "work-ui"\n[instance.work-ui]\nbase_url = "{ui.ORIGIN}"\nworkspace = "work-ui"\napi_key_file = "{key}"\n')
        config.chmod(0o600)
        host.env.update(PLANE_CONFIG=str(config), CAIRN_PLANE_BIN=str(old_source / 'tests/fixtures/work_ui/plane.py'),
                        CAIRN_WORK_UI_PLANE_STATE=str(state))
        workspace, project, item, state_id = [str(uuid.uuid4()) for _ in range(4)]
        title, progress_text, rationale = 'Retained078 work item', 'Retained078 operator progress', 'Retained078 exact report rationale'
        ui.dump(state, dict(origin=ui.ORIGIN, workspace=workspace, projects={project: dict(
            name='Synthetic078 upgrade project', identifier='UPG', mode='normal', items=[dict(
                id=item, sequence_id=1, name=title, workspace=workspace, project=project,
                state=dict(id=state_id, name='Started', group='started'), assignees=[],
                updated_at=datetime.now(timezone.utc).isoformat(), project_detail=dict(id=project, identifier='UPG'))])}))

        def command(label, *argv, expected='OK', timeout=15):
            return hostlib.run_envelope([inputs[label]['binary'], *argv], env=host.env,
                                       expected=expected, timeout=timeout, label=label + ' upgrade command')

        def work(label, *argv, expected='OK'):
            return command(label, 'work', *argv, '--collection', host.repo, expected=expected)

        def sql(statement):
            return json.loads(host.sql(statement, limit=1_000_000))

        def migrations():
            return sql("SELECT json_agg(json_build_object('version',version,'sha256',encode(digest,'hex'),'applied_at',applied_at) ORDER BY version) FROM public.cairn_migration")

        for feature in ('work-view/1', 'work-artifacts/1', 'coordination-waits/1', 'next-turn-notices/1'):
            work('old', 'features', 'enable', '--feature', feature, '--request-id', str(uuid.uuid4()))
        added = work('old', 'project', 'add', '--slug', 'upgrade', '--workspace', workspace, '--project', project,
                     '--origin', ui.ORIGIN, '--instance', 'work-ui', '--request-id', str(uuid.uuid4()))
        work('old', 'refresh', '--project', 'upgrade')
        annotation = work('old', 'progress', 'add', '--project', 'upgrade', '--item', 'UPG-1',
                          '--text', progress_text, '--request-id', str(uuid.uuid4()))
        revision = work('old', 'artifact', 'register', '--kind', 'report', '--url',
                        'https://reports.fixture.invalid/upgrade/' + str(uuid.uuid4()), '--revision', 'immutable-v1',
                        '--display-name', 'Retained078 report', '--request-id', str(uuid.uuid4()))
        linked = work('old', 'artifact', 'link', '--project', 'upgrade', '--item', item,
                      '--revision', revision['revision_id'], '--role', 'evidence', '--rationale', rationale,
                      '--request-id', str(uuid.uuid4()))
        binding = added['binding_id']

        def observations(label):
            detail = work(label, 'show', '--project', 'upgrade', '--item', item)
            artifact = work(label, 'artifact', 'show', '--revision', revision['revision_id'])
            links = work(label, 'artifact', 'list', '--project', 'upgrade', '--item', item)
            features = work(label, 'features', 'status')['features']
            # Freshness is evaluated at read time; identity/source/cache fields
            # and every retained database cell remain the exact observations.
            item_observation = {key: value for key, value in detail['item'].items() if key != 'is_stale'}
            return dict(item=item_observation, annotations=detail['annotations'], artifact=artifact,
                        links=links['artifacts'], features=features)

        def retained_rows():
            return sql(f"""SELECT json_build_object(
                'project',(SELECT to_jsonb(p) FROM cairn.work_project p WHERE binding_id='{binding}'),
                'item',(SELECT to_jsonb(i) FROM cairn.work_item i WHERE binding_id='{binding}' AND plane_item_uuid='{item}'),
                'annotation',(SELECT to_jsonb(a) FROM cairn.work_annotation a WHERE annotation_id='{annotation['annotation_id']}'),
                'annotation_history',(SELECT jsonb_agg(to_jsonb(h) ORDER BY revision) FROM cairn.work_annotation_history h WHERE annotation_id='{annotation['annotation_id']}'),
                'artifact',(SELECT to_jsonb(a) FROM cairn.work_artifact a WHERE artifact_id='{revision['artifact_id']}'),
                'revision',(SELECT to_jsonb(r) FROM cairn.work_artifact_revision r WHERE revision_id='{revision['revision_id']}'),
                'link',(SELECT to_jsonb(l) FROM cairn.work_artifact_link l WHERE link_id='{linked['link_id']}'),
                'link_history',(SELECT jsonb_agg(to_jsonb(h) ORDER BY revision) FROM cairn.work_artifact_link_history h WHERE link_id='{linked['link_id']}'),
                'prior_features',(SELECT jsonb_agg(to_jsonb(f) ORDER BY feature_name) FROM cairn.work_feature f WHERE feature_name!='work-advice/1'))""")

        before_obs, before_rows, before_migrations = observations('old'), retained_rows(), migrations()
        require(before_obs['item']['plane_item_uuid'] == item and before_obs['item']['title'] == title
                and len(before_obs['annotations']) == 1 and before_obs['annotations'][0]['body'] == progress_text,
                'old binary did not create the selected item/progress positive controls')
        require(len(before_obs['links']) == 1 and before_obs['links'][0]['link_id'] == linked['link_id']
                and before_obs['links'][0]['rationale'] == rationale and before_obs['links'][0]['state'] == 'active',
                'old binary did not create the selected exact artifact link')
        require([r['version'] for r in before_migrations] == list(range(1, 79))
                and [r['sha256'] for r in before_migrations] == [r['sha256'] for r in inputs['old']['migrations']],
                'initial DB migration receipts differ from exact old78 source')
        report.update(pre_upgrade_observations=before_obs, pre_upgrade_rows=before_rows,
                      pre_upgrade_migrations=before_migrations)
        report['stages'].append('real_old78_project_progress_artifact_link')
        first_migrate = command('new', 'migrate', timeout=180)
        after_migrations = migrations()
        require([r['version'] for r in after_migrations] == list(range(1, 80))
                and after_migrations[:78] == before_migrations
                and after_migrations[-1]['sha256'] == inputs['new']['migrations'][-1]['sha256'],
                '079 upgrade changed an old migration receipt or recorded the wrong new digest')
        after_obs, after_rows = observations('new'), retained_rows()

        def preserves(old, new, where):
            # New79 may add fields, but every old observation remains exact.
            if isinstance(old, dict):
                require(isinstance(new, dict) and set(old) <= set(new), 'lost keys at ' + where)
                for key, value in old.items():
                    preserves(value, new[key], where + '.' + key)
            elif isinstance(old, list):
                require(isinstance(new, list) and len(old) == len(new), 'changed row count at ' + where)
                for index, (left, right) in enumerate(zip(old, new)):
                    preserves(left, right, where + '[' + str(index) + ']')
            else:
                require(old == new, 'changed retained078 value at ' + where)

        preserves(before_rows, after_rows, 'retained_rows')
        for name in ('item', 'annotations', 'artifact', 'links'):
            preserves(before_obs[name], after_obs[name], 'observations.' + name)
        old_features = {r['feature_name']: r for r in before_obs['features']}
        new_features = {r['feature_name']: r for r in after_obs['features']}
        require(set(new_features) == set(old_features) | {'work-advice/1'}, 'upgrade feature set differs from prior four plus advice')
        for name, value in old_features.items():
            preserves(value, new_features[name], 'features.' + name)
        require(new_features['work-advice/1']['status'] == 'disabled'
                and new_features['work-advice/1']['pending_count'] == 0, '079 migration enabled advice or invented pending work')
        require(host.sql('SELECT count(*) FROM cairn.work_advice_job') == '0'
                and host.sql('SELECT count(*) FROM cairn.work_advice_proposal') == '0', 'migration created advice workflow rows')
        report.update(first_migrate=first_migrate, post_upgrade_migrations=after_migrations,
                      post_upgrade_observations=after_obs, post_upgrade_rows=after_rows)
        report['stages'].append('actual079_migration_preserves_all078_digests_and_selected_data')
        second_migrate = command('new', 'migrate', timeout=180)
        require(migrations() == after_migrations and retained_rows() == after_rows
                and observations('new') == after_obs, 'second migration changed receipts/data/observations')
        report['second_migrate'] = second_migrate
        report['stages'].append('second_migrate_is_idempotent_with_advice_disabled')
        refused = work('old', 'show', '--project', 'upgrade', '--item', item, expected='SCHEMA_MISMATCH')
        require('database schema version 79 exceeds supported version 78' in refused['message'],
                'old binary refusal did not establish actual schema ceiling')
        require(migrations() == after_migrations and retained_rows() == after_rows,
                'refused old78 operation mutated the upgraded database')
        report['old_binary_refusal'] = refused
        report['stages'].append('fresh_old78_executable_refuses079_even_when_advice_disabled')
        report['status'] = 'passed'
    except BaseException as error:
        original_failure = error
        report.update(status='failed', error=str(error), error_type=type(error).__name__)
    finally:
        if host is not None:
            try:
                host.close()
            except BaseException as error:
                failures.append('host cleanup: ' + str(error))
        try:
            require(cluster.stop(), 'owned upgrade cluster stop unconfirmed')
            report['cluster_stopped'] = True
            if report['status'] == 'passed' and not failures:
                cluster.remove()
                report['cluster_cleaned'] = not root.exists()
        except BaseException as error:
            failures.append('cluster cleanup: ' + str(error))
        for label, selected in inputs.items():
            try:
                after = ui.source_identity(Path(selected['source']), Path(selected['capture']))
                require(after == selected['source_identity'], label + ' source changed during upgrade')
                require(digest(selected['binary']) == selected['binary_sha256'], label + ' binary changed during upgrade')
                require(digest(selected['binary_report']) == selected['binary_report_sha256'], label + ' build receipt changed')
                report[label + '_source_after'] = after
            except BaseException as error:
                failures.append('identity check: ' + str(error))
        if digest(__file__) != fixture_sha:
            failures.append('upgrade fixture changed during run')
        if failures:
            report.update(status='failed', cleanup_or_identity_errors=failures)
        (output / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
    if original_failure is not None:
        raise original_failure
    require(report['status'] == 'passed' and report['cluster_cleaned'], 'upgrade proof or cleanup incomplete')
    print(json.dumps(dict(status='passed', stages=report['stages'], report=str(output / 'report.json'))))


if __name__ == '__main__':
    main()
