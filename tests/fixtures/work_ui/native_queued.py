"""Run the actual native queued_case seam with an exact candidate's UI callback."""
import argparse
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace

sys.dont_write_bytecode = True
parser = argparse.ArgumentParser()
parser.add_argument('--source', type=Path, required=True)
parser.add_argument('--capture-manifest', type=Path, required=True)
parser.add_argument('--binary', type=Path, required=True)
parser.add_argument('--binary-report', type=Path, required=True,
    help='retained work-view build report binding this binary to the same captured source without overlays')
parser.add_argument('--native-manifest', type=Path, required=True)
parser.add_argument('--output', type=Path, required=True)
parser.add_argument('--uipass-home', type=Path, default=Path.home() / 'git/uipass')
args = parser.parse_args()
source = args.source.resolve()
args.output = args.output.resolve()
sys.path.insert(0, str(source / 'scripts'))
import check_codex_organic_native as native
import codex_loopback_fixture
import disposable_host
import work_coordination_ui_fixture as ui
from playwright.sync_api import sync_playwright

require = disposable_host.require
require(not args.output.is_relative_to(source)
    and not args.output.is_relative_to(Path(__file__).resolve().parents[3]), 'native panel output must be outside source checkouts')
for module in (native, codex_loopback_fixture, disposable_host, ui):
    require(Path(module.__file__).resolve().parent == source / 'scripts', 'native proof imported a helper outside the captured candidate')
