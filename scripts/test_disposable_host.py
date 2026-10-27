"""Unit tests for the disposable host module; a real cluster test runs only when pg_config exists."""

import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import disposable_host as dh  # noqa: E402

FAKE_BINARY = r'''#!/usr/bin/env python3
import json, os, signal, socket, sys, time
argv = sys.argv[1:]
if argv and argv[0] == "migrate":
    sys.exit(0)
if argv and argv[0] == "serve":
    path = argv[argv.index("--socket") + 1]
    mode = os.environ.get("FAKE_SERVE", "listen")
    if mode == "exit_store_error":
        print(json.dumps(dict(ok=False, status="STORE_ERROR", message="connection refused")))
        sys.exit(1)
    if mode == "exit_silent":
        sys.exit(1)
    if mode == "exit_text":
        sys.stderr.write("identity configuration rejected\n")
        sys.exit(1)
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(path)
    server.listen(4)
    server.settimeout(0.1)
    stop = []
    signal.signal(signal.SIGTERM, lambda *_: stop.append(1))
    while not stop:
        try:
            client, _ = server.accept()
            client.close()
        except socket.timeout:
            pass
    server.close()
    os.unlink(path)
    sys.exit(0)
operation = argv[-1] if argv and argv[0] == "agent" else (argv[0] if argv else "")
body = sys.stdin.read()
envelope = dict(schema="cairn.response/1", ok=True, status="OK", data=dict(echo=body, argv=argv))
if operation == "refused":
    print(json.dumps(dict(schema="cairn.response/1", ok=False, status="REFUSED", message="no")))
    sys.exit(1)
if operation == "big":
    print(json.dumps(dict(envelope, data=dict(pad="x" * 1_000_001))))
    sys.exit(0)
if operation == "dup":
    print('{"schema":"cairn.response/1","ok":true,"status":"OK","status":"OK","data":{}}')
    sys.exit(0)
if operation == "noschema":
    print(json.dumps(dict(ok=True, status="OK", data={})))
    sys.exit(0)
if operation == "lie":
    print(json.dumps(envelope))
    sys.exit(1)
if operation == "slow":
    time.sleep(5)
if operation == "secret":
    envelope["data"]["body"] = "SECRET-BODY"
print(json.dumps(envelope))
'''

FAKE_HOOK = r'''#!/usr/bin/env python3
import json, os, sys
if sys.argv[1] == "hook":
    event = json.loads(sys.stdin.readline())
    print(json.dumps(dict(seen=event["hook_event_name"], config=sys.argv[3])))
elif sys.argv[1:3] == ["inbox", "compact"]:
    print(json.dumps(dict(compact=sys.argv[3:], thread=os.environ.get("CODEX_THREAD_ID"))))
else:
    sys.exit(3)
'''


def private_tempdir(prefix="cairn-dh-test.", parent="/tmp"):
    path = Path(tempfile.mkdtemp(prefix=prefix, dir=parent))
    path.chmod(0o700)
    return path


def write_script(path, text):
    path.write_text(text, encoding="utf-8")
    path.chmod(0o700)
    return path


class LabelTests(unittest.TestCase):
    def test_labels_strip_check_prefix_and_normalise(self):
        self.assertEqual(dh.fixture_label("scripts/check_panel_stale_journal.py"), "panel_stale_journal")
        self.assertEqual(dh.fixture_label("check-local-api.py"), "local_api")
        self.assertEqual(dh.fixture_label("trial_x.py"), "trial_x")
        with self.assertRaises(dh.HostRefusal):
            dh.fixture_label("check_.py")


class ClassifierTests(unittest.TestCase):
    def test_pg_start_failure_classes(self):
        with tempfile.TemporaryDirectory() as directory:
            log = Path(directory) / "postgres.log"
            self.assertEqual(dh.classify_pg_start_failure(b"waiting failed", b"", log),
                             "diagnostic_log_unavailable")
            log.write_text("FATAL: could not bind: Address already in use")
            self.assertEqual(dh.classify_pg_start_failure(b"", b"", log), "address_in_use")
            log.write_text("something else")
            self.assertEqual(dh.classify_pg_start_failure(b"", b"", log), "unclassified")

    def test_api_start_output_classes(self):
        envelope = json.dumps(dict(ok=False, status="STORE_ERROR", message="x")).encode()
        self.assertEqual(dh.classify_api_start_output(envelope, b"", truncated=False), "store_open_failed")
        self.assertEqual(dh.classify_api_start_output(b"", b"", truncated=True), "output_truncated")
        self.assertEqual(dh.classify_api_start_output(b"", b"", truncated=False), "process_exit_no_output")
        self.assertEqual(dh.classify_api_start_output(b"", b"identity configuration bad", truncated=False),
                         "identity_config_invalid")


