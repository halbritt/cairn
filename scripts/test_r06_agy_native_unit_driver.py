"""Preparation boundaries; no actual Agy/native/API/provider invocation."""
import contextlib
import copy
from datetime import datetime, timedelta, timezone
import importlib
import io
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
import uuid
import unittest
from unittest.mock import patch

import check_r06_agy_native_unit as driver


class NativeAdmissionTests(unittest.TestCase):
    """Owned nonnative files and injected packet/source boundaries; no native run."""
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='cairn-agy-admission-', dir=Path.home()/'.cache')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for name in ('evidence', 'catalog', 'ledgers', 'packet'):
            (self.root/name).mkdir(mode=0o700)
        self.client = self.root/'owned-nonnative-client'
        self.client.write_bytes(b'NONNATIVE selector test image; never executed\n')
        self.client.chmod(0o500)
        self.attempt = str(uuid.uuid4())
        self.images = {}
        self.pins = dict(images={})
        for role in driver.ROLES:
            path = self.root/('nonnative-'+role)
            path.write_text('not executable: '+role)
            path.chmod(0o400)
            self.images[role] = path
            self.pins['images'][role] = dict(path=str(path), sha256=driver.roundtrip.sha(path))
        now = datetime.now(timezone.utc).replace(microsecond=0)
        fmt = lambda value: value.strftime('%Y-%m-%dT%H:%M:%SZ')
        tools = {name:driver.tool_pin(Path(sys.executable).resolve() if name == 'python3'
                 else Path(shutil.which(name)).resolve()) for name in ('python3','strace','unshare','ip')}
        self.body = dict(schema=driver.NATIVE_SCHEMA, source_head='1'*40, source_tree='2'*40,
            images={role:dict(Path=str(path),SHA256=self.pins['images'][role]['sha256'])
                    for role,path in self.images.items()},
            client=dict(Path=str(self.client),SHA256=driver.roundtrip.sha(self.client)),
            provider=copy.deepcopy(driver.LOCAL_PROVIDER), attempt_id=self.attempt, attempt_limit=1,
            issued_at=fmt(now-timedelta(seconds=1)), expires_at=fmt(now+timedelta(minutes=5)),
            ledger=str(self.root/'ledgers'/(self.attempt+'.json')), evidence=str(self.root/'evidence'),
            catalog_root=str(self.root/'catalog'), tools=tools)
        for field in ('manifest','index','build_complete'):
            path=self.root/'packet'/(field+'.json');path.write_text('{}\n');path.chmod(0o400)
            self.body[field]=dict(Path=str(path),SHA256=driver.roundtrip.sha(path))
        self.path=self.root/'admission.json'
        self.patchers=[patch.object(driver,'AGY_SOURCE',self.client),
            patch.object(driver,'AGY_SHA256',self.body['client']['SHA256']),
            patch.object(driver,'native_source_identity',return_value=dict(source_head='1'*40,source_tree='2'*40)),
            patch.object(driver,'checked_packet',return_value=(self.images,self.pins,{'nonnative_injected_packet':True})),
            patch('subprocess.Popen',side_effect=AssertionError('process in admission unit test')),
            patch.object(driver.r04,'AgySession',side_effect=AssertionError('Agy in admission unit test')),
            patch.object(driver.r04,'GeminiFixture',side_effect=AssertionError('provider in admission unit test')),
            patch.object(driver.OwnedCluster,'start',side_effect=AssertionError('PG in admission unit test')),
            patch.object(driver.DisposableHost,'start',side_effect=AssertionError('API in admission unit test'))]
        for item in self.patchers:
            item.start();self.addCleanup(item.stop)

    def select(self, body=None, raw=None, **kwargs):
        self.path.chmod(0o600) if self.path.exists() else None
        self.path.write_text(json.dumps(body or self.body) if raw is None else raw)
        self.path.chmod(0o400)
        return driver.checked_native_admission(self.path,driver.roundtrip.sha(self.path),
                                               kwargs.pop('attempt_id',self.attempt),**kwargs)

    def test_valid_selector_performs_no_application_start_or_reservation(self):
        approved=self.select()
        self.assertEqual(approved['admission'],self.body)
        self.assertEqual(list((self.root/'ledgers').iterdir()),[])
        self.assertEqual(list((self.root/'catalog').iterdir()),[])

    def test_mutated_tuple_fields_refuse_without_consuming(self):
        for field,value in [('source_head','3'*40),('source_tree',driver.BASE_TREE),('attempt_id',str(uuid.uuid4())),
                ('attempt_limit',True),('attempt_limit',2),('schema','cairn.other/1'),('ledger',3)]:
            with self.subTest(field=field,value=value):
                body=copy.deepcopy(self.body);body[field]=value
                with self.assertRaises((driver.Refused,ValueError)):self.select(body)
                self.assertEqual(list((self.root/'ledgers').iterdir()),[])

    def test_missing_unknown_and_duplicate_fields_refuse(self):
        body=copy.deepcopy(self.body);del body['source_tree']
        with self.assertRaises(driver.Refused):self.select(body)
        body=copy.deepcopy(self.body);body['authorized']=True
        with self.assertRaises(driver.Refused):self.select(body)
        with self.assertRaisesRegex(driver.Refused,'duplicate'):
            self.select(raw=json.dumps(self.body)[:-1]+',"attempt_limit":1}')

    def test_expired_future_boundary_and_unbounded_times_refuse(self):
        expiry=datetime.strptime(self.body['expires_at'],'%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)
        issued=datetime.strptime(self.body['issued_at'],'%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)
        for now in (expiry,expiry+timedelta(seconds=1),issued-timedelta(seconds=1)):
            with self.subTest(now=now),self.assertRaises(driver.Refused):self.select(now=now)
        body=copy.deepcopy(self.body);body['expires_at']=(expiry+timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M:%SZ')
        with self.assertRaises(driver.Refused):self.select(body)
        body=copy.deepcopy(self.body);body['expires_at']=body['expires_at'].replace('Z','+00:00')
        with self.assertRaises(driver.Refused):self.select(body)

    def test_provider_host_key_proxy_and_unknown_values_refuse(self):
        for key,value in [('host','localhost'),('host','198.51.100.1'),('api_key','subscription'),
                ('NO_PROXY','*'),('port',False),('port',443),('unknown',True)]:
            with self.subTest(key=key,value=value):
                body=copy.deepcopy(self.body);body['provider'][key]=value
                with self.assertRaises(driver.Refused):self.select(body)
        self.assertEqual(list((self.root/'ledgers').iterdir()),[])

    def test_client_manifest_role_and_tool_alias_refuse(self):
        for field in ('client','manifest'):
            body=copy.deepcopy(self.body);body[field]['SHA256']='0'*64
            if field=='manifest':
                # Real packet preflight owns this hash check; injection returns
                # selected bytes, so verify its exact arguments independently.
                with patch.object(driver,'checked_packet',side_effect=driver.Refused('packet pin mismatch')):
                    with self.assertRaises(driver.Refused):self.select(body)
            else:
                with self.assertRaises(driver.Refused):self.select(body)
        body=copy.deepcopy(self.body);body['images']['observer']=body['images']['helper']
        with self.assertRaises(driver.Refused):self.select(body)
        body=copy.deepcopy(self.body);body['tools']['strace']=body['tools']['python3']
        with self.assertRaises(driver.Refused):self.select(body)

    def test_symlink_and_changed_admission_pin_refuse(self):
        self.select();pin=driver.roundtrip.sha(self.path)
        alias=self.root/'alias.json';alias.symlink_to(self.path)
        with self.assertRaises(driver.Refused):driver.checked_native_admission(alias,pin,self.attempt)
        self.path.chmod(0o600);self.path.write_text('{}\n');self.path.chmod(0o400)
        with self.assertRaisesRegex(driver.Refused,'pin mismatch'):
            driver.checked_native_admission(self.path,pin,self.attempt)

    def test_source_root_is_actual_driver_checkout_not_image_source(self):
        body=copy.deepcopy(self.body);body.update(source_head=driver.BASE,source_tree=driver.BASE_TREE)
        with self.assertRaisesRegex(driver.Refused,'driver source'):self.select(body)

    def test_root_overlap_nonempty_and_existing_consumption_refuse(self):
        body=copy.deepcopy(self.body);body['catalog_root']=body['evidence']
        with self.assertRaisesRegex(driver.Refused,'custody'):self.select(body)
        keep=self.root/'catalog'/'keep';keep.write_bytes(b'preserve')
        with self.assertRaisesRegex(driver.Refused,'fresh empty'):self.select()
        self.assertEqual(keep.read_bytes(),b'preserve');keep.unlink()
        ledger=Path(self.body['ledger']);ledger.write_bytes(b'old consumed marker')
        with self.assertRaisesRegex(driver.Refused,'consumed'):self.select()
        self.assertEqual(ledger.read_bytes(),b'old consumed marker')

    def test_prerequisite75_boundary_does_not_create_or_consume_ledger(self):
        approved=self.select()
        def prereq(_):raise driver.PrerequisiteUnavailable('fixture prerequisite')
        with self.assertRaises(driver.PrerequisiteUnavailable):
            driver.run_native_attempt(approved,prereq,lambda _:self.fail('execute after refusal'))
        self.assertFalse(Path(self.body['ledger']).exists())

    def test_expiry_is_rechecked_after_preflight_before_reservation(self):
        approved=self.select()
        def expire(value):value['admission']['expires_at']=value['admission']['issued_at']
        with self.assertRaisesRegex(driver.Refused,'changed after preflight'):
            driver.run_native_attempt(approved,expire,lambda _:self.fail('expired execute'))
        self.assertFalse(Path(self.body['ledger']).exists())

    def test_failure_remains_consumed_with_atomic_bound_terminal_and_no_retry(self):
        approved=self.select();calls=[]
        def fail(_):calls.append('entered');raise RuntimeError('owned nonnative failure')
        with self.assertRaises(RuntimeError):driver.run_native_attempt(approved,lambda _:None,fail)
        ledger=Path(self.body['ledger']);saved=ledger.read_bytes()
        terminal=ledger.with_suffix('.terminal.json');raw=terminal.read_bytes();result=json.loads(raw)
        self.assertEqual(result['state'],'failed');self.assertEqual(result['admission'],approved['pin'])
        self.assertEqual(result['tuple'],self.body);self.assertEqual(calls,['entered'])
        with self.assertRaises(FileExistsError):driver.run_native_attempt(approved,lambda _:None,fail)
        self.assertEqual(calls,['entered']);self.assertEqual(ledger.read_bytes(),saved)
        self.assertEqual(terminal.read_bytes(),raw)
        self.assertEqual(sorted(p.name for p in ledger.parent.iterdir()),sorted([ledger.name,terminal.name]))

    def test_two_simultaneous_reservations_have_exactly_one_winner(self):
        approved=self.select()
        def reserve(_):
            try:return driver.NativeReservation(approved)
            except FileExistsError:return None
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(reserve,range(2)))
        winners=[result for result in results if result is not None]
        self.assertEqual(len(winners),1);winners[0].finish(dict(state='failed',nonnative_control=True))

    def test_terminal_cannot_overwrite_binding_or_prior_result(self):
        approved=self.select();reservation=driver.NativeReservation(approved)
        terminal=reservation.finish(dict(state='failed',admission={'forged':True},tuple={'forged':True}))
        original=terminal.read_bytes();parsed=json.loads(original)
        self.assertEqual(parsed['admission'],approved['pin']);self.assertEqual(parsed['tuple'],self.body)
        with self.assertRaises(FileExistsError):reservation.finish(dict(state='passed'))
        self.assertEqual(terminal.read_bytes(),original)
        self.assertFalse(any(path.name.startswith('.') for path in terminal.parent.iterdir()))

    def test_reservation_rechecks_selected_bytes_and_copies_immutable_binding(self):
        approved=self.select();reservation=driver.NativeReservation(approved)
        original=copy.deepcopy(approved['admission']);approved['admission']['images']={}
        terminal=reservation.finish(dict(state='failed'))
        self.assertEqual(json.loads(terminal.read_bytes())['tuple'],original)
        other=copy.deepcopy(approved);other['admission']=original
        other['admission']['ledger']=str(self.root/'ledgers'/(str(uuid.uuid4())+'.json'))
        with self.assertRaisesRegex(driver.Refused,'changed after preflight'):driver.NativeReservation(other)

    def test_client_is_byte_identical_owned0500_inside_catalog_without_execution(self):
        approved=self.select();source=self.client.stat();result=driver.stage_native_client(approved)
        path=Path(result['pin']['Path']);info=path.stat()
        self.assertTrue(path.is_relative_to(Path(self.body['catalog_root'])))
        self.assertEqual(path.read_bytes(),self.client.read_bytes());self.assertEqual(info.st_mode&0o777,0o500)
        self.assertNotEqual((info.st_dev,info.st_ino),(source.st_dev,source.st_ino))
        with self.assertRaises(FileExistsError):driver.stage_native_client(approved)
        self.assertEqual(path.read_bytes(),self.client.read_bytes())

    def test_changed_client_pin_refuses_before_creating_catalog_copy(self):
        approved=self.select();self.client.chmod(0o700);self.client.write_bytes(b'changed');self.client.chmod(0o500)
        with self.assertRaises(ValueError):driver.stage_native_client(approved)
        self.assertEqual(list((self.root/'catalog').iterdir()),[])


class AgyPreparationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='cairn-agy-prep-test-', dir=Path.home() / '.cache')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def pins(self):
        return dict(python_sha256=driver.tool_pin(Path(sys.executable).resolve())['SHA256'],
                    strace_sha256=driver.tool_pin(Path(shutil.which('strace')).resolve())['SHA256'])

    def test_import_never_launches_a_process(self):
        with patch('subprocess.Popen', side_effect=AssertionError('process on import')), \
                patch.object(driver.r04, 'AgySession', side_effect=AssertionError('Agy on import')):
            importlib.reload(driver)

    def test_native_entry_refuses_before_any_process_or_file(self):
        with patch.object(driver.r04, 'TracedProcess', side_effect=AssertionError('trace launch')), \
                patch.object(driver.r04, 'AgySession', side_effect=AssertionError('native launch')):
            with contextlib.redirect_stderr(io.StringIO()) as diagnostic:
                self.assertEqual(driver.main(['--native-unit']), 2)
        self.assertIn('target resource closure', diagnostic.getvalue())
        self.assertEqual(list(self.root.iterdir()), [])

    def test_entry_requires_explicit_tool_pins(self):
        with patch.object(driver.r04, 'TracedProcess', side_effect=AssertionError('trace launch')):
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(driver.main(['--prepare-stubbed', '--evidence', str(self.root)]), 2)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_modes_cannot_be_combined(self):
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                driver.main(['--native-unit', '--prepare-stubbed'])

    def test_pin_mismatch_cannot_consume_ledger_or_launch(self):
        pins = self.pins()
        pins['python_sha256'] = '0' * 64
        with patch.object(driver.r04, 'TracedProcess', side_effect=AssertionError('trace launch')):
            with self.assertRaisesRegex(driver.Refused, 'pin mismatch'):
                driver.prepare_stubbed(self.root, **pins)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_symlink_and_writable_input_refused(self):
        original = self.root / 'original'
        original.write_bytes(b'pin')
        link = self.root / 'link'
        link.symlink_to(original)
        with self.assertRaises(ValueError):
            driver.checked_pin(link)
        original.chmod(0o666)
        with self.assertRaises(ValueError):
            driver.checked_pin(original)

    def test_evidence_requires_owned_0700_directory(self):
        self.root.chmod(0o755)
        with self.assertRaisesRegex(driver.Refused, '0700'):
            driver.prepare_stubbed(self.root, **self.pins())

    def test_output_custody_refuses_preexisting_bytes(self):
        selected = self.root / 'keep'
        selected.write_bytes(b'do not overwrite')
        with self.assertRaisesRegex(driver.Refused, 'fresh empty'):
            driver.prepare_stubbed(self.root, **self.pins())
        self.assertEqual(selected.read_bytes(), b'do not overwrite')

    def test_ledger_is_one_shot_and_keeps_first_result(self):
        path = self.root / 'stub-ledger.json'
        ledger = driver.reserve_ledger(path, {})
        driver.finish_ledger(ledger, dict(state='failed', kind='PREPARATION_STUBBED'))
        saved = path.read_bytes()
        with self.assertRaises(FileExistsError):
            driver.reserve_ledger(path, {})
        self.assertEqual(path.read_bytes(), saved)

    def test_launch_failure_remains_consumed_without_native_fallback(self):
        with patch.object(driver.r04, 'TracedProcess', side_effect=RuntimeError('retained fixture failure')), \
                patch.object(driver.r04, 'AgySession', side_effect=AssertionError('native fallback')):
            with self.assertRaisesRegex(RuntimeError, 'retained fixture failure'):
                driver.prepare_stubbed(self.root, **self.pins())
        ledger = json.loads((self.root / 'stub-ledger.json').read_bytes())
        self.assertEqual(ledger['state'], 'failed')
        self.assertEqual(ledger['native_runs'], 0)
        with self.assertRaisesRegex(driver.Refused, 'fresh empty'):
            driver.prepare_stubbed(self.root, **self.pins())

    def test_real_stub_trace_has_live_generation_eof_zero_join_and_no_native_claim(self):
        with patch.object(driver.r04, 'AgySession', side_effect=AssertionError('actual native launch')):
            result = driver.prepare_stubbed(self.root, **self.pins())
        self.assertEqual(result['state'], 'passed')
        self.assertEqual(result['parsed_execs'], 1)
        self.assertEqual(result['stdin_bytes'], len(driver.INPUT))
        self.assertEqual(result['interval']['Start'], 0)
        self.assertEqual(result['interval']['End'], Path(result['interval']['Trace']['Path']).stat().st_size)
        self.assertEqual(result['callbacks'], 0)
        self.assertFalse(result['NativePASS'])
        self.assertFalse(result['native_client_executed'])
        self.assertFalse(result['complete_unit_qualified'])
        self.assertEqual(result['refresh'], 'unobserved')
        self.assertEqual(result['native_runs'], 0)
        self.assertTrue(all(pid > 1 and not Path(f'/proc/{pid}').exists() for pid in result['owned_pids']))
        self.assertTrue(all(path.stat().st_mode & 0o077 == 0 for path in self.root.iterdir()))
        generation = json.loads(Path(result['generation']['Path']).read_bytes())
        self.assertEqual(generation['Before'], generation['After'])
        with self.assertRaisesRegex(driver.Refused, 'fresh empty'):
            driver.prepare_stubbed(self.root, **self.pins())


class AuthenticatedPreparationBoundaries(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='cairn-agy-auth-test-', dir=Path.home()/'.cache')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def test_authenticated_entry_requires_sealed_packet_before_pg_or_native(self):
        with patch.object(driver, 'pg_bindir', side_effect=AssertionError('PG before packet')), \
                patch.object(driver.r04, 'AgySession', side_effect=AssertionError('native entry')):
            with contextlib.redirect_stderr(io.StringIO()) as error:
                code = driver.main(['--prepare-authenticated', '--evidence', str(self.root)])
        self.assertEqual(code, 2)
        self.assertIn('INPUT_NOT_READY', error.getvalue())
        self.assertEqual(list(self.root.iterdir()), [])

    def test_unknown_input_preserves_consumed_ledger(self):
        ledger = self.root/'preparation-ledger.json'
        handle = driver.reserve_ledger(ledger, {}, kind='AUTHENTICATED_PREPARATION')
        driver.finish_ledger(handle, dict(state='passed', native_runs=0))
        saved = ledger.read_bytes()
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                driver.main(['--prepare-authenticated', '--unknown-admission'])
        self.assertEqual(ledger.read_bytes(), saved)

    def test_native_refusal_preserves_existing_auth_ledger(self):
        ledger = self.root/'preparation-ledger.json'
        ledger.write_bytes(b'consumed preparation; native_runs=0\n')
        saved = ledger.read_bytes()
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(driver.main(['--native-unit', '--evidence', str(self.root)]), 2)
        self.assertEqual(ledger.read_bytes(), saved)

    def manifest(self):
        return dict(schema='cairn.r06-unit-images/1', source_head=driver.BASE, source_tree=driver.BASE_TREE,
                    images={role:dict(path='/unit/'+role, sha256=str(i)*64)
                            for i, role in enumerate(driver.ROLES, start=1)})

    def test_images_must_name_exact1508_tree(self):
        pins = self.manifest()
        pins['source_tree'] = '71302f4fe158e7d59fa3d5b077df2447af03ad54'
        with self.assertRaisesRegex(driver.Refused, 'exact1508'):
            driver.validate_image_manifest(pins)

    def test_five_roles_cannot_alias_helper_observer(self):
        pins = self.manifest()
        pins['images']['observer'] = dict(pins['images']['helper'])
        with self.assertRaisesRegex(driver.Refused, 'alias'):
            driver.validate_image_manifest(pins)

    def packet_paths(self, manifest_name):
        paths = [self.root/manifest_name, self.root/'index.json', self.root/'build-complete.json']
        for path in paths:
            path.write_text('{}\n')
            path.chmod(0o600)
        return [item for path in paths for item in (path, driver.roundtrip.sha(path))]

    def test_pending_manifest_is_not_consumable(self):
        with self.assertRaisesRegex(driver.Refused, 'final packet filenames'):
            driver.checked_packet(*self.packet_paths('images-host.pending.json'))

    def test_unsealed_index_is_not_consumable(self):
        with self.assertRaisesRegex(driver.Refused, 'sealed index'):
            driver.checked_packet(*self.packet_paths('images-host.json'))

    def test_privacy_snapshot_never_hashes_credentials_aliases_or_api_home(self):
        credential = self.root/'worker.token'
        credential.write_bytes(b'unit-only credential')
        alias = self.root/'alias'
        os.link(credential, alias)
        api_home = self.root/'api-home'
        api_home.mkdir(mode=0o700)
        (api_home/'identities.json').write_bytes(b'private identity input')
        normal = self.root/'profile.json'
        normal.write_bytes(b'{"inbox_recall_binding":null}\n')
        original = driver.roundtrip.sha
        def privacy_guard(path):
            path = Path(path)
            self.assertFalse(path.is_relative_to(api_home))
            self.assertFalse(os.path.samefile(path, credential))
            return original(path)
        with patch.object(driver.roundtrip, 'sha', side_effect=privacy_guard):
            rows = driver.roundtrip.snapshot_root(self.root, credential, api_home=api_home)
        for path in (credential, alias, api_home/'identities.json'):
            self.assertFalse(rows[str(path)]['content_read'])
            self.assertNotIn('sha256', rows[str(path)])
        self.assertIn('sha256', rows[str(normal)])

    def test_null_profile_pin_must_match_actual_bytes(self):
        path = self.root/'profile.json'
        path.write_text('{"inbox_recall_binding":null}\n')
        pin = dict(Path=str(path), SHA256=driver.roundtrip.sha(path))
        fixture = {role+label:pin for role in ('Memory', 'Config') for label in ('A', 'B')}
        self.assertEqual(len(driver.null_profiles(fixture)), 4)
        path.write_text('{"inbox_recall_binding":"invented"}\n')
        with self.assertRaisesRegex(driver.Refused, 'pin changed'):
            driver.null_profiles(fixture)


class SealedRecipeCompatibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.packet = Path(os.environ['CAIRN_R06_AGY_CONTROLLED_PACKET'])
        cls.pins = json.loads((cls.packet/'images-host.json').read_bytes())
        cls.candidate = json.loads((cls.packet/'candidate.json').read_bytes())
        driver.checked_pin(cls.packet/'images-host.json',
            'e132531f982044e89546e67242f0ae28d7b0d3db05e8fee4f21c8f428feca068')
        driver.checked_pin(cls.packet/'candidate.json',
            '4e14e15f0f0aaa8a43cbdaf36bd396355f42dfdfd748584443b5570b0bb60397')

    def recipe(self, role='helper'):
        return copy.deepcopy(self.candidate['roles'][role]), self.pins['images'][role]

    def test_actual_sealed_recipes_all_five_roles(self):
        for role in driver.ROLES:
            with self.subTest(role=role):
                record, image = self.recipe(role)
                driver.validate_build_recipe(role, record, image, self.pins)

    def test_real_flags_permit_option_order(self):
        for role in ('helper', 'observer'):
            record, image = self.recipe(role)
            argv = record['build']['inner_argv']
            record['build']['inner_argv'] = [argv[0], 'test', '-c', '-o', image['path'],
                                            '-buildvcs=false', '-race', argv[-1]]
            driver.validate_build_recipe(role, record, image, self.pins)

    def test_missing_compile_refuses_execution_only_test(self):
        record, image = self.recipe()
        record['build']['inner_argv'].remove('-c')
        with self.assertRaises(driver.Refused):
            driver.validate_build_recipe('helper', record, image, self.pins)

    def test_missing_race_refused(self):
        record, image = self.recipe()
        record['build']['inner_argv'].remove('-race')
        with self.assertRaises(driver.Refused):
            driver.validate_build_recipe('helper', record, image, self.pins)

    def test_missing_vcs_refused(self):
        record, image = self.recipe()
        record['build']['inner_argv'].remove('-buildvcs=false')
        with self.assertRaises(driver.Refused):
            driver.validate_build_recipe('helper', record, image, self.pins)

    def test_wrong_output_refused(self):
        record, image = self.recipe()
        argv = record['build']['inner_argv']
        argv[argv.index('-o')+1] = '/wrong/output'
        with self.assertRaises(driver.Refused):
            driver.validate_build_recipe('helper', record, image, self.pins)

    def test_wrong_package_refused(self):
        record, image = self.recipe()
        record['build']['inner_argv'][-1] = './internal/lifecycle/coordination'
        with self.assertRaises(driver.Refused):
            driver.validate_build_recipe('helper', record, image, self.pins)

    def test_duplicate_output_refused(self):
        record, image = self.recipe()
        record['build']['inner_argv'][2:2] = ['-o', image['path']]
        with self.assertRaises(driver.Refused):
            driver.validate_build_recipe('helper', record, image, self.pins)

    def test_duplicate_required_flag_refused(self):
        record, image = self.recipe()
        record['build']['inner_argv'].insert(2, '-race')
        with self.assertRaises(driver.Refused):
            driver.validate_build_recipe('helper', record, image, self.pins)

    def test_unapproved_go_identity_refused(self):
        record, image = self.recipe()
        record['build']['inner_argv'][0] = '/bin/sh'
        with self.assertRaises(driver.Refused):
            driver.validate_build_recipe('helper', record, image, self.pins)

    def test_unknown_build_flag_refused(self):
        record, image = self.recipe()
        record['build']['inner_argv'].insert(2, '-run=all')
        with self.assertRaises(driver.Refused):
            driver.validate_build_recipe('helper', record, image, self.pins)

    def test_actual_role_alias_refused(self):
        pins = copy.deepcopy(self.pins)
        pins['images']['observer'] = dict(pins['images']['helper'])
        with self.assertRaises(driver.Refused):
            driver.validate_image_manifest(pins)

    def test_controlled_regular_file_still_refuses_0664(self):
        with tempfile.TemporaryDirectory(dir=Path.home()/'.cache') as directory:
            path = Path(directory)/'artifact'
            path.write_bytes(b'unit-only data')
            path.chmod(0o664)
            with self.assertRaises(ValueError):
                driver.checked_pin(path)

    def test_complete_controlled_packet_preflight(self):
        driver.checked_packet(self.packet/'images-host.json',
            'e132531f982044e89546e67242f0ae28d7b0d3db05e8fee4f21c8f428feca068',
            self.packet/'index.json', 'f169590e02f41ba1b9e6578713a7f030a24d0a1f21432bf2a2c118bc200bbeaa',
            self.packet/'build-complete.json', 'ddfcf61f4e63f44378e5009f16b510fa2436914d270dc807dcd865201a55745f')


def load_tests(loader, standard, pattern):
    # Normal discovery keeps the standing portable controls. This explicit
    # fixture input adds real sealed-recipe controls, with no skipped tests.
    if os.environ.get('CAIRN_R06_AGY_CONTROLLED_PACKET'):
        return standard
    return unittest.TestSuite(loader.loadTestsFromTestCase(case) for case in
        (AgyPreparationTests, AuthenticatedPreparationBoundaries, NativeAdmissionTests))


if __name__ == '__main__':
    unittest.main()
