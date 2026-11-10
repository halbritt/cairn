#!/usr/bin/env python3
"""Disposable R-02 installation/reverse-selection checks; no live activation.

Run through make test-isolated after building the actual stager and cairn. F-04
frontend activation is deliberately outside this fixture's implemented claims.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
STAGER = None
CAIRN = None
OLD_ID = "147fc8dd-b0d9-4b9f-8be1-59a2c77ef0cb"
NEW_ID = "9661ba6a-14a1-4167-82a4-72a400af3546"


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


class GenerationInstallation(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="cairn-r02-install-", dir="/dev/shm")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.releases = self.base / "releases"
        self.releases.mkdir(mode=0o700)
        self.state = self.base / "custom-state"
        self.state.mkdir(mode=0o700)
        self.retained = self.state / "session.inbox-recall.json"
        self.retained.write_bytes(b'{"schema":"retained-state","custom":[1,2,3]}\n')
        self.retained.chmod(0o600)
        self.token = self.base / "synthetic.token"
        self.token.write_bytes(b"synthetic fixture only; not an installation member\n")
        self.token.chmod(0o600)
        self.tripwire = self.base / "unexpected-api-call"
        self.fake_api = self.base / "api-tripwire"
        self.fake_api.write_text("#!/bin/sh\nprintf called > " + str(self.tripwire) + "\nexit 99\n")
        self.fake_api.chmod(0o700)
        self.custom = self.base / "unrelated-settings.json"
        self.custom.write_bytes(b'{ "unrelated": true, "custom": "leave unchanged" }\n')
        self.custom.chmod(0o600)
        self.preserved = {p: p.read_bytes() for p in (self.retained, self.token, self.custom)}
        self.work = self.base / "workspace"
        self.work.mkdir(mode=0o700)
        self.inputs = self.base / "old-inputs"
        spec = importlib.util.spec_from_file_location("r02_legacy_bundle", ROOT / "integrations/lifecycle/lifecycle_bundle.py")
        bundle = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(bundle)
        bundle.stage_runtime(ROOT / "integrations/lifecycle", self.inputs)
        self.old_dir = self.releases / ("selection-" + OLD_ID)
        self.write_configs(self.inputs, self.old_dir, "cairn.inbox-recall-binding/2", bundle)
        self.launches = {
            "memory": [sys.executable, "-I", "-B", str(self.old_dir / "memory.py"), "--config", str(self.old_dir / "memory.json")],
            "coordination": [sys.executable, "-I", "-B", str(self.old_dir / "coordination.py"), "hook", "--config", str(self.old_dir / "coordination.json")],
        }
        (self.inputs / "retained-argv.json").write_text(json.dumps(self.launches))
        (self.inputs / "retained-argv.json").chmod(0o600)
        self.old = self.stage(self.inputs, OLD_ID)

    def call(self, argv, payload=None):
        return subprocess.run(argv, cwd=self.work, input=payload, capture_output=True,
                              timeout=15, env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))

    def stage(self, source, identity):
        files = [dict(name=p.name, path=str(p), sha256=digest(p.read_bytes()), executable=p.name == "cairn")
                 for p in sorted(source.iterdir()) if p.is_file()]
        result = self.call([STAGER, "generation", "--root", str(self.releases)],
                           json.dumps(dict(id=identity, files=files)).encode())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, b"")
        return json.loads(result.stdout)

    def inspect(self, selection, success=True):
        result = self.call([STAGER, "inspect-generation", "--path", selection["path"], "--sha256", selection["sha256"]])
        if success:
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(result.stdout)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, b"")

    def write_configs(self, source, final, schema, bundle=None):
        manifest = final / "binding.json"
        common = dict(harness="codex", cairn=str(self.fake_api), socket=str(self.base / "unused.sock"),
                      token_file=str(self.token), repo="fixture:r02-install", inbox_recall_binding=str(manifest))
        memory = dict(common, recall_mode="agent_tools", context_bytes=8123, state_dir=str(self.state),
                      preserved_custom={"unknown": [1, "two", False]})
        coord = dict(common, native_delivery=True, state_dir=str(self.base / "coord-state"), binding="fixture-r02")
        for name, config in (("memory", memory), ("coordination", coord)):
            (source / (name + ".json")).write_text(json.dumps(config, indent=2) + "\n")
        descriptor = lambda name: dict(path=str(final / name), sha256=digest((source / name).read_bytes()))
        binding = dict(schema=schema, enabled=False, memory_config=descriptor("memory.json"),
                       coordination_configs=[descriptor("coordination.json")])
        if bundle is not None:
            for role, name in (("engine", "memory.py"), ("bridge", "inbox_recall.py")):
                binding[role] = dict(descriptor(name), bundle=bundle.bundle_descriptor(source))
        else:
            binding["binary"] = descriptor("cairn")
        (source / "binding.json").write_text(json.dumps(binding) + "\n")
        for p in source.iterdir():
            if p.is_file():
                p.chmod(0o600)

    def assert_preserved(self):
        for path, raw in self.preserved.items():
            self.assertEqual(path.read_bytes(), raw)
        self.assertFalse(self.tripwire.exists(), "fixture unexpectedly invoked the API executable")

    def legacy_frontends(self, launches):
        event = json.dumps(dict(hook_event_name="PostToolUse", session_id="11111111-1111-4111-8111-111111111111",
                                cwd=str(self.work), tool_name="Read", tool_input={"file_path": "selected.go"})).encode()
        # Ordinary excluded coordination is a real frontend path, not admission
        # or activation acceptance. The legacy binding predicate is checked below.
        (self.work / ".cairn-no-coordination").touch()
        results = {}
        for role, argv in launches.items():
            result = self.call(argv, event)
            self.assertEqual(result.returncode, 0, (role, result.stderr))
            self.assertEqual(result.stderr, b"")
            self.assertEqual(json.loads(result.stdout), {})
            results[role] = (result.returncode, result.stdout, result.stderr)
        return results

    def test_install_and_reverse_retains_real_legacy_bytes_and_argv(self):
        original = self.inspect(self.old)
        before = self.legacy_frontends(self.launches)
        # Invoke the actual retained /2 validator over the installed full bundle;
        # it reads config/code pins, not the selected token contents or API.
        check = ("import importlib.util,json,sys;from pathlib import Path;"
                 "r=Path(sys.argv[1]);s=importlib.util.spec_from_file_location('retained',r/'lifecycle_bundle.py');"
                 "m=importlib.util.module_from_spec(s);s.loader.exec_module(m);b=m.Bundle(r);"
                 "b.load('inbox_binding').InboxBinding(b).validate(json.loads((r/'memory.json').read_text()));print('validated /2')")
        checked = self.call([sys.executable, "-I", "-B", "-c", check, str(self.old_dir)])
        self.assertEqual((checked.returncode, checked.stdout, checked.stderr), (0, b"validated /2\n", b""))

        new_inputs = self.base / "new-inputs"
        new_inputs.mkdir(mode=0o700)
        shutil.copyfile(CAIRN, new_inputs / "cairn")
        final = self.releases / ("selection-" + NEW_ID)
        self.write_configs(new_inputs, final, "cairn.inbox-recall-binding/3")
        newer = self.stage(new_inputs, NEW_ID)
        self.inspect(newer)
        public = self.call([str(final / "cairn"), "lifecycle", "check"], b"unparsed input")
        self.assertNotEqual(public.returncode, 0)
        self.assertIn(b"LIFECYCLE_DISABLED", public.stdout + public.stderr)

        # Reverse selection is explicitly the original independently pinned
        # generation and its exact stored argv, never a new executor or /2 rewrite.
        self.assertEqual(self.inspect(self.old), original)
        reverse = json.loads((self.old_dir / "retained-argv.json").read_bytes())
        self.assertEqual(reverse, self.launches)
        self.assertEqual(self.legacy_frontends(reverse), before)
        self.assert_preserved()

    def test_tampered_retained_member_refuses_before_reverse_selection(self):
        helper = self.old_dir / "hook_admission.py"
        helper.chmod(0o600)
        helper.write_bytes(b"changed installed helper\n")
        helper.chmod(0o400)
        self.inspect(self.old, success=False)
        result = self.call(self.launches["memory"], b"{}")
        self.assertNotEqual(result.returncode, 0)
        self.assert_preserved()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stager", required=True)
    parser.add_argument("--cairn", required=True)
    args, remaining = parser.parse_known_args()
    STAGER, CAIRN = str(Path(args.stager).resolve()), str(Path(args.cairn).resolve())
    unittest.main(argv=[sys.argv[0], *remaining])