class RootTests(unittest.TestCase):
    def setUp(self):
        self.root = private_tempdir(prefix=dh.ROOT_PREFIX)
        (self.root / "data").mkdir()
        (self.root / "data" / "PG_VERSION").write_text("17")
        (self.root / "socket").mkdir()
        self.environ = {"CAIRN_DISPOSABLE_TEST_ROOT": str(self.root)}

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_accepts_fresh_private_root(self):
        self.assertEqual(dh.disposable_root(self.environ), self.root)
        cluster = dh.ProvidedCluster.from_environment(self.environ)
        self.assertEqual(cluster.socket, self.root / "socket")
        self.assertEqual(cluster.dsn("cairn_x"), f"host={self.root / 'socket'} dbname=cairn_x sslmode=disable")

    def test_refuses_missing_or_foreign_roots(self):
        with self.assertRaises(dh.HostRefusal):
            dh.disposable_root({})
        other = private_tempdir(prefix="other.")
        try:
            with self.assertRaises(dh.HostRefusal):
                dh.disposable_root({"CAIRN_DISPOSABLE_TEST_ROOT": str(other)})
        finally:
            shutil.rmtree(other)

    def test_refuses_shared_mode_and_missing_cluster(self):
        self.root.chmod(0o755)
        with self.assertRaises(dh.HostRefusal):
            dh.disposable_root(self.environ)
        self.root.chmod(0o700)
        (self.root / "data" / "PG_VERSION").unlink()
        with self.assertRaises(dh.HostRefusal):
            dh.disposable_root(self.environ)

    def test_binary_must_be_the_cluster_build(self):
        cluster = dh.ProvidedCluster(self.root, pg_bin="/nonexistent")
        with self.assertRaises(dh.HostRefusal):
            cluster.binary()
        (self.root / "cairn").write_text("")
        self.assertEqual(cluster.binary(), self.root / "cairn")
        self.assertEqual(cluster.binary(self.root / "cairn"), self.root / "cairn")
        with self.assertRaises(dh.HostRefusal):
            cluster.binary("/usr/bin/true")
        with self.assertRaises(dh.HostRefusal):
            cluster.create_database("Not-Valid")


class ProvidedDatabaseTests(unittest.TestCase):
    def test_requires_the_same_test_database_twice(self):
        dsn = "host=/tmp/x dbname=cairn_test sslmode=disable"
        self.assertEqual(dh.ProvidedDatabase.from_environment(
            {"CAIRN_TEST_DATABASE_URL": dsn, "CAIRN_DATABASE_URL": dsn}).dsn, dsn)
        with self.assertRaises(dh.HostRefusal):
            dh.ProvidedDatabase.from_environment({"CAIRN_TEST_DATABASE_URL": dsn, "CAIRN_DATABASE_URL": "other"})
        with self.assertRaises(dh.HostRefusal):
            dh.ProvidedDatabase.from_environment({"CAIRN_DATABASE_URL": dsn})


class IdentityTests(unittest.TestCase):
    def setUp(self):
        self.home = private_tempdir()

    def tearDown(self):
        shutil.rmtree(self.home, ignore_errors=True)

    def test_stages_private_tokens_and_identities(self):
        staged = dh.stage_identities(self.home, ("sender", "worker"), "fixture:unit",
                                     principals=lambda role: "agent/" + role,
                                     destination={"worker": "local"}, identity_role={"worker": "observer"})
        identities = json.loads((self.home / "identities.json").read_text())
        self.assertEqual([row["principal"] for row in identities], ["agent/sender", "agent/worker"])
        for row, role in zip(identities, ("sender", "worker")):
            token_file = staged[role]["token_file"]
            self.assertEqual(token_file, self.home / f"{role}.token")
            self.assertEqual(stat.S_IMODE(token_file.stat().st_mode), 0o600)
            self.assertEqual(row["token_sha256"], __import__("hashlib").sha256(
                token_file.read_text().encode()).hexdigest())
            self.assertEqual((row["repo"], row["role"], row["destination"]),
                             ("fixture:unit", "agent", "hosted") if role == "sender"
                             else ("fixture:unit", "observer", "local"))
        self.assertEqual(stat.S_IMODE((self.home / "identities.json").stat().st_mode), 0o600)
        with self.assertRaises(FileExistsError):
            dh.stage_identities(self.home, ("sender",), "fixture:unit", principals={"sender": "x"})

    def test_refuses_shared_home(self):
        self.home.chmod(0o755)
        with self.assertRaises(dh.HostRefusal):
            dh.stage_identities(self.home, ("sender",), "fixture:unit", principals={"sender": "x"})


class EnvelopeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = private_tempdir()
        cls.binary = write_script(cls.directory / "fake-cairn", FAKE_BINARY)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.directory, ignore_errors=True)

    def call(self, operation, **kwargs):
        return dh.run_envelope([self.binary, "agent", "--socket", "s", "--token-file", "t", operation],
                               kwargs.pop("body", {"a": 1}), **kwargs)

    def test_ok_returns_data_and_refusal_returns_envelope(self):
        data = self.call("ok")
        self.assertEqual(json.loads(data["echo"]), {"a": 1})
        envelope = self.call("refused", expected="REFUSED")
        self.assertEqual((envelope["status"], envelope["message"]), ("REFUSED", "no"))
        self.assertEqual(dh.run_envelope([self.binary, "ok"], "raw body")["echo"], "raw body")
        self.assertEqual(self.call("refused", expected=None)["status"], "REFUSED")
        self.assertEqual(self.call("ok", expected=None)["status"], "OK")
        with self.assertRaises(dh.HostRefusal):
            self.call("lie", expected=None)
        self.assertEqual(dh.run_envelope([self.binary, "ok"])["echo"], "")

    def test_contract_refusals(self):
        for operation, kwargs in (("refused", {}), ("ok", dict(expected="REFUSED")), ("big", {}),
                                  ("dup", {}), ("noschema", {}), ("lie", {}),
                                  ("slow", dict(timeout=1)), ("secret", dict(forbidden="SECRET-BODY"))):
            with self.subTest(operation=operation):
                with self.assertRaises(dh.HostRefusal):
                    self.call(operation, **kwargs)
        self.assertTrue(issubclass(dh.HostRefusal, AssertionError) and issubclass(dh.HostRefusal, RuntimeError))


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = private_tempdir()
        cls.binary = write_script(cls.directory / "fake-cairn", FAKE_BINARY)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.directory, ignore_errors=True)

    def test_ready_server_stops_cleanly(self):
        socket_path = self.directory / "ready.sock"
        server = dh.launch_server(self.binary, dict(os.environ, FAKE_SERVE="listen"), socket_path=socket_path)
        server.wait_ready(timeout=10)
        self.assertTrue(dh.socket_accepts(socket_path))
        self.assertEqual(server.stop(), 0)
        self.assertFalse(socket_path.exists())

    def test_startup_failures_are_classified(self):
        for mode, category in (("exit_store_error", "store_open_failed"), ("exit_silent", "process_exit_no_output"),
                               ("exit_text", "identity_config_invalid")):
            with self.subTest(mode=mode):
                server = dh.launch_server(self.binary, dict(os.environ, FAKE_SERVE=mode),
                                          socket_path=self.directory / f"{mode}.sock")
                with self.assertRaises(dh.HostRefusal) as caught:
                    server.wait_ready(timeout=5)
                self.assertIn(category, str(caught.exception))

    def test_log_mode_points_at_the_log(self):
        log = self.directory / "api.log"
        server = dh.launch_server(self.binary, dict(os.environ, FAKE_SERVE="exit_store_error"),
                                  socket_path=self.directory / "log.sock", log=log)
        with self.assertRaises(dh.HostRefusal) as caught:
            server.wait_ready(timeout=5)
        self.assertIn("see", str(caught.exception))
        server.stop()
        self.assertIn("STORE_ERROR", log.read_text())


class HookOwnerTests(unittest.TestCase):
    def test_runs_hooks_and_compact_requests(self):
        directory = private_tempdir()
        try:
            script = write_script(directory / "hook.py", FAKE_HOOK)
            owner = dh.HookOwner.start(script, directory / "config.json", dict(os.environ))
            try:
                reply = owner.hook({"hook_event_name": "Stop"}, code=0)
                self.assertEqual(json.loads(reply["stdout"])["seen"], "Stop")
                reply = owner.compact("sid-1", "read", "--handle", "h")
                self.assertEqual(json.loads(reply["stdout"]), {"compact": ["read", "--handle", "h"], "thread": "sid-1"})
            finally:
                self.assertEqual(owner.stop(), 0)
        finally:
            shutil.rmtree(directory, ignore_errors=True)


