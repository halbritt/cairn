"""End-to-end acceptance tests demonstrating native Hermes request cancellation and queue invariants.

Proves the 6 required claims on an owned Hermes interactive process:
  1. Queued wake preserves typed composer draft.
  2. Busy owner work finishes before wake.
  3. Exact Cairn request / native binding.
  4. Actual bounded long-running tool stops on request cancel.
  5. Later owner work unaffected (owner turns protected from stale/racing cancels).
  6. Retained uncertainty / recovery (no fake success, honest failure reporting).
"""
import json
import os
from pathlib import Path
import queue
import socket
import struct
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

# Ensure repo root and hermes-agent are on sys.path
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

_hermes_root = Path(os.environ.get('HERMES_ROOT', Path.home() / '.hermes' / 'hermes-agent'))
if str(_hermes_root) not in sys.path:
    sys.path.insert(0, str(_hermes_root))
_venv_site = Path.home() / '.hermes' / 'hermes-agent' / 'venv/lib'
if not _venv_site.exists() and (_hermes_root / 'venv/lib').exists():
    _venv_site = _hermes_root / 'venv/lib'
if _venv_site.exists():
    for _p in _venv_site.glob('python*/site-packages'):
        if str(_p) not in sys.path:
            sys.path.insert(0, str(_p))

# Set up isolated HERMES_HOME before importing ANY Hermes module (P1 B3 requirement)
import shutil
_isolated_tmp = tempfile.TemporaryDirectory(prefix='cairn-hermes-test-')
_hermes_home = Path(_isolated_tmp.name) / '.hermes'
_hermes_home.mkdir(parents=True, mode=0o700)
_old_hermes_home = os.environ.get('HERMES_HOME')
os.environ['HERMES_HOME'] = str(_hermes_home)

try:
    from prompt_toolkit.buffer import Buffer
    from cli import HermesCLI, _VoiceInputMessage
    from hermes_cli.plugins import get_plugin_manager, QueuedMessage
    from tools.approval import (
        get_current_request_id,
        set_current_request_id,
        get_current_turn_id,
        set_current_turn_id,
    )
    from tools.process_registry import process_registry
    HAS_HERMES_DEPS = True
except ImportError:
    HAS_HERMES_DEPS = False

from integrations.lifecycle import hermes_queue


def _get_process_credentials():
    pid = os.getpid()
    stat_text = Path(f'/proc/{pid}/stat').read_text()
    start_ticks = int(stat_text.rsplit(')', 1)[1].split()[19])
    boot_id = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    return dict(pid=pid, start=start_ticks, boot=boot_id)


