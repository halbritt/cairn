#!/usr/bin/env python3
"""One disposable Cairn host for integration fixtures.

A fixture needs a private PostgreSQL database, an owner-only home with agent
identities, a running local API, one contract for reading CLI envelopes, and
clean process teardown. This module owns that implementation so fixtures keep
only their scenario and assertions.

Cluster and database adapters behind the one seam:

- ``ProvidedCluster``: the cluster ``scripts/test-postgres.sh`` provisioned,
  selected by ``CAIRN_DISPOSABLE_TEST_ROOT``. The host creates and migrates its
  own database inside it.
- ``OwnedCluster``: a cluster this process initialises itself (initdb/pg_ctl)
  for self-bootstrapping fixtures that run outside the integration runner.
- ``ProvidedDatabase``: an already migrated disposable database named by
  ``CAIRN_TEST_DATABASE_URL`` and ``CAIRN_DATABASE_URL`` (shared-database
  fixtures and ``scripts/test-local-lifecycle.sh``).

Every refusal is a ``HostRefusal``. Fixtures never touch an existing production
store: a provided root must be an owner-only ``/tmp/cairn-postgres.*`` cluster,
an owned cluster is created fresh, and a provided database must be the test
database named twice by the environment.
"""

import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import select
import shutil
import socket as socketlib
import stat
import subprocess
import sys
import threading
import time
import uuid

ROOT_PARENT = Path("/tmp")
ROOT_PREFIX = "cairn-postgres."
RESPONSE_SCHEMA = "cairn.response/1"
STDOUT_LIMIT = 1_000_000
STDERR_LIMIT = 100_000
SQL_LIMIT = 65_536
EXCERPT_LIMIT = 300


class HostRefusal(AssertionError, RuntimeError):
    """A fail-closed refusal.

    Both bases are deliberate: fixtures written before this module catch either
    ``AssertionError`` or ``RuntimeError`` around host operations.
    """


def require(condition, message):
    if not condition:
        raise HostRefusal(message)


def excerpt(text, limit=EXCERPT_LIMIT):
    """One bounded line of diagnostic text for a refusal message."""
    if isinstance(text, bytes):
        text = text.decode("utf-8", "replace")
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[:limit] + "…"


def pairs_unique(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, f"duplicate JSON key: {key}")
        result[key] = value
    return result


def decode_json(text):
    """One JSON value with duplicate keys refused."""
    try:
        return json.loads(text, object_pairs_hook=pairs_unique)
    except ValueError as error:
        raise HostRefusal("reply is not one JSON value: " + type(error).__name__) from None


def fixture_label(script=None):
    """A fixture's label from its script name: check_panel_stale_journal.py -> panel_stale_journal."""
    stem = Path(sys.argv[0] if script is None else script).stem
    for prefix in ("check_", "check-"):
        if stem.startswith(prefix):
            stem = stem[len(prefix):]
            break
    label = re.sub(r"[^a-z0-9]+", "_", stem.lower()).strip("_")
    require(label, "fixture label required")
    return label


def private_dir(path):
    path = Path(path)
    require(path.is_dir() and not path.is_symlink() and path.stat().st_uid == os.getuid()
            and stat.S_IMODE(path.stat().st_mode) == 0o700,
            f"owner-only directory required: {path.name}")


def write_private(path, text):
    """Create an owner-only regular file that must not exist yet."""
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(text)


def pg_bindir(environ=None):
    environ = os.environ if environ is None else environ
    if environ.get("CAIRN_PG_BIN"):
        return Path(environ["CAIRN_PG_BIN"])
    result = subprocess.run(["pg_config", "--bindir"], capture_output=True, text=True, timeout=10)
    require(result.returncode == 0 and result.stdout.strip(), "pg_config --bindir unavailable")
    return Path(result.stdout.strip())