callback_path = Path(__file__).resolve().with_name('coordination.py')
spec = importlib.util.spec_from_file_location('selected_coordination_panel', callback_path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
require(not args.output.exists(), 'native panel output must be fresh')
args.output.mkdir(mode=0o700)
runtime = Path(tempfile.mkdtemp(prefix='cairn-native-panel-'))
cluster = disposable_host.OwnedCluster(runtime / 'pg')
report = dict(schema='cairn.native-queued-work-panel-proof/1', status='running', stages=[],
    whole_coordination_acceptance=False, binary_sha256=native.digest(args.binary),
    fixture_sha256=native.digest(__file__), cluster_cleaned=False,
    provider='local scripted Responses fixture', model_usefulness_measured=False)
try:
    report['source'] = ui.source_identity(source, args.capture_manifest)
    raw_build = args.binary_report.read_bytes()
    build = json.loads(raw_build)
    require(build.get('schema') == 'cairn.work-ui-proof/1'
        and build.get('binary_sha256') == report['binary_sha256']
        and build.get('source') == report['source'] == build.get('source_after_run')
        and build.get('build_input_sha256') == report['source']['input_sha256']
        and build.get('web_overlay') == {}, 'binary build report does not bind this unchanged captured candidate')
    report['binary_build_report_sha256'] = hashlib.sha256(raw_build).hexdigest()
    report['loaded_helpers'] = {str(Path(item.__file__).resolve().relative_to(source)): native.digest(item.__file__)
        for item in (native, codex_loopback_fixture, disposable_host, ui)}
    report['callback_fixture'] = dict(path=str(callback_path), sha256=native.digest(callback_path))
    home = runtime / 'home'; home.mkdir(mode=0o700)
    config = home / 'plane.toml'; key = home / 'plane.key'
    key.write_text('synthetic-fixture-key-never-sent\n'); key.chmod(0o600)
    config.write_text(f'default_instance = "work-ui"\n[instance.work-ui]\nbase_url = "{ui.ORIGIN}"\nworkspace = "work-ui"\napi_key_file = "{key}"\n')
    config.chmod(0o600)
    env = dict(PATH=os.environ.get('PATH', '/usr/bin:/bin'), LANG='C.UTF-8', TZ='UTC', HOME=str(home),
        XDG_CONFIG_HOME=str(home / '.config'), TMPDIR=str(runtime),
        PLAYWRIGHT_BROWSERS_PATH=os.environ.get('PLAYWRIGHT_BROWSERS_PATH', str(Path.home() / '.cache/ms-playwright')),
        PLANE_CONFIG=str(config), CAIRN_PLANE_BIN=str(source / 'tests/fixtures/work_ui/plane.py'),
        CAIRN_WORK_UI_PLANE_STATE=str(runtime / 'plane-state.json'))
    os.environ.clear(); os.environ.update(env)
    fixture = SimpleNamespace(report=report, env=env, output=args.output.resolve(),
        state=dict(origin=ui.ORIGIN, workspace=ui.uid('workspace'), projects={}), state_path=runtime / 'plane-state.json')
    disposable_host.OwnedCluster.start(cluster.root, pg_bin=cluster.pg_bin)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            with disposable_host.DisposableHost.start(cluster, repo=str(runtime/'collection'),
                binary=args.binary, database='native_panel', label='native_panel', env=env) as host:
                callback = module.make_native_queued_case(ui.Fixture, fixture, browser, ui.uid, ui.dump, require)
                def with_uipass(active_host, session):
                    started = time.monotonic()
                    callback(active_host, session)
                    borrowed = copy.copy(active_host)
                    borrowed.env = dict(active_host.env, **{key: env[key] for key in
                        ('PLANE_CONFIG', 'CAIRN_PLANE_BIN', 'CAIRN_WORK_UI_PLANE_STATE')})
                    view = SimpleNamespace(host=borrowed, binary=active_host.binary,
                        collection=active_host.repo, server=None)
                    before = active_host.sql('SELECT jsonb_agg(to_jsonb(n) ORDER BY notice_id) FROM cairn.agent_organic_notice n')
                    try:
                        ui.Fixture.start_web(view)
                        with browser.new_context() as context:
                            ui.Fixture.login(view, context)
                            storage = context.storage_state()
                        items = ui.Fixture.work(view, 'list', '--project', 'native-quiet-view')['items']
                        require(len(items) == 1, 'native UIPass project did not retain its exact one item')
                        folder = args.output / 'uipass-fixture'; folder.mkdir(mode=0o700)
                        ui.dump(folder / 'session.json', storage)
                        first = [dict(type='click', selector=f'.work-card[data-item-uuid="{items[0]["plane_item_uuid"]}"]')]
                        states = dict(queued_first=dict(route='/', ready='.coordination-notice >> nth=0', storage_state_file='session.json',
                            actions=first + [dict(type='click', selector='.coordination-notice summary >> nth=0')]),
                            queued_last=dict(route='/', ready='.coordination-notice >> nth=0', storage_state_file='session.json',
                            actions=first + [dict(type='click', selector='#coordination-notices-more'),
                                dict(type='click', selector='.coordination-notice summary >> nth=0')]))
                        manifest = dict(schema_version=1, viewports=dict(desktop=dict(width=1440,height=1000), phone=dict(width=390,height=844)),
                            timeouts=dict(build=300,startup=15,case=10,total=25), surfaces=dict(native_quiet=dict(states=states)))
                        import yaml
                        (folder/'manifest.yaml').write_text(yaml.safe_dump(manifest,sort_keys=False))
                        result = subprocess.run(['uv','run','--project',str(args.uipass_home.resolve()),'--frozen','uipass','run',
                            '--manifest',str(folder/'manifest.yaml'),'--base-url',view.url,'--output',str(args.output/'uipass')],
                            capture_output=True,text=True,timeout=30)
                        (args.output/'uipass.log').write_text(result.stdout+result.stderr)
                        verdict = json.loads((args.output/'uipass/verdict.json').read_text())
                        report['uipass'] = dict(exit_code=result.returncode,bundle='uipass',verdict=verdict)
                        require(result.returncode == 0, 'native notice-row UIPass did not pass')
                        require(active_host.sql('SELECT jsonb_agg(to_jsonb(n) ORDER BY notice_id) FROM cairn.agent_organic_notice n') == before
                            and active_host.sql('SELECT count(*) FROM cairn.agent_delivery') == '0', 'native UIPass changed queue or ordinary delivery state')
                        report['native_queued_panel']['pending'].remove('UIPass and actual screenshot review')
                        report['native_queued_panel']['pending'].append('actual screenshot review')
                        report['native_queued_panel']['with_uipass_elapsed_seconds'] = time.monotonic() - started
                    finally:
                        ui.Fixture.stop_web(view)
                native.scenario(host, args.native_manifest.resolve(), report, queued_case=with_uipass)
                require(report.get('native_queued_panel', {}).get('status') == 'passed', 'native UI callback did not complete')
        finally:
            browser.close()
    require(native.digest(args.binary) == report['binary_sha256'], 'candidate binary changed during native proof')
    require(native.digest(__file__) == report['fixture_sha256'], 'native panel fixture changed during proof')
    require(native.digest(callback_path) == report['callback_fixture']['sha256'], 'native panel callback changed during proof')
    report['source_after_run'] = ui.source_identity(source, args.capture_manifest)
    require(report['source'] == report['source_after_run'], 'candidate source changed during native proof')
    report['status'] = 'passed'
except BaseException as error:
    report.update(status='failed', error=str(error), error_type=type(error).__name__, diagnostics_root=str(runtime))
finally:
    try:
        require(cluster.stop(), 'disposable native panel PostgreSQL did not stop')
        report['cluster_cleaned'] = True
        if report['status'] == 'passed':
            shutil.rmtree(runtime)
    except BaseException as error:
        report.update(status='failed', cleanup_error=str(error), diagnostics_root=str(runtime))
    (args.output/'report.json').write_text(json.dumps(report, indent=2)+'\n')
print(json.dumps(dict(status=report['status'], report=str(args.output/'report.json'), error=report.get('error'), cluster_cleaned=report['cluster_cleaned'])))
raise SystemExit(0 if report['status'] == 'passed' else 1)