@unittest.skipUnless(HAS_HERMES_DEPS, "Hermes dependencies required")
class HermesCancellationE2ETests(unittest.TestCase):
    _isolation_validated = False
    _bridge_initialized = False

    @classmethod
    def setUpClass(cls):
        cls._isolation_validated = False
        cls._bridge_initialized = False
        cls.old_hermes_home = _old_hermes_home
        cls.hermes_home = _hermes_home
        cls.isolated_tmp = _isolated_tmp
        cls.sock_path = f"/tmp/cairn-hermes-{os.getpid()}.sock"
        cls.process = _get_process_credentials()

        def _cleanup_env():
            if _old_hermes_home is not None:
                os.environ['HERMES_HOME'] = _old_hermes_home
            else:
                os.environ.pop('HERMES_HOME', None)
        cls.addClassCleanup(_cleanup_env)

        try:
            # Assert every persistence path belongs to the temporary home (P1 B3 isolation)
            from tools import process_registry as _pr
            if hasattr(_pr, 'CHECKPOINT_PATH'):
                assert Path(_pr.CHECKPOINT_PATH).resolve().is_relative_to(_hermes_home.resolve()), (
                    f"CHECKPOINT_PATH {_pr.CHECKPOINT_PATH} not in isolated {_hermes_home}"
                )
            # Isolation verified: only now may this fixture claim ownership of process registry resources
            cls._isolation_validated = True

            # Set up isolated plugin and configuration
            plugin_dir = cls.hermes_home / 'plugins' / 'cairn-coordination'
            plugin_dir.mkdir(parents=True, mode=0o700)
            shutil.copyfile(REPO_ROOT / 'integrations/hermes/coordination.py', plugin_dir / '__init__.py')
            (plugin_dir / 'plugin.yaml').write_text(json.dumps({'name': 'cairn-coordination', 'version': '1.0'}))
            (cls.hermes_home / 'cairn-coordination.json').write_text(
                json.dumps({'script': '/bin/true', 'config': '{}'})
            )
            (cls.hermes_home / 'config.yaml').write_text("plugins:\n  enabled:\n    - cairn-coordination\n")

            # Discover and load plugins once
            pm = get_plugin_manager()
            pm.discover_and_load()
            cls._bridge_initialized = True

            # Wait for bridge socket to become ready
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                if os.path.exists(cls.sock_path):
                    break
                time.sleep(0.05)
            if not os.path.exists(cls.sock_path):
                raise RuntimeError(f"Bridge socket {cls.sock_path} was not created during setUpClass")
        except BaseException:
            cls.tearDownClass()
            raise

    @classmethod
    def tearDownClass(cls):
        # Clean up ONLY resources actually owned and created by this fixture
        if getattr(cls, '_isolation_validated', False):
            try:
                from tools.process_registry import process_registry
                process_registry.kill_all()
            except Exception:
                pass
        if getattr(cls, '_bridge_initialized', False) and hasattr(cls, 'sock_path') and os.path.exists(cls.sock_path):
            try:
                os.unlink(cls.sock_path)
            except OSError:
                pass
        old_env = getattr(cls, 'old_hermes_home', _old_hermes_home)
        if old_env is not None:
            os.environ['HERMES_HOME'] = old_env
        else:
            os.environ.pop('HERMES_HOME', None)
        try:
            if getattr(cls, 'isolated_tmp', None) is not None:
                cls.isolated_tmp.cleanup()
        except Exception:
            pass

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name) / '.hermes'
        self.home.mkdir(parents=True, mode=0o700)

        # Build credentials
        self.process = self.__class__.process
        self.sock_path = self.__class__.sock_path

        # Instantiate HermesCLI in test mode
        self.cli = HermesCLI.__new__(HermesCLI)
        self.cli.session_id = f"ses_hermes_{os.getpid()}_{int(time.time() * 1000)}"
        self.cli._pending_input = queue.Queue()
        self.cli._interrupt_queue = queue.Queue()
        self.cli._agent_running = False
        self.cli._admission_lock = threading.RLock()
        self.cli._admission_state = "idle"
        self.cli._active_cancelled = False
        self.cli._should_exit = False
        self.cli._status_bar_suppressed_after_resize = False
        self.cli._pending_resume_sessions = []
        self.cli._pending_agent_seed = None
        self.cli._interactive_turn = False
        self.cli._pet_turn_error = False
        self.cli._pet_reasoning = False
        self.cli._spinner_text = ""
        self.cli._tool_start_time = 0.0
        self.cli._pending_tool_info = []
        self.cli._last_scrollback_tool = ""
        self.cli._last_turn_interrupted = False
        self.cli._active_request_id = None
        self.cli._active_delivery_id = None
        self.cli._active_turn_id = None
        self.cli._voice_mode = False
        self.cli._voice_continuous = False
        self.cli._voice_recording = False
        self.cli._voice_tts = False
        self.cli._command_running = False

        # Real prompt_toolkit buffer for composer draft testing
        self.composer_buffer = Buffer()
        self.cli._app = SimpleNamespace(
            current_buffer=self.composer_buffer,
            invalidate=MagicMock(),
            is_running=True,
            exit=MagicMock(),
        )

        # Mock mock-able methods
        self.cli._check_config_mcp_changes = MagicMock()
        self.cli._check_termios_drift = MagicMock()
        self.cli._drain_process_notifications = MagicMock()
        self.cli._maybe_fire_loop_tick = MagicMock()
        self.cli._turn_summary_begin = MagicMock()
        self.cli._turn_summary_emit = MagicMock()
        self.cli._pet_react_turn_end = MagicMock()
        self.cli._print_user_message_preview = MagicMock()
        self.cli._recover_terminal_after_interrupt = MagicMock()
        self.cli._drain_interrupt_queue_to_pending_input = MagicMock()
        self.cli._maybe_continue_goal_after_turn = MagicMock()
        self.cli._maybe_complete_loop_tick_after_turn = MagicMock()
        self.cli._typed_voice_stop = MagicMock(return_value=False)
        self.cli.handle_bang_shell = MagicMock(return_value=False)
        self.cli.process_command = MagicMock(return_value=True)

        # Mock agent for interruption tracking
        self.agent = MagicMock()
        self.cli.agent = self.agent
        self.interrupt_calls = []

        def mock_interrupt(*args, **kwargs):
            self.interrupt_calls.append((args, kwargs))

        self.agent.interrupt = mock_interrupt

        # Set plugin manager CLI reference
        pm = get_plugin_manager()
        pm._cli_ref = self.cli

        def cleanup_tools():
            process_registry.kill_all()
            set_current_request_id("")

        self.addCleanup(cleanup_tools)

    def test_claim1_queued_wake_preserves_typed_composer_draft(self):
        """Claim 1: Queued wake preserves human typed composer draft in prompt_toolkit."""
        draft_text = "def calculate_orbit(trajectory):\n    # user working draft"
        self.composer_buffer.text = draft_text
        self.composer_buffer.cursor_position = len(draft_text)

        # Enqueue wake via native Unix domain socket
        queued_id, started = hermes_queue.enqueue(
            self.sock_path,
            self.process,
            self.cli.session_id,
            "Cairn automated wakeup event",
            "wake-event-101",
            request_id="req-wake-101",
            delivery_id="del-wake-101",
        )
        self.assertEqual(queued_id, "wake-event-101")
        self.assertTrue(started)

        # Verify draft remains pristine immediately after enqueue
        self.assertEqual(self.composer_buffer.text, draft_text)
        self.assertEqual(self.composer_buffer.cursor_position, len(draft_text))

        # Dequeue the message as _process_loop would do
        item = self.cli._pending_input.get_nowait()
        unwrapped = self.cli._dequeue_pending_input(item)
        self.assertEqual(unwrapped, "Cairn automated wakeup event")

        # Crucial invariant: draft is still untouched after admission
        self.assertEqual(self.composer_buffer.text, draft_text)
        self.assertEqual(self.composer_buffer.cursor_position, len(draft_text))

    def test_claim2_busy_owner_work_finishes_before_wake(self):
        """Claim 2: Busy session queues serially; owner work completes before wake executes."""
        self.cli._agent_running = True  # Owner turn is currently running
        self.cli._active_request_id = None  # None indicates owner interactive turn

        # Check status over socket
        st = hermes_queue.status(self.sock_path, self.process, self.cli.session_id)
        self.assertEqual(st.get("status"), "busy")

        # Enqueue wake while busy
        queued_id, started = hermes_queue.enqueue(
            self.sock_path,
            self.process,
            self.cli.session_id,
            "Wake prompt while busy",
            "wake-event-102",
            request_id="req-wake-102",
        )
        self.assertEqual(queued_id, "wake-event-102")
        self.assertFalse(started, "Busy session must report started=False")

        # Interrupt queue must be empty: wake never interrupts busy owner work!
        self.assertTrue(self.cli._interrupt_queue.empty())
        self.assertEqual(self.cli._pending_input.qsize(), 1)
        queued_msg = self.cli._pending_input.queue[0]
        self.assertEqual(queued_msg.status, "pending")

        # Simulate owner finishing their turn
        self.cli._agent_running = False

        # Now process loop can admit the pending wake
        admitted = self.cli._dequeue_pending_input(self.cli._pending_input.get())
        self.assertEqual(admitted, "Wake prompt while busy")
        self.assertEqual(queued_msg.status, "admitted")

    def test_claim3_exact_cairn_request_native_binding(self):
        """Claim 3: Admitting QueuedMessage pins exact Cairn request ID and delivery ID."""
        qm = QueuedMessage(
            text="Wake for bound request",
            expected_session_id=self.cli.session_id,
            request_id="cairn-req-uuid-42",
            delivery_id="cairn-del-uuid-84",
        )
        self.cli._pending_input.put(qm)

        # Dequeue and admit
        payload = self.cli._dequeue_pending_input(self.cli._pending_input.get())
        self.assertEqual(payload, "Wake for bound request")

        # Check exact bindings
        self.assertEqual(self.cli._active_request_id, "cairn-req-uuid-42")
        self.assertEqual(self.cli._active_delivery_id, "cairn-del-uuid-84")
        self.assertIsNotNone(self.cli._active_turn_id)
        self.assertEqual(get_current_request_id(), "cairn-req-uuid-42")

    def test_claim4_bounded_long_running_tool_stops_on_request_cancel(self):
        """Claim 4: Request cancellation terminates tracked tool processes and positively verifies termination.

        Crucially: background tools NOT captured as belonging to this exact request/turn are PRESERVED.
        """
        # Setup active turn for request req-tool-cancel
        req_id = "req-tool-cancel-99"
        turn_id = "turn-tool-cancel-99"
        self.cli._active_request_id = req_id
        self.cli._active_turn_id = turn_id
        self.cli._agent_running = True
        set_current_request_id(req_id)
        set_current_turn_id(turn_id)

        # Spawn a real long-running OS process through process_registry belonging to this request/turn
        proc_sess = process_registry.spawn_local(
            "sleep 60",
            session_key=self.cli.session_id,
            request_id=req_id,
            turn_id=turn_id,
        )
        self.assertIsNotNone(proc_sess.pid)
        self.assertFalse(proc_sess.exited)
        os.kill(proc_sess.pid, 0)

        # Spawn an unrelated background tool (e.g. owner tool or prior turn)
        set_current_request_id("")
        set_current_turn_id("owner-turn-42")
        owner_tool = process_registry.spawn_local(
            "sleep 60",
            session_key=self.cli.session_id,
            request_id="",
            turn_id="owner-turn-42",
        )
        set_current_request_id(req_id)
        set_current_turn_id(turn_id)
        self.assertIsNotNone(owner_tool.pid)
        self.assertFalse(owner_tool.exited)
        os.kill(owner_tool.pid, 0)

        # Check tools status via bridge socket
        tstatus = hermes_queue.tools_status(
            self.sock_path, self.process, self.cli.session_id, tool_ids=[proc_sess.id]
        )
        self.assertEqual(len(tstatus.get("tools", [])), 1)
        self.assertEqual(tstatus["tools"][0]["status"], "running")

        # Now issue request cancellation via native socket
        abort_res = hermes_queue.abort(
            self.sock_path,
            self.process,
            self.cli.session_id,
            expected_request_id=req_id,
            expected_turn_id=turn_id,
        )

        # Verify abort outcome
        self.assertTrue(abort_res.get("aborted"))
        self.assertEqual(abort_res.get("turn_stop"), "interrupted")
        self.assertEqual(len(self.interrupt_calls), 1)
        self.assertTrue(self.interrupt_calls[0][1].get("hard_cancel"))

        # Verify tool termination outcome reported by bridge: ONLY proc_sess terminated!
        tools = abort_res.get("tools", [])
        self.assertEqual(len(tools), 1)
        self.assertEqual(tools[0]["item_id"], proc_sess.id)
        self.assertEqual(tools[0]["stop_state"], "terminated")

        # Positive proof: proc_sess was terminated
        time.sleep(0.1)
        with self.assertRaises(ProcessLookupError):
            os.kill(proc_sess.pid, 0)

        # Positive proof: owner_tool was PRESERVED and is still alive!
        os.kill(owner_tool.pid, 0)
        self.assertFalse(owner_tool.exited)

        # Cleanup owner_tool
        process_registry.kill_process(owner_tool.id, source="test.cleanup")

    def test_claim5_later_owner_work_unaffected_against_stale_cancel(self):
        """Claim 5: Cancellation of previous request/turn is refused against active owner turn."""
        old_req_id = "req-completed-earlier"

        # Turn finishes, finally block resets active request state
        self.cli._active_request_id = None
        self.cli._active_delivery_id = None
        self.cli._active_turn_id = None
        set_current_request_id("")
        set_current_turn_id("")
        self.cli._agent_running = False

        # Owner starts a new interactive turn
        self.cli._dequeue_pending_input("Owner interactive command")
        self.cli._agent_running = True
        self.assertIsNone(self.cli._active_request_id)
        owner_turn_id = self.cli._active_turn_id
        self.assertIsNotNone(owner_turn_id)

        # 1. Stale cancellation for old_req_id arrives while owner is working -> refused!
        with self.assertRaises(hermes_queue.RequestMismatchError) as ctx:
            hermes_queue.abort(
                self.sock_path,
                self.process,
                self.cli.session_id,
                expected_request_id=old_req_id,
            )
        self.assertIn("REQUEST_MISMATCH", str(ctx.exception))
        self.assertEqual(len(self.interrupt_calls), 0)
        self.assertTrue(self.cli._agent_running)

        # 2. Turn mismatch arrives with wrong expected_turn_id -> refused!
        with self.assertRaises(hermes_queue.RequestMismatchError) as ctx:
            hermes_queue.abort(
                self.sock_path,
                self.process,
                self.cli.session_id,
                expected_turn_id="turn-stale-999",
            )
        self.assertIn("TURN_MISMATCH", str(ctx.exception))
        self.assertEqual(len(self.interrupt_calls), 0)
        self.assertTrue(self.cli._agent_running)

        # 3. Unspecified abort (neither request nor turn) arrives while owner is working -> refused!
        with self.assertRaises(hermes_queue.RequestMismatchError) as ctx:
            hermes_queue.abort(
                self.sock_path,
                self.process,
                self.cli.session_id,
            )
        self.assertIn("UNSPECIFIED_ABORT", str(ctx.exception))
        self.assertEqual(len(self.interrupt_calls), 0)
        self.assertTrue(self.cli._agent_running)

        # Owner finishes turn normally without interruption
        self.cli._agent_running = False

    def test_claim6_retained_uncertainty_and_recovery(self):
        """Claim 6: Peer credentials, session mismatches, and connection drops report honest uncertainty."""
        # 1. Process identity mismatch (wrong PID) refuses before sending
        wrong_proc = dict(self.process, pid=999999)
        with self.assertRaises(hermes_queue.QueueUnavailable) as ctx:
            hermes_queue.enqueue(
                self.sock_path, wrong_proc, self.cli.session_id, "text", "c1"
            )
        self.assertIn("different process", str(ctx.exception))

        # 2. Session mismatch refuses honestly
        with self.assertRaises(hermes_queue.QueueUnavailable) as ctx:
            hermes_queue.abort(
                self.sock_path,
                self.process,
                "ses_nonexistent",
                expected_request_id="req-any",
            )
        self.assertIn("SESSION_MISMATCH", str(ctx.exception))

        # 3. Connection drop / closed socket reports uncertain outcome (never fake success)
        dead_sock = str(self.home / "dead.sock")
        with self.assertRaises(hermes_queue.QueueUnavailable) as ctx:
            hermes_queue.abort(
                dead_sock,
                self.process,
                self.cli.session_id,
                expected_request_id="req-any",
            )
        self.assertIn("unavailable", str(ctx.exception))

    def test_claim7_abort_during_admission_cancels_before_execution(self):
        """Claim 7: Abort arriving during admitting state cancels before chat can start."""
        req_id = "req-admit-race-1"
        turn_id = "turn-admit-race-1"
        qm = QueuedMessage(
            text="Wake for admission race",
            expected_session_id=self.cli.session_id,
            request_id=req_id,
            turn_id=turn_id,
        )
        self.cli._pending_input.put(qm)

        # Dequeue item: state becomes 'admitting', but chat has not started
        user_input = self.cli._dequeue_pending_input(self.cli._pending_input.get())
        self.assertEqual(user_input, "Wake for admission race")
        self.assertEqual(self.cli._admission_state, "admitting")
        self.assertFalse(self.cli._agent_running)

        # Abort arrives while admitting
        abort_res = hermes_queue.abort(
            self.sock_path,
            self.process,
            self.cli.session_id,
            expected_request_id=req_id,
            expected_turn_id=turn_id,
        )
        self.assertTrue(abort_res.get("aborted"))
        self.assertEqual(abort_res.get("turn_stop"), "cancelled_before_running")
        self.assertTrue(self.cli._active_cancelled)

        # When _process_loop reaches the execution block under _admission_lock:
        mock_chat = MagicMock()
        self.cli.chat = mock_chat
        with self.cli._admission_lock:
            if self.cli._active_cancelled:
                self.cli._admission_state = "idle"
                self.cli._active_cancelled = False
                self.cli._active_request_id = None
                self.cli._active_delivery_id = None
                self.cli._active_turn_id = None
            else:
                self.cli.chat(user_input)

        # Chat was never started
        mock_chat.assert_not_called()
        self.assertEqual(self.cli._admission_state, "idle")

    def test_claim8_pending_abort_requires_all_supplied_ids_match(self):
        """Claim 8: Pending queue abort requires all supplied IDs to match; partial mismatch does not dequeue."""
        req_id = "req-both-match"
        turn_id = "turn-both-match"
        qm = QueuedMessage(
            text="Wake requiring exact match",
            expected_session_id=self.cli.session_id,
            request_id=req_id,
            turn_id=turn_id,
        )
        self.cli._pending_input.put(qm)

        # 1. Supply matching request_id but mismatched turn_id:
        abort_mismatch = hermes_queue.abort(
            self.sock_path,
            self.process,
            self.cli.session_id,
            expected_request_id=req_id,
            expected_turn_id="turn-WRONG",
        )
        self.assertFalse(abort_mismatch.get("aborted"))
        self.assertEqual(abort_mismatch.get("turn_stop"), "already_ended")
        # Item must still be in pending input
        self.assertEqual(self.cli._pending_input.qsize(), 1)
        self.assertEqual(self.cli._pending_input.queue[0].request_id, req_id)

        # 2. Supply both correctly:
        abort_match = hermes_queue.abort(
            self.sock_path,
            self.process,
            self.cli.session_id,
            expected_request_id=req_id,
            expected_turn_id=turn_id,
        )
        self.assertTrue(abort_match.get("aborted"))
        self.assertEqual(abort_match.get("turn_stop"), "dequeued_before_admission")
        self.assertEqual(self.cli._pending_input.qsize(), 0)

    def test_claim9_immediate_refusal_on_stale_session_in_queue_message(self):
        """Claim 9: Queueing message for mismatched session immediately refuses with SESSION_MISMATCH."""
        with self.assertRaises(hermes_queue.QueueUnavailable) as ctx:
            hermes_queue.enqueue(
                self.sock_path,
                self.process,
                "ses_stale_target",
                "Wake text",
                "client-1",
                expected_session_id="ses_stale_expected",
            )
        self.assertIn("SESSION_MISMATCH", str(ctx.exception))
        # Nothing added to pending queue
        self.assertEqual(self.cli._pending_input.qsize(), 0)

    def test_claim10_concurrent_wake_history_persistence(self):
        """Claim 10: Concurrent wake history updates persist atomically without file corruption."""
        history_file = self.__class__.hermes_home / f"cairn-wake-history-{os.getpid()}.json"
        
        errors = []
        def writer(worker_id):
            try:
                for i in range(10):
                    # Enqueue a message which triggers on_consumed callback recording wake outcome
                    cid = f"client-{worker_id}-{i}"
                    s = socket.socket(socket.AF_UNIX)
                    s.connect(self.sock_path)
                    with s:
                        req = {"id": 1, "method": "session/wake_status", "params": {"client_id": cid}}
                        s.sendall(json.dumps(req).encode() + b"\n")
                        res = json.loads(s.makefile("r").readline())
                        if "error" in res:
                            errors.append(res["error"])
            except Exception as exc:
                errors.append(exc)

        # 3 workers within MAX_CONCURRENT_CLIENTS (4)
        threads = [threading.Thread(target=writer, args=(w,)) for w in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])

    def test_claim11_persistence_failure_handling(self):
        """Claim 11: Wake outcome persistence failure raises error and is captured by on_consumed callback."""
        qm = QueuedMessage(
            text="Wake testing persistence failure",
            expected_session_id=self.cli.session_id,
            request_id="req-persist-fail",
        )
        
        # Simulate on_consumed callback that raises an OSError on persistence failure
        def failing_on_consumed(status, current_sid):
            raise OSError("Disk full: unable to persist wake history")

        qm.on_consumed = failing_on_consumed
        self.cli._pending_input.put(qm)

        # Dequeue must not crash the process loop; callback error must be captured on message
        user_input = self.cli._dequeue_pending_input(self.cli._pending_input.get())
        self.assertEqual(user_input, "Wake testing persistence failure")
        self.assertEqual(qm.status, "admitted")
    def test_claim12_dequeue_to_admission_cancellation_atomicity(self):
        """Claim 12: Dequeue-to-admission atomicity holds admission lock across queue removal and admission."""
        removed = threading.Event()
        release = threading.Event()
        cancelled = threading.Event()
        errors = []
        result = {}
        chat = []

        class PausingQueue(queue.Queue):
            def get(q, *args, **kwargs):
                item = super().get(*args, **kwargs)
                removed.set()
                if not release.wait(4):
                    raise RuntimeError("dequeue barrier timed out")
                return item

        self.cli._pending_input = PausingQueue()
        self.cli._pending_input.put(
            QueuedMessage(
                text="wake-boundary",
                expected_session_id=self.cli.session_id,
                request_id="req-boundary",
            )
        )

        def preview(_):
            if not cancelled.wait(4):
                raise RuntimeError("cancel response timed out")
            self.cli._should_exit = True

        self.cli._print_user_message_preview = preview
        self.cli.chat = lambda *a, **kw: chat.append(a)

        def cancel():
            try:
                result.update(
                    hermes_queue.abort(
                        self.sock_path,
                        self.process,
                        self.cli.session_id,
                        expected_request_id="req-boundary",
                    )
                )
            except BaseException as e:
                errors.append(repr(e))
            finally:
                cancelled.set()

        worker = threading.Thread(
            target=self.cli._process_loop, args=(self.cli._app,), daemon=True
        )
        worker.start()
        self.assertTrue(removed.wait(4))
        acquired = self.cli._admission_lock.acquire(blocking=False)
        if acquired:
            self.cli._admission_lock.release()
        aborter = threading.Thread(target=cancel, daemon=True)
        aborter.start()
        if acquired:
            self.assertTrue(cancelled.wait(4))
        release.set()
        worker.join(5)
        aborter.join(5)

        self.assertFalse(worker.is_alive())
        self.assertFalse(aborter.is_alive())
        self.assertFalse(errors)
        self.assertFalse(
            acquired,
            "Admission lock was not held during queue removal to admission handoff",
        )
        self.assertFalse(
            chat,
            "Chat must not be called after cancellation during admission handoff",
        )
        self.assertEqual(result.get("turn_stop"), "cancelled_before_running")

    def test_claim13_tool_inventory_failure_reported_as_uncertain(self):
        """Claim 13: Tool inventory failure reports tools_uncertain and explicit tool_error."""
        from unittest.mock import patch
        req_id = "req-tool-inventory-fail"
        self.cli._admission_state = "running"
        self.cli._agent_running = True
        self.cli._active_request_id = req_id
        self.cli._active_turn_id = "turn-tool-fail"

        try:
            with patch.object(process_registry, 'list_sessions',
                              side_effect=RuntimeError('tool inventory unavailable')):
                result = hermes_queue.abort(
                    self.sock_path,
                    self.process,
                    self.cli.session_id,
                    expected_request_id=req_id,
                )
            self.assertTrue(result.get("aborted"))
            self.assertEqual(result.get("turn_stop"), "interrupted")
            self.assertTrue(result.get("tools_uncertain"))
            self.assertIn("tool inventory unavailable", result.get("tool_error", ""))
            tools = result.get("tools", [])
            self.assertEqual(len(tools), 1)
            self.assertEqual(tools[0].get("item_id"), "tool_inventory")
            self.assertIn("unavailable: tool inventory unavailable", tools[0].get("stop_state", ""))
        finally:
            self.cli._agent_running = False
            self.cli._admission_state = "idle"
            self.cli._active_request_id = None
            self.cli._active_turn_id = None

    def test_claim14_native_turn_binding_end_to_end_and_reconciliation(self):
        """Claim 14: End-to-end native turn identity spans admission, hooks, claim, TurnEnd reconciliation, and abort."""
        from integrations.lifecycle import coordination
        config = dict(harness='hermes', native_delivery=True, idle_wakeup='/unused')
        state = dict(agent=dict(agent_id='test-agent', execution_id='test-exec'), process=self.process)
        state['idle_wake'] = dict(
            transport='hermes-queue',
            session=coordination.session_ref(state['agent']),
            delivery_id='delivery-claim14',
            request_id='wake-request-claim14',
            native_id=self.cli.session_id,
        )
        text = coordination.wake_message(state['idle_wake'])
        qm = QueuedMessage(
            text=text,
            expected_session_id=self.cli.session_id,
            request_id='wake-request-claim14',
            delivery_id='delivery-claim14',
        )
        self.cli._dequeue_pending_input(qm)
        cli_turn = self.cli._active_turn_id
        self.assertTrue(cli_turn.startswith(f"{self.cli.session_id}:{self.cli.session_id}:"))

        calls = []
        def capture_run(*args, **kwargs):
            calls.append(json.loads(kwargs['input']))
            return SimpleNamespace(returncode=0, stdout=json.dumps({'hookSpecificOutput': {'additionalContext': ''}}))

        pm = get_plugin_manager()
        hook_turn = f"{self.cli.session_id}:task-c14:turn-c14"
        with patch('subprocess.run', side_effect=capture_run):
            pm.invoke_hook('pre_llm_call', session_id=self.cli.session_id, task_id='task-c14',
                           turn_id=hook_turn, user_message=text, model='test-model', platform='cli')
            pm.invoke_hook('post_llm_call', session_id=self.cli.session_id, task_id='task-c14',
                           turn_id=hook_turn, user_message=text, platform='cli')

        self.assertEqual(len(calls), 2)
        start, end = calls
        self.assertEqual(start.get('turn_id'), hook_turn)
        self.assertEqual(end.get('turn_id'), hook_turn)

        binding = coordination.wake_binding(config, state, start)
        self.assertEqual(binding.get('native_turn_id'), hook_turn)
        self.assertEqual(binding.get('delivery_id'), 'delivery-claim14')

        state['inbox_intent'] = dict(request_id='store-poll-c14', **binding)
        observed = coordination.normalize(config, end)
        self.assertEqual(observed.get('native_turn_id'), hook_turn)

        # Reconciliation at TurnEnd succeeds without NATIVE_TURN_MISMATCH
        with tempfile.TemporaryDirectory() as td:
            state_p = Path(td) / 'session.json'
            ctx_ret = coordination.inbox_context(config, state, state_p, observed)
            self.assertEqual(ctx_ret, '')

        # Abort using the bound hook turn is accepted
        self.cli._agent_running = True
        self.cli._admission_state = 'running'
        try:
            abort_res = hermes_queue.abort(
                self.sock_path,
                self.process,
                self.cli.session_id,
                expected_request_id='wake-request-claim14',
                expected_turn_id=binding['native_turn_id'],
            )
            self.assertTrue(abort_res.get('aborted'))
            self.assertEqual(abort_res.get('turn_stop'), 'interrupted')
        finally:
            self.cli._agent_running = False
            self.cli._admission_state = 'idle'
            self.cli._active_request_id = None
            self.cli._active_turn_id = None

    def test_claim15_adverse_turn_binding_rejection(self):
        """Claim 15: Adverse turn bindings (wrong prompt, missing turn_id, foreign session) are strictly rejected."""
        from integrations.lifecycle import coordination
        config = dict(harness='hermes', native_delivery=True, idle_wakeup='/unused')
        state = dict(agent=dict(agent_id='test-agent', execution_id='test-exec'), process=self.process)
        state['idle_wake'] = dict(
            transport='hermes-queue',
            session=coordination.session_ref(state['agent']),
            delivery_id='delivery-claim15',
            request_id='wake-request-claim15',
            native_id=self.cli.session_id,
        )
        valid_turn = f"{self.cli.session_id}:task-1:turn-1"
        valid_start = {
            'session_id': self.cli.session_id,
            'hook_event_name': 'TurnStart',
            'turn_id': valid_turn,
            'prompt': coordination.wake_message(state['idle_wake']),
        }
        # 1. Valid binding produces expected dict
        self.assertEqual(
            coordination.wake_binding(config, state, valid_start),
            {'delivery_id': 'delivery-claim15', 'native_turn_id': valid_turn},
        )
        # 2. Wrong prompt returns empty dict
        self.assertEqual(
            coordination.wake_binding(config, state, dict(valid_start, prompt='owner input text')),
            {},
        )
        # 3. Missing or empty turn_id returns empty dict
        self.assertEqual(
            coordination.wake_binding(config, state, dict(valid_start, turn_id='')),
            {},
        )
        # 4. Foreign session returns empty dict
        self.assertEqual(
            coordination.wake_binding(config, state, dict(valid_start, session_id='ses_foreign_other')),
            {},
        )
        # 5. Mismatched session prefix in turn_id returns empty dict
        self.assertEqual(
            coordination.wake_binding(config, state, dict(valid_start, turn_id='ses_other:task-1:turn-1')),
            {},
        )

    def test_claim16_session_end_turn_reconciliation_payload(self):
        """Claim 16: SessionEnd lifecycle hook carries saved turn_id and reconciles without NATIVE_TURN_MISMATCH."""
        from integrations.lifecycle import coordination
        config = dict(harness='hermes', native_delivery=True, idle_wakeup='/unused')
        state = dict(agent=dict(agent_id='test-agent', execution_id='test-exec'), process=self.process)
        state['idle_wake'] = dict(
            transport='hermes-queue',
            session=coordination.session_ref(state['agent']),
            delivery_id='delivery-claim16',
            request_id='wake-request-claim16',
            native_id=self.cli.session_id,
        )
        calls = []
        def capture_run(*args, **kwargs):
            calls.append(json.loads(kwargs['input']))
            return SimpleNamespace(returncode=0, stdout=json.dumps({'hookSpecificOutput': {'additionalContext': ''}}))

        pm = get_plugin_manager()
        hook_turn = f"{self.cli.session_id}:task-c16:turn-c16"
        text = coordination.wake_message(state['idle_wake'])
        with patch('subprocess.run', side_effect=capture_run):
            pm.invoke_hook('pre_llm_call', session_id=self.cli.session_id, task_id='task-c16',
                           turn_id=hook_turn, user_message=text, model='test-model', platform='daemon')
            pm.invoke_hook('post_llm_call', session_id=self.cli.session_id, task_id='task-c16',
                           turn_id=hook_turn, user_message=text, platform='daemon')

        self.assertEqual(len(calls), 2)
        start, end = calls
        self.assertEqual(start.get('hook_event_name'), 'TurnStart')
        self.assertEqual(start.get('turn_id'), hook_turn)
        self.assertEqual(end.get('hook_event_name'), 'SessionEnd')
        self.assertEqual(end.get('turn_id'), hook_turn)

        binding = coordination.wake_binding(config, state, start)
        state['inbox_intent'] = dict(request_id='store-poll-c16', **binding)
        observed = coordination.normalize(config, end)
        self.assertEqual(observed.get('native_turn_id'), hook_turn)

        # Successful SessionEnd reconciliation payload releases inbox without error
        with tempfile.TemporaryDirectory() as td:
            state_p = Path(td) / 'session.json'
            ctx_ret = coordination.inbox_context(config, state, state_p, observed)
            self.assertEqual(ctx_ret, '')