def checked(argv, label, *, env=None, timeout=30, cwd=None):
    """Run a bounded command that must exit zero; refuse with a bounded excerpt."""
    try:
        result = subprocess.run([str(a) for a in argv], env=env, cwd=cwd, capture_output=True,
                                timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        raise HostRefusal(f"{label} timed out after {timeout}s") from None
    require(result.returncode == 0, f"{label} failed: " + excerpt(result.stderr or result.stdout))
    return result


# ------------------------------------------------------------- startup failures

def classify_pg_start_failure(stdout, stderr, log_path):
    """Map transient PostgreSQL diagnostics to fixed codes; retain no text or paths."""
    data = (stdout + b"\n" + stderr).decode("utf-8", "replace")
    try:
        data += "\n" + Path(log_path).read_text(errors="replace")
    except OSError:
        return "diagnostic_log_unavailable"
    lowered = data.lower()
    patterns = (
        ("address_in_use", ("address already in use", "could not bind")),
        ("socket_path_too_long", ("unix-domain socket path", "too long")),
        ("permission_denied", ("permission denied", "operation not permitted")),
        ("port_conflict", ("lock file \"postmaster.pid\" already exists", "is another postmaster")),
        ("invalid_configuration", ("unrecognized configuration parameter", "syntax error in file")),
        ("storage_or_filesystem", ("no space left on device", "read-only file system", "input/output error")),
        ("data_directory_invalid", ("is not a valid data directory", "database files are incompatible")),
    )
    for code, needles in patterns:
        if any(needle in lowered for needle in needles):
            return code
    return "unclassified"


def classify_api_start_output(stdout, stderr, *, truncated, socket_path=None):
    """Classify transient ``serve`` output without retaining paths or error text."""
    if truncated:
        return "output_truncated"
    try:
        envelope = json.loads(stdout)
    except (TypeError, ValueError):
        envelope = None
    if isinstance(envelope, dict) and envelope.get("ok") is False:
        status = envelope.get("status")
        message = envelope.get("message")
        lower = message.lower() if isinstance(message, str) else ""
        if status == "STORE_ERROR" and socket_path is not None and len(os.fsencode(socket_path)) >= 108:
            return "api_socket_path_too_long"
        if status in ("STORE_ERROR", "STORE_UNREACHABLE"):
            return "store_open_failed"
        if status == "INVALID_REQUEST" and "identity configuration" in lower:
            return "identity_config_invalid"
        if any(text in lower for text in ("connection refused", "no such file or directory",
                                          "database does not exist", "authentication failed")):
            return "database_unavailable"
        if any(text in lower for text in ("socket directory", "socket lock", "api socket")):
            return "socket_setup_refused"
        if any(text in lower for text in ("unrecognized configuration", "invalid source-opening")):
            return "runtime_configuration_invalid"
        return "unknown_json_refusal"
    combined = (stdout + b"\n" + stderr).lower()
    if b"address already in use" in combined or b"bind: address" in combined:
        return "api_bind_failed"
    if b"identity configuration" in combined:
        return "identity_config_invalid"
    if b"connection refused" in combined or b"database does not exist" in combined:
        return "database_unavailable"
    if not combined.strip():
        return "process_exit_no_output"
    return "unclassified_output"


class OutputCapture:
    """Bounded in-memory capture of a child's stream, discarded once startup succeeds."""

    def __init__(self, limit=16_384):
        self.limit = limit
        self.kept = bytearray()
        self.total = 0
        self.enabled = True
        self.lock = threading.Lock()

    def drain(self, stream):
        while True:
            chunk = stream.read(2048)
            if not chunk:
                return
            with self.lock:
                if self.enabled:
                    self.total += len(chunk)
                    if len(self.kept) < self.limit:
                        self.kept.extend(chunk[:self.limit - len(self.kept)])

    def snapshot(self):
        with self.lock:
            return bytes(self.kept), self.total > len(self.kept)

    def discard(self):
        with self.lock:
            self.enabled = False
            self.kept.clear()
            self.total = 0


# ------------------------------------------------------------------- clusters

def disposable_root(environ=None):
    """The integration runner's cluster root, refused unless fresh, private and ours."""
    environ = os.environ if environ is None else environ
    value = environ.get("CAIRN_DISPOSABLE_TEST_ROOT")
    require(value, "CAIRN_DISPOSABLE_TEST_ROOT required: run through scripts/test-postgres.sh")
    root = Path(value)
    require(root.parent == ROOT_PARENT and root.name.startswith(ROOT_PREFIX)
            and root.is_dir() and not root.is_symlink()
            and root.stat().st_uid == os.getuid() and stat.S_IMODE(root.stat().st_mode) == 0o700
            and (root / "data" / "PG_VERSION").is_file()
            and (root / "socket").is_dir() and not (root / "socket").is_symlink(),
            "fresh private PostgreSQL cluster required")
    return root


def pg_start_command(pg_bin, data_dir, socket_dir, log_path, options=""):
    server_options = f"-F -k {socket_dir} -c listen_addresses=''"
    if options:
        server_options += " " + options
    return [str(Path(pg_bin) / "pg_ctl"), "-D", str(data_dir), "-l", str(log_path),
            "-o", server_options, "-w", "start"]


def stop_postgres(pg_bin, data_dir):
    """Stop a cluster immediately; True only when it is observed stopped."""
    data_dir = Path(data_dir)
    if not (data_dir / "postmaster.pid").exists():
        return True
    try:
        result = subprocess.run([str(Path(pg_bin) / "pg_ctl"), "-D", str(data_dir),
                                 "-m", "immediate", "-w", "stop"],
                                capture_output=True, timeout=20, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0 and not (data_dir / "postmaster.pid").exists()


class ProvidedCluster:
    """A disposable cluster another owner provisioned, selected by its verified root."""

    owned = False

    def __init__(self, root, pg_bin=None):
        self.root = Path(root)
        self.socket = self.root / "socket"
        self.pg_bin = Path(pg_bin) if pg_bin else pg_bindir()

    @classmethod
    def from_environment(cls, environ=None):
        return cls(disposable_root(environ))

    def binary(self, candidate=None):
        """The disposable Cairn binary built beside the cluster; a candidate must be that file."""
        binary = self.root / "cairn"
        require(binary.is_file() and not binary.is_symlink(), "disposable Cairn binary required")
        if candidate is not None:
            require(Path(candidate).resolve() == binary.resolve(), "disposable Cairn binary required")
        return binary

    def dsn(self, database):
        return f"host={self.socket} dbname={database} sslmode=disable"

    def create_database(self, database):
        require(re.fullmatch(r"[a-z][a-z0-9_]{0,62}", database or ""),
                "disposable database name must be a lowercase identifier")
        checked([self.pg_bin / "createdb", "-h", self.socket, database],
                "createdb " + database, timeout=30)
        return self.dsn(database)

    def stop(self):
        return True

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.stop()
        return False


class OwnedCluster(ProvidedCluster):
    """A cluster this process initialises in an owner-only directory it controls."""

    owned = True

    @classmethod
    def start(cls, root, pg_bin=None, *, options="", timeout=30):
        root = Path(root)
        if root.exists():
            private_dir(root)
        else:
            root.mkdir(mode=0o700, parents=True)
        cluster = cls(root, pg_bin)
        data, log = root / "data", root / "postgres.log"
        checked([cluster.pg_bin / "initdb", "-D", data, "--auth-local=trust", "--auth-host=reject",
                 "--no-locale", "-E", "UTF8"], "initdb", timeout=timeout)
        cluster.socket.mkdir(mode=0o700)
        argv = pg_start_command(cluster.pg_bin, data, cluster.socket, log, options=options)
        try:
            result = subprocess.run(argv, capture_output=True, timeout=timeout, check=False)
        except subprocess.TimeoutExpired:
            raise HostRefusal("pg_start_timeout") from None
        require(result.returncode == 0, "pg_start_failed: "
                + classify_pg_start_failure(result.stdout, result.stderr, log))
        return cluster

    def stop(self):
        return stop_postgres(self.pg_bin, self.root / "data")

    def remove(self):
        """Stop and delete the cluster; a cluster whose state is unresolved is retained."""
        require(self.stop(), "PostgreSQL state unresolved; retained " + str(self.root))
        shutil.rmtree(self.root)

    def __exit__(self, *exc):
        self.stop()
        return False


class ProvidedDatabase:
    """An already migrated disposable database that the environment names twice."""

    def __init__(self, dsn):
        self.dsn = dsn

    @classmethod
    def from_environment(cls, environ=None):
        environ = os.environ if environ is None else environ
        dsn = environ.get("CAIRN_TEST_DATABASE_URL")
        require(dsn and environ.get("CAIRN_DATABASE_URL") == dsn,
                "CAIRN_TEST_DATABASE_URL and CAIRN_DATABASE_URL must name the same disposable database")
        return cls(dsn)


# ------------------------------------------------------------------ identities

def _per_role(value, role, default):
    if value is None:
        return default
    if callable(value):
        return value(role)
    if isinstance(value, dict):
        return value.get(role, default)
    return value


def stage_identities(home, roles, repo, *, principals, destination="hosted", identity_role="agent",
                     token_dir=None, token_name=None):
    """Write one owner-only token per role and the identities file the API reads.

    ``principals`` maps each role to its principal or is a callable of the role.
    ``destination`` and ``identity_role`` are a single value, a dict by role or a
    callable of the role. Returns ``{role: {"token_file": Path, "principal": str}}``.
    """
    home = Path(home)
    private_dir(home)
    token_dir = Path(token_dir) if token_dir else home
    if token_name is None:
        def token_name(role):
            return f"{role}.token"
    staged, identities = {}, []
    for role in roles:
        principal = principals(role) if callable(principals) else principals[role]
        token = secrets.token_urlsafe(32)
        path = token_dir / token_name(role)
        write_private(path, token)
        identities.append(dict(token_sha256=hashlib.sha256(token.encode()).hexdigest(),
                               principal=principal, repo=repo,
                               role=_per_role(identity_role, role, "agent"),
                               destination=_per_role(destination, role, "hosted")))
        staged[role] = dict(token_file=path, principal=principal)
    write_private(home / "identities.json", json.dumps(identities))
    return staged


# -------------------------------------------------------------------- envelope

def run_envelope(argv, body=None, *, env=None, expected="OK", timeout=15,
                 stdout_limit=STDOUT_LIMIT, stderr_limit=STDERR_LIMIT, forbidden=None, label=None):
    """Run one CLI command and read its ``cairn.response/1`` envelope.

    The reply must be bounded, one JSON value without duplicate keys, carry the
    response schema, agree between ``ok``, ``status`` and the exit code, and
    match ``expected``. Returns ``data`` for ``OK``; the whole envelope when a
    refusal was expected, so callers can read its status and message. With
    ``expected=None`` any status is accepted and the envelope is returned.
    """
    argv = [str(a) for a in argv]
    label = label or " ".join(argv[1:3])
    stdin = body if body is None or isinstance(body, (str, bytes)) else json.dumps(body)
    try:
        if stdin is None:
            result = subprocess.run(argv, stdin=subprocess.DEVNULL, text=True, capture_output=True,
                                    env=env, timeout=timeout)
        else:
            result = subprocess.run(argv, input=stdin, text=True, capture_output=True, env=env,
                                    timeout=timeout)
    except subprocess.TimeoutExpired:
        raise HostRefusal(f"{label}: no reply within {timeout}s") from None
    require(len(result.stdout) <= stdout_limit and len(result.stderr) <= stderr_limit,
            f"{label}: reply exceeds bound")
    for text in ([forbidden] if isinstance(forbidden, str) else forbidden or ()):
        require(text not in result.stdout and text not in result.stderr,
                f"{label}: reply disclosed forbidden text")
    envelope = decode_json(result.stdout)
    status = envelope.get("status") if isinstance(envelope, dict) else None
    require(isinstance(envelope, dict) and envelope.get("schema") == RESPONSE_SCHEMA
            and envelope.get("ok") is (status == "OK") and (result.returncode == 0) is (status == "OK"),
            f"{label}: malformed reply (status {status}, exit {result.returncode})"
            + (" " + excerpt(result.stderr) if result.stderr.strip() else ""))
    if expected is None:
        return envelope
    succeeded = expected == "OK"
    require(status == expected,
            f"{label}: {status} (expected {expected}, exit {result.returncode})"
            + (" " + excerpt(result.stderr) if result.stderr.strip() else ""))
    return envelope.get("data") if succeeded else envelope


# -------------------------------------------------------------------------- sql

def run_sql(dsn, statement, *, limit=SQL_LIMIT, timeout=10, pg_bin=None):
    """One bounded psql statement against a disposable database; returns stripped stdout."""
    psql = Path(pg_bin) / "psql" if pg_bin else None
    argv = [str(psql) if psql and psql.exists() else "psql", "-X", "-A", "-t", "-v",
            "ON_ERROR_STOP=1", "-d", dsn, "-c", statement]
    result = subprocess.run(argv, text=True, capture_output=True, timeout=timeout)
    require(result.returncode == 0 and len(result.stdout) <= limit,
            "disposable SQL operation failed: " + excerpt(result.stderr))
    return result.stdout.strip()


# ---------------------------------------------------------------------- server

def stop_process(process, *, timeout=10, label="process"):
    """Terminate, then kill, one process this host started; refuse an unobserved exit."""
    if process is None:
        return None
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout)
        except subprocess.TimeoutExpired:
            process.kill()
            try:
                process.wait(timeout)
            except subprocess.TimeoutExpired:
                raise HostRefusal(f"{label} did not exit after kill") from None
    return process.returncode


def socket_accepts(path):
    try:
        with socketlib.socket(socketlib.AF_UNIX, socketlib.SOCK_STREAM) as probe:
            probe.settimeout(0.2)
            probe.connect(str(path))
        return True
    except OSError:
        return False


class ApiServer:
    """One ``cairn serve`` process with bounded startup diagnostics."""

    def __init__(self, process, socket_path, *, captures=None, log=None, label="disposable API"):
        self.process = process
        self.socket = Path(socket_path)
        self.captures = captures
        self.log = log
        self.label = label

    def poll(self):
        return self.process.poll()

    def wait_ready(self, timeout=10, *, probe="accept"):
        """Wait until the server is ready or refuse with its failure class.

        ``probe="accept"`` requires a Unix socket that accepts a connection.
        ``probe="exists"`` is the historical contract that only requires the
        path to exist; TrialStore keeps it because pinned trial consumers drive
        it with synthetic binaries that create the path without listening.
        """
        require(probe in ("accept", "exists"), "unknown readiness probe")
        deadline = time.monotonic() + timeout
        while True:
            if self.process.poll() is not None:
                raise HostRefusal(f"{self.label} did not start: " + self.failure_class())
            if self.socket.exists():
                if probe == "exists":
                    self.discard_capture()
                    return self
                require(stat.S_ISSOCK(self.socket.stat().st_mode), f"{self.label} socket missing")
                if socket_accepts(self.socket):
                    self.discard_capture()
                    return self
            require(time.monotonic() < deadline, f"{self.label} did not start within {timeout}s")
            time.sleep(0.02)

    def failure_class(self):
        if self.captures is None:
            return "exited; see " + str(self.log)
        stdout_capture, stderr_capture, readers = self.captures
        for reader in readers:
            reader.join(timeout=2)
        stdout, out_truncated = stdout_capture.snapshot()
        stderr, err_truncated = stderr_capture.snapshot()
        category = classify_api_start_output(stdout, stderr, truncated=out_truncated or err_truncated,
                                             socket_path=self.socket)
        detail = excerpt(stderr or stdout)
        self.discard_capture()
        return category + (f" ({detail})" if detail else "")

    def discard_capture(self):
        if self.captures is not None:
            for capture in self.captures[:2]:
                capture.discard()

    def stop(self, timeout=10):
        code = stop_process(self.process, timeout=timeout, label=self.label)
        if self.log is not None and hasattr(self.log, "close"):
            self.log.close()
        return code


def launch_server(binary, env, *, socket_path, args=(), log=None, label="disposable API"):
    """Start ``cairn serve`` on ``socket_path``; call ``wait_ready`` on the result."""
    argv = [str(binary), "serve", "--socket", str(socket_path), *[str(a) for a in args]]
    if log is not None:
        stream = open(log, "ab")
        process = subprocess.Popen(argv, env=env, stdout=stream, stderr=stream)
        return ApiServer(process, socket_path, log=stream, label=label)
    process = subprocess.Popen(argv, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    stdout_capture, stderr_capture = OutputCapture(), OutputCapture()
    readers = [threading.Thread(target=stdout_capture.drain, args=(process.stdout,), daemon=True),
               threading.Thread(target=stderr_capture.drain, args=(process.stderr,), daemon=True)]
    for reader in readers:
        reader.start()
    return ApiServer(process, socket_path, captures=(stdout_capture, stderr_capture, readers),
                     label=label)


# ------------------------------------------------------------------------ host

class DisposableHost:
    """A database, an owner-only home with identities, and a local API for one fixture."""

    @classmethod
    def start(cls, source, *, repo, binary=None, database=None, home=None,
              roles=("sender", "worker"), principals=None, destination="hosted",
              identity_role="agent", token_name=None, label=None, env=None, inbox_root=False,
              serve=True, serve_args=(), socket_path=None, log=None, timeout=10):
        host = cls()
        host.label = label or fixture_label()
        host.source = source
        host.root = getattr(source, "root", None)
        host.repo = repo
        host.roles = tuple(roles)
        host.serve_args = tuple(serve_args)
        host.log = log
        host.timeout = timeout
        host.server = None
        if isinstance(source, ProvidedDatabase):
            require(database is None, "a provided database already has a name")
            require(binary is not None, "Cairn binary required")
            host.binary = Path(binary).resolve()
            host.database, host.dsn = None, source.dsn
            host.pg_bin = pg_bindir()
        else:
            host.binary = Path(binary).resolve() if source.owned else source.binary(binary)
            host.database = database or "cairn_" + host.label
            host.dsn = source.create_database(host.database)
            host.pg_bin = source.pg_bin
        require(home is not None or host.root is not None, "home required")
        host.home = Path(home) if home else host.root / (host.label.replace("_", "-") + "-home")
        if host.home.exists():
            private_dir(host.home)
        else:
            host.home.mkdir(mode=0o700)
        host.env = dict(os.environ, CAIRN_HOME=str(host.home), CAIRN_DATABASE_URL=host.dsn,
                        CAIRN_TEST_DATABASE_URL=host.dsn)
        if inbox_root:
            host.env["CAIRN_INBOX_ROOT"] = str(host.home)
        if env:
            host.env.update({key: str(value) for key, value in env.items()})
        host.socket = Path(socket_path) if socket_path else host.home / "api.sock"
        try:
            if not isinstance(source, ProvidedDatabase):
                checked([host.binary, "migrate"], "migrate", env=host.env, timeout=300)
            if principals is None:
                def principals(role, prefix=host.label.replace("_", "-")):
                    return f"agent/{prefix}-{role}-{uuid.uuid4()}"
            host.identities = stage_identities(host.home, host.roles, repo, principals=principals,
                                               destination=destination, identity_role=identity_role,
                                               token_name=token_name)
            if serve:
                host.start_server()
        except BaseException:
            host.close()
            raise
        return host

    # identities and connections

    def token(self, role):
        return self.identities[role]["token_file"]

    def principal(self, role):
        return self.identities[role]["principal"]

    @property
    def principals(self):
        return {role: value["principal"] for role, value in self.identities.items()}

    def connection(self, role, session=None):
        flags = ["--socket", str(self.socket), "--token-file", str(self.token(role))]
        if session:
            flags += ["--agent-id", session["agent_id"], "--execution-id", session["execution_id"]]
        return flags

    # envelopes

    def agent(self, role, operation, body=None, *, args=(), session=None, expected="OK",
              timeout=15, forbidden=None):
        """``cairn agent`` as one role: ``data``, the envelope for an expected refusal, or any
        envelope with ``expected=None``."""
        argv = [self.binary, "agent", *self.connection(role, session), operation, *args]
        return run_envelope(argv, body, env=self.env, expected=expected, timeout=timeout,
                            forbidden=forbidden, label=operation)

    def operator(self, operation, body=None, *, args=(), expected="OK", timeout=15):
        """An operator command through this home's trusted channel."""
        return run_envelope([self.binary, operation, *args], body, env=self.env, expected=expected,
                            timeout=timeout, label="operator " + operation)

    def command(self, argv, body=None, *, expected="OK", timeout=15, env=None, forbidden=None,
                label=None):
        """Any other envelope-returning invocation of the disposable binary."""
        return run_envelope([self.binary, *argv], body, env=self.env if env is None else env,
                            expected=expected, timeout=timeout, forbidden=forbidden, label=label)

    # database

    def sql(self, statement, *, limit=SQL_LIMIT, timeout=10):
        return run_sql(self.dsn, statement, limit=limit, timeout=timeout, pg_bin=self.pg_bin)

    def count(self, table):
        require(re.fullmatch(r"[a-z_]+", table or ""), "unsupported count table")
        return int(self.sql(f"SELECT count(*) FROM cairn.{table}"))

    def require_fresh(self, *tables, message=None):
        for table in tables:
            require(self.count(table) == 0,
                    message or f"{self.label} database is not fresh: cairn.{table}")

    # server lifecycle

    def start_server(self, *, args=None, timeout=None):
        require(self.server is None or self.server.poll() is not None, "API already running")
        self.server = launch_server(self.binary, self.env, socket_path=self.socket,
                                    args=self.serve_args if args is None else args, log=self.log,
                                    label=f"{self.label} API")
        self.server.wait_ready(self.timeout if timeout is None else timeout)
        return self.server

    def stop_server(self, timeout=10):
        if self.server is None:
            return None
        code, self.server = self.server.stop(timeout), None
        return code

    def restart_server(self, **kwargs):
        self.stop_server()
        return self.start_server(**kwargs)

    @property
    def process(self):
        return None if self.server is None else self.server.process

    def close(self, timeout=10):
        """Stop the API this host started; returns its exit code (None when already stopped)."""
        return self.stop_server(timeout)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


# ------------------------------------------------------------------ hook owner

OWNER_PROGRAM = r'''
import json, os, subprocess, sys
script, config, timeout = sys.argv[1], sys.argv[2], float(sys.argv[3])
for line in sys.stdin:
    request = json.loads(line)
    if '_compact' in request:
        env = dict(os.environ, CODEX_THREAD_ID=request['session_id'])
        result = subprocess.run([sys.executable, script, 'inbox', 'compact', *request['_compact']],
            env=env, text=True, capture_output=True, timeout=timeout)
    else:
        result = subprocess.run([sys.executable, script, 'hook', '--config', config],
            input=line, text=True, capture_output=True, timeout=timeout)
    print(json.dumps(dict(code=result.returncode, stdout=result.stdout,
                          stderr=result.stderr)), flush=True)
'''


class HookOwner:
    """A long-lived synthetic owner process that runs lifecycle hooks one event per line.

    Each ``send`` returns ``{"code", "stdout", "stderr"}`` of one hook run; a
    request carrying ``_compact`` runs ``inbox compact`` under that session.
    """

    @classmethod
    def start(cls, script, config, env, *, hook_timeout=20, reply_timeout=30):
        owner = cls()
        owner.reply_timeout = reply_timeout
        owner.process = subprocess.Popen(
            [sys.executable, "-u", "-c", OWNER_PROGRAM, str(script), str(config), str(hook_timeout)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, env=env)
        return owner

    def send(self, request, *, timeout=None):
        self.process.stdin.write(json.dumps(request) + "\n")
        self.process.stdin.flush()
        ready = select.select([self.process.stdout], [], [], self.reply_timeout if timeout is None else timeout)[0]
        require(ready, "hook owner timed out")
        line = self.process.stdout.readline()
        require(line, "hook owner exited")
        return decode_json(line)

    def hook(self, event, *, code=None, timeout=None):
        reply = self.send(event, timeout=timeout)
        if code is not None:
            require(reply.get("code") == code,
                    f"hook exited {reply.get('code')}, expected {code}: " + excerpt(reply.get("stderr")))
        return reply

    def compact(self, session_id, *args, timeout=None):
        return self.send(dict(session_id=session_id, _compact=[str(a) for a in args]), timeout=timeout)

    def stop(self, timeout=5):
        """Close the owner's input so it exits at end of file; terminate it only if it lingers."""
        if self.process is None:
            return None
        process, self.process = self.process, None
        if process.stdin and not process.stdin.closed:
            try:
                process.stdin.close()
            except OSError:
                pass
        try:
            process.wait(timeout)
        except subprocess.TimeoutExpired:
            pass
        return stop_process(process, timeout=timeout, label="hook owner")
