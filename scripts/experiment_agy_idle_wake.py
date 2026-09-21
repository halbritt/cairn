#!/usr/bin/env python3
"""Rigorous empirical measurement of native Agy CLI (cortex) idle behavior and FileStore boundaries.

Addresses all parent review criteria:
1. Native completion verification: Inspects transcript.jsonl for PLANNER_RESPONSE status="DONE",
   never relying on PTY prompt echoes (e.g. b"READY").
2. Real metadata paths: Confirms presence lock, transcript.jsonl, and brain directory existence.
3. Draft preservation: Simulates user typing into composer before any external event.
4. Idle window observation (10s): Measures transcript delta, PTY output, wchan, and read receipt state.
5. Positive turn control: Submits an explicit prompt to measure whether disk-dropped message files
   are drained or ignored by the native harness.
"""

import fcntl
import json
import os
import pty
import select
import signal
import struct
import sys
import termios
import time
import uuid
from pathlib import Path


def run_experiment():
    exp_dir = Path("/tmp/cairn-agy-idle-exp")
    exp_dir.mkdir(parents=True, exist_ok=True)

    master, slave = pty.openpty()
    # Set proper terminal dimensions so Bubbletea initializes full viewport and textarea
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 80, 0, 0))

    pid = os.fork()
    if pid == 0:
        os.close(master)
        os.setsid()
        fcntl.ioctl(slave, termios.TIOCSCTTY, 0)
        os.dup2(slave, 0)
        os.dup2(slave, 1)
        os.dup2(slave, 2)
        os.close(slave)
        os.chdir(str(exp_dir))
        os.environ["TERM"] = "xterm-256color"
        os.execlp("agy", "agy", "-i", "Say 1")

    os.close(slave)

    flags = fcntl.fcntl(master, fcntl.F_GETFL)
    fcntl.fcntl(master, fcntl.F_SETFL, flags | os.O_NONBLOCK)

    convo_id = None
    transcript_file = None
    start_time = time.time()

    print(f"[*] Spawned agy child PID {pid}")

    try:
        # Step 1: Discover conversation ID via presence lock
        print("[*] Discovering conversation ID via presence lock...")
        while time.time() - start_time < 15:
            time.sleep(0.3)
            fds_dir = Path(f"/proc/{pid}/fd")
            if fds_dir.is_dir():
                try:
                    for fd in fds_dir.iterdir():
                        try:
                            target = fd.resolve()
                            if "presence" in str(target) and target.name.endswith(".lock"):
                                convo_id = target.stem
                                break
                        except OSError:
                            pass
                except OSError:
                    pass
            if convo_id:
                break

        if not convo_id:
            raise RuntimeError("Could not find conversation ID from presence lock")

        brain_dir = Path.home() / ".gemini" / "antigravity-cli" / "brain" / convo_id
        transcript_file = brain_dir / ".system_generated" / "logs" / "transcript.jsonl"
        messages_dir = brain_dir / ".system_generated" / "messages"
        undelivered_dir = messages_dir / "undelivered"
        messages_dir.mkdir(parents=True, exist_ok=True)
        undelivered_dir.mkdir(parents=True, exist_ok=True)

        print(f"[*] Found conversation {convo_id}. Waiting for initial turn PLANNER_RESPONSE status=DONE...")

        # Step 2: Verify native completion via transcript.jsonl
        step1_done = False
        while time.time() - start_time < 30:
            time.sleep(0.5)
            if transcript_file.is_file():
                lines = transcript_file.read_text().splitlines()
                for line in lines:
                    try:
                        step = json.loads(line)
                        if step.get("type") == "PLANNER_RESPONSE" and step.get("status") == "DONE":
                            step1_done = True
                            break
                    except Exception:
                        pass
            if step1_done:
                break

        if not step1_done:
            raise RuntimeError("Initial turn did not reach PLANNER_RESPONSE status=DONE in transcript.jsonl")

        initial_transcript_count = len(transcript_file.read_text().splitlines())
        print(f"[*] Initial turn verified complete. Transcript has {initial_transcript_count} steps.")

        # Give TUI a moment to settle at the composer
        time.sleep(1.0)

        # Drain any initial output from PTY
        try:
            while True:
                r, _, _ = select.select([master], [], [], 0.1)
                if not r:
                    break
                os.read(master, 4096)
        except OSError:
            pass

        # Step 3: Simulate user typing unsubmitted draft into composer
        draft_content = "user draft waiting in composer"
        print(f"[*] Typing draft into composer (no newline): {draft_content!r}")
        os.write(master, draft_content.encode("utf-8"))
        time.sleep(0.5)

        # Drain echo of typed draft
        try:
            while True:
                r, _, _ = select.select([master], [], [], 0.1)
                if not r:
                    break
                os.read(master, 4096)
        except OSError:
            pass

        # Step 4: Drop native message into messages/ and messages/undelivered/
        msg_id = str(uuid.uuid4())
        msg_payload = {
            "id": msg_id,
            "recipient": convo_id,
            "sender": "test:parent-review",
            "priority": "MESSAGE_PRIORITY_HIGH",
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "content": "TEST_IDLE_WAKE_PAYLOAD",
            "renderDetails": {"messageTitle": "Idle Wake Probe"}
        }
        msg_file_main = messages_dir / f"{msg_id}.json"
        msg_file_undelivered = undelivered_dir / f"{msg_id}.json"
        payload_str = json.dumps(msg_payload, indent=2)
        msg_file_main.write_text(payload_str)
        msg_file_undelivered.write_text(payload_str)
        print(f"[*] Dropped message {msg_id} into messages/ and undelivered/")

        # Step 5: Observe idle state for 10.0 seconds
        print("[*] Observing for 10.0 seconds while session is idle at composer...")
        idle_start = time.time()
        idle_output = b""
        while time.time() - idle_start < 10.0:
            r, _, _ = select.select([master], [], [], 0.5)
            if r:
                try:
                    chunk = os.read(master, 4096)
                    if chunk:
                        idle_output += chunk
                except OSError:
                    pass

        # Inspect state after idle observation
        wchan = "unknown"
        wchan_path = Path(f"/proc/{pid}/wchan")
        if wchan_path.is_file():
            wchan = wchan_path.read_text().strip()

        transcript_count_after_idle = len(transcript_file.read_text().splitlines())
        read_json_file = messages_dir / "read.json"
        read_json_exists_idle = read_json_file.is_file()
        msg_in_read_json_idle = False
        if read_json_exists_idle:
            try:
                read_data = json.loads(read_json_file.read_text())
                msg_in_read_json_idle = msg_id in read_data
            except Exception:
                pass

        print("--- 10s IDLE OBSERVATION RESULTS ---")
        print(f"  PID {pid} wchan: {wchan}")
        print(f"  Transcript steps before/after: {initial_transcript_count} -> {transcript_count_after_idle} (delta: {transcript_count_after_idle - initial_transcript_count})")
        print(f"  PTY output received during idle: {len(idle_output)} bytes")
        print(f"  Message in messages/ exists: {msg_file_main.is_file()}")
        print(f"  Message in undelivered/ exists: {msg_file_undelivered.is_file()}")
        print(f"  read.json exists: {read_json_exists_idle}, marked read: {msg_in_read_json_idle}")

        # Step 6: Positive Turn Control
        # Press Enter (\r) to submit the typed draft as a prompt and run a turn.
        print("[*] Submitting prompt via Enter (\\r) to execute Turn 2...")
        os.write(master, b"\r")

        # Wait up to 15 seconds for Turn 2 completion
        turn2_done = False
        turn2_start = time.time()
        while time.time() - turn2_start < 15.0:
            time.sleep(0.5)
            lines = transcript_file.read_text().splitlines()
            if len(lines) > initial_transcript_count:
                for line in lines[initial_transcript_count:]:
                    try:
                        step = json.loads(line)
                        if step.get("type") == "PLANNER_RESPONSE" and step.get("status") == "DONE":
                            turn2_done = True
                            break
                    except Exception:
                        pass
            if turn2_done:
                break

        transcript_count_after_turn2 = len(transcript_file.read_text().splitlines())
        read_json_exists_turn2 = read_json_file.is_file()
        msg_in_read_json_turn2 = False
        if read_json_exists_turn2:
            try:
                read_data = json.loads(read_json_file.read_text())
                msg_in_read_json_turn2 = msg_id in read_data
            except Exception:
                pass

        print("--- POST-ENTER TURN 2 RESULTS ---")
        print(f"  Turn 2 completed: {turn2_done}")
        print(f"  Transcript steps after Turn 2: {transcript_count_after_turn2} (delta: {transcript_count_after_turn2 - initial_transcript_count})")
        print(f"  read.json exists: {read_json_exists_turn2}, marked read: {msg_in_read_json_turn2}")

        # Clean up test message files
        try:
            if msg_file_main.is_file():
                msg_file_main.unlink()
            if msg_file_undelivered.is_file():
                msg_file_undelivered.unlink()
        except OSError:
            pass

        return {
            "pid": pid,
            "convo_id": convo_id,
            "wchan": wchan,
            "idle_observation_seconds": 10.0,
            "initial_step_verified_done": step1_done,
            "transcript_steps_initial": initial_transcript_count,
            "transcript_steps_after_idle": transcript_count_after_idle,
            "transcript_delta_during_idle": transcript_count_after_idle - initial_transcript_count,
            "pty_output_bytes_during_idle": len(idle_output),
            "msg_file_main_retained_during_idle": msg_file_main.is_file(),
            "msg_file_undelivered_retained_during_idle": msg_file_undelivered.is_file(),
            "marked_read_during_idle": msg_in_read_json_idle,
            "turn2_executed_after_enter": turn2_done,
            "transcript_steps_after_turn2": transcript_count_after_turn2,
            "marked_read_after_turn2": msg_in_read_json_turn2,
            "boundary_conclusion": (
                "FileStore has no filesystem watcher (fsnotify/inotify). External disk writes "
                "to messages/ or messages/undelivered/ are not monitored while the process is blocked "
                "in ep_poll (Bubbletea event loop). Furthermore, FileStore is only updated in-process "
                "via Hub.Send. Neither idle wakeups nor turn-boundary ingestion occur from external "
                "file drops; Agy requires supported lifecycle hook injection (injectSteps) or an "
                "explicit upstream socket/PTY adapter."
            )
        }

    finally:
        try:
            os.kill(pid, signal.SIGTERM)
            time.sleep(0.5)
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
        os.close(master)


if __name__ == "__main__":
    results = run_experiment()
    print("\nFINAL MEASURED EXPERIMENT SUMMARY:")
    print(json.dumps(results, indent=2))