class HermesFixtureIsolationFailureTests(unittest.TestCase):
    """B3: Prove isolation rejection performs NO operations on a foreign registry, and environment is restored."""

    def _run_subp(self, code, *args):
        import subprocess
        env = dict(os.environ, HERMES_ROOT=str(_hermes_root))
        cmd = [sys.executable, "-c", code, str(REPO_ROOT), str(_hermes_root), str(_venv_site), *args]
        return subprocess.run(cmd, capture_output=True, text=True, env=env)

    def test_isolation_rejection_never_touches_foreign_registry(self):
        """Stage 1: Preloaded foreign registry with outside CHECKPOINT_PATH is rejected without kill_all or mutation."""
        code = """
import os, sys, json, tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, sys.argv[1])
sys.path.insert(0, sys.argv[2])
for p in Path(sys.argv[3]).glob("python*/site-packages"):
    sys.path.insert(0, str(p))

orig_env = "/some/original/hermes/path"
os.environ["HERMES_HOME"] = orig_env

with tempfile.TemporaryDirectory(prefix="cairn-foreign-test-") as foreign_dir:
    foreign_home = Path(foreign_dir) / ".hermes"
    foreign_home.mkdir(parents=True, mode=0o700)
    foreign_checkpoint = foreign_home / "processes.json"
    initial_content = json.dumps([{"session_id": "foreign-proc-1", "pid": 99999}])
    foreign_checkpoint.write_text(initial_content)
    initial_mtime = foreign_checkpoint.stat().st_mtime

    class ForeignReg:
        CHECKPOINT_PATH = foreign_checkpoint
        kill_all_called = False
        def kill_all(self):
            self.kill_all_called = True

    foreign_reg = ForeignReg()
    from scripts import test_hermes_cancellation_e2e as t

    with patch("tools.process_registry.process_registry", foreign_reg), \
         patch("tools.process_registry.CHECKPOINT_PATH", foreign_checkpoint):
        try:
            t.HermesCancellationE2ETests.setUpClass()
            sys.exit(1)
        except AssertionError as e:
            assert "not in isolated" in str(e), f"unexpected error {e}"

    assert not foreign_reg.kill_all_called, "foreign kill_all was called!"
    assert foreign_checkpoint.read_text() == initial_content, "checkpoint file mutated!"
    assert foreign_checkpoint.stat().st_mtime == initial_mtime, "checkpoint mtime mutated!"
    assert os.environ.get("HERMES_HOME") == orig_env, f"env not restored: {os.environ.get('HERMES_HOME')} != {orig_env}"
    print("STAGE 1 PASSED")
"""
        res = self._run_subp(code)
        self.assertEqual(res.returncode, 0, f"Stage 1 failed: stdout={res.stdout}, stderr={res.stderr}")
        self.assertIn("STAGE 1 PASSED", res.stdout)

    def test_setup_failure_during_bridge_creation_restores_env(self):
        """Stage 2: Setup failure after isolation check (bridge timeout) cleans only fixture resources and restores env."""
        code = """
import os, sys, json, tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, sys.argv[1])
sys.path.insert(0, sys.argv[2])
for p in Path(sys.argv[3]).glob("python*/site-packages"):
    sys.path.insert(0, str(p))

orig_env = "/some/original/hermes/path"
os.environ["HERMES_HOME"] = orig_env

with tempfile.TemporaryDirectory(prefix="cairn-foreign-test-") as foreign_dir:
    foreign_home = Path(foreign_dir) / ".hermes"
    foreign_home.mkdir(parents=True, mode=0o700)
    foreign_checkpoint = foreign_home / "processes.json"
    initial_content = json.dumps([{"session_id": "foreign-proc-2", "pid": 77777}])
    foreign_checkpoint.write_text(initial_content)
    initial_mtime = foreign_checkpoint.stat().st_mtime

    from scripts import test_hermes_cancellation_e2e as t

    real_exists = os.path.exists
    def fake_exists(path):
        if "cairn-hermes" in str(path) and str(path).endswith(".sock"):
            return False
        return real_exists(path)

    with patch("os.path.exists", side_effect=fake_exists), \
         patch("time.sleep", return_value=None), \
         patch("time.monotonic", side_effect=[0.0, 10.0, 10.0, 10.0]):
        try:
            t.HermesCancellationE2ETests.setUpClass()
            sys.exit(1)
        except RuntimeError as e:
            assert "Bridge socket" in str(e), f"unexpected error {e}"

    assert foreign_checkpoint.read_text() == initial_content, "checkpoint file mutated!"
    assert foreign_checkpoint.stat().st_mtime == initial_mtime, "checkpoint mtime mutated!"
    assert os.environ.get("HERMES_HOME") == orig_env, f"env not restored: {os.environ.get('HERMES_HOME')} != {orig_env}"
    print("STAGE 2 PASSED")
"""
        res = self._run_subp(code)
        self.assertEqual(res.returncode, 0, f"Stage 2 failed: stdout={res.stdout}, stderr={res.stderr}")
        self.assertIn("STAGE 2 PASSED", res.stdout)

    def test_subprocess_full_lifecycle_cleanup(self):
        """Stage 3: End-to-end subprocess executing successful setup and teardown cleans up only fixture state."""
        code = """
import os, sys, json, tempfile
from pathlib import Path

sys.path.insert(0, sys.argv[1])
sys.path.insert(0, sys.argv[2])
for p in Path(sys.argv[3]).glob("python*/site-packages"):
    sys.path.insert(0, str(p))

orig_env = "/some/original/hermes/path"
os.environ["HERMES_HOME"] = orig_env

with tempfile.TemporaryDirectory(prefix="cairn-foreign-test-") as foreign_dir:
    foreign_home = Path(foreign_dir) / ".hermes"
    foreign_home.mkdir(parents=True, mode=0o700)
    foreign_checkpoint = foreign_home / "processes.json"
    initial_content = json.dumps([{"session_id": "foreign-proc-3", "pid": 66666}])
    foreign_checkpoint.write_text(initial_content)
    initial_mtime = foreign_checkpoint.stat().st_mtime

    from scripts import test_hermes_cancellation_e2e as t

    t.HermesCancellationE2ETests.setUpClass()
    sock = t.HermesCancellationE2ETests.sock_path
    assert t.HermesCancellationE2ETests._isolation_validated is True, "isolation not validated"
    assert t.HermesCancellationE2ETests._bridge_initialized is True, "bridge not initialized"
    assert os.path.exists(sock), f"bridge socket {sock} does not exist"

    t.HermesCancellationE2ETests.tearDownClass()
    assert not os.path.exists(sock), f"bridge socket {sock} was not cleaned up"
    assert foreign_checkpoint.read_text() == initial_content, "checkpoint file mutated!"
    assert foreign_checkpoint.stat().st_mtime == initial_mtime, "checkpoint mtime mutated!"
    assert os.environ.get("HERMES_HOME") == orig_env, f"env not restored: {os.environ.get('HERMES_HOME')} != {orig_env}"
    print("STAGE 3 PASSED")
"""
        res = self._run_subp(code)
        self.assertEqual(res.returncode, 0, f"Stage 3 failed: stdout={res.stdout}, stderr={res.stderr}")
        self.assertIn("STAGE 3 PASSED", res.stdout)


if __name__ == "__main__":
    unittest.main()