class HostTests(unittest.TestCase):
    def setUp(self):
        self.directory = private_tempdir()
        self.binary = write_script(self.directory / "fake-cairn", FAKE_BINARY)
        self.database = dh.ProvidedDatabase("host=/tmp/none dbname=cairn_test sslmode=disable")

    def tearDown(self):
        shutil.rmtree(self.directory, ignore_errors=True)

    def test_host_over_a_provided_database(self):
        with dh.DisposableHost.start(self.database, repo="fixture:unit", binary=self.binary,
                                     home=self.directory / "home", roles=("alice",),
                                     principals={"alice": "agent/alice"}, label="unit",
                                     env=dict(FAKE_SERVE="listen"), inbox_root=True) as host:
            self.assertEqual(host.env["CAIRN_HOME"], str(self.directory / "home"))
            self.assertEqual(host.env["CAIRN_DATABASE_URL"], host.dsn)
            self.assertEqual(host.env["CAIRN_INBOX_ROOT"], str(host.home))
            self.assertEqual(host.principal("alice"), "agent/alice")
            self.assertEqual(host.socket, host.home / "api.sock")
            self.assertTrue(dh.socket_accepts(host.socket))
            data = host.agent("alice", "ok", {"n": 1}, session=dict(agent_id="a", execution_id="e"))
            self.assertEqual(data["argv"][:7], ["agent", "--socket", str(host.socket), "--token-file",
                                                 str(host.token("alice")), "--agent-id", "a"])
            self.assertEqual(host.operator("ok", {"x": 2})["argv"], ["ok"])
            self.assertEqual(host.command(["agents", "list", *host.connection("alice")])["argv"][0], "agents")
            self.assertEqual(host.agent("alice", "refused", expected="REFUSED")["status"], "REFUSED")
            host.restart_server()
            self.assertTrue(dh.socket_accepts(host.socket))
            self.assertEqual(host.close(), 0)
            self.assertIsNone(host.close())
        self.assertIsNone(host.server)
        self.assertFalse(host.socket.exists())

    def test_existing_private_home_and_token_names(self):
        home = self.directory / "existing"
        home.mkdir(mode=0o700)
        with dh.DisposableHost.start(self.database, repo="fixture:unit", binary=self.binary, home=home,
                                     roles=("agent",), principals={"agent": "agent:x"}, label="unit",
                                     token_name=lambda role: "hosted-agent.token", serve=False) as host:
            self.assertEqual(host.token("agent"), home / "hosted-agent.token")
        home.chmod(0o755)
        with self.assertRaises(dh.HostRefusal):
            dh.DisposableHost.start(self.database, repo="fixture:unit", binary=self.binary, home=home,
                                    roles=("agent",), label="unit", serve=False)

    def test_failed_start_releases_the_server(self):
        with self.assertRaises(dh.HostRefusal):
            dh.DisposableHost.start(self.database, repo="fixture:unit", binary=self.binary,
                                    home=self.directory / "home", roles=("alice",), label="unit",
                                    env=dict(FAKE_SERVE="exit_silent"))
        with self.assertRaises(dh.HostRefusal):
            dh.DisposableHost.start(self.database, repo="fixture:unit", binary=self.binary,
                                    home=self.directory / "other", database="named", label="unit")


@unittest.skipUnless(shutil.which("pg_config"), "PostgreSQL binaries required")
class OwnedClusterTests(unittest.TestCase):
    def test_owned_cluster_lifecycle(self):
        root = Path(tempfile.mkdtemp(prefix="cairn-dh-cluster.", dir="/tmp"))
        shutil.rmtree(root)
        cluster = dh.OwnedCluster.start(root)
        try:
            self.assertTrue((root / "data" / "PG_VERSION").is_file())
            dsn = cluster.create_database("cairn_unit")
            self.assertIn("dbname=cairn_unit", dsn)
            result = subprocess.run([str(cluster.pg_bin / "psql"), "-X", "-A", "-t", "-d", dsn,
                                     "-c", "SELECT 1"], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.stdout.strip(), "1")
        finally:
            cluster.remove()
        self.assertFalse(root.exists())


if __name__ == "__main__":
    unittest.main()
