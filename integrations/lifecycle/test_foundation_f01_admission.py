"""Independent F01 reader-kernel comparison; production profiles stay separate.

The helper wire and environment below exist only in compiled Go test binaries.
There is no production command, trusted profile handoff, chosen depth policy,
activation, or writer interface in this fixture.
"""
import hashlib
import json
import math
import os
from pathlib import Path
import resource
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]


def tagged(value):
    if value is None:
        return ["null"]
    if isinstance(value, bool):
        return ["bool", value]
    if isinstance(value, int):
        return ["int", str(value)]
    if isinstance(value, float):
        scalar = ("nan" if math.isnan(value) else "+inf" if value == math.inf else
                  "-inf" if value == -math.inf else struct.pack(">d", value).hex())
        return ["float", scalar]
    if isinstance(value, str):
        return ["string", [ord(c) for c in value]]
    if isinstance(value, list):
        return ["array", [tagged(item) for item in value]]
    if isinstance(value, dict):
        return ["object", [[[ord(c) for c in key], tagged(item)] for key, item in value.items()]]
    raise AssertionError(type(value))


def reader_oracle(mode, encoding, path):
    result = dict(integer_digits=sys.get_int_max_str_digits(), python=sys.version,
                  executable=sys.executable, argv=sys.argv,
                  stack=resource.getrlimit(resource.RLIMIT_STACK),
                  filesystem_encoding=sys.getfilesystemencoding(),
                  filesystem_errors=sys.getfilesystemencodeerrors(),
                  interpreter_sha256=hashlib.sha256(Path(sys.executable).read_bytes()).hexdigest())
    try:
        raw = path.read_bytes() if mode == "bytes" else path.read_text(encoding=encoding)
        value = json.loads(raw)
        result.update(error_class="", value=tagged(value))
    except Exception as exc:
        result.update(error_class=type(exc).__name__)
    print(json.dumps(result))


class ReaderKernelProcessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build = tempfile.TemporaryDirectory(prefix="f01-reader-", dir="/tmp")
        cls.addClassCleanup(cls.build.cleanup)
        cls.binary = Path(cls.build.name) / "reader.test"
        subprocess.run(["go", "test", "-c", "-o", str(cls.binary), "./internal/lifecycle/value"],
                       cwd=ROOT, check=True, capture_output=True, timeout=90)
        cls.evidence = os.environ.get("CAIRN_F01_EVIDENCE_DIR")
        if cls.evidence:
            cls.evidence = Path(cls.evidence)
            if not cls.evidence.is_absolute():
                raise ValueError("reader evidence path must be absolute")
            cls.evidence.mkdir(parents=True, exist_ok=False)
            shutil.copyfile(cls.binary, cls.evidence / "reader.test")
            pins = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in (
                "internal/lifecycle/value/value.go", "internal/lifecycle/value/value_test.go",
                "integrations/lifecycle/test_foundation_f01_admission.py")}
            (cls.evidence / "build.json").write_text(json.dumps(dict(
                command=["go", "test", "-c", "-o", str(cls.binary), "./internal/lifecycle/value"],
                source_pins=pins, binary_sha256=hashlib.sha256(cls.binary.read_bytes()).hexdigest(),
                go_version=subprocess.check_output(["go", "version"], text=True).strip(),
                scope="reader mechanics; production profile/domain/grammar held"), indent=2) + "\n")

    def compare(self, raw, mode="bytes", encoding="utf-8"):
        path = Path(self.build.name) / "raw-input"
        path.write_bytes(raw)
        reference_argv = [sys.executable, "-I", str(Path(__file__).resolve()), "--reader-oracle", mode, encoding, str(path)]
        reference = subprocess.run(reference_argv, input=b"", capture_output=True, timeout=5)
        self._sequence = getattr(self, "_sequence", 0) + 1
        evidence = None
        if self.evidence:
            evidence = self.evidence / (self._testMethodName + f"-{self._sequence:03d}")
            evidence.mkdir()
            (evidence / "input.bin").write_bytes(raw)
            (evidence / "reference.stdout").write_bytes(reference.stdout)
            (evidence / "reference.stderr").write_bytes(reference.stderr)
            (evidence / "reference.json").write_text(json.dumps(dict(
                argv=reference_argv, exit=reference.returncode, input_sha256=hashlib.sha256(raw).hexdigest(),
                mode=mode, encoding=encoding), indent=2) + "\n")
        self.assertEqual(reference.returncode, 0, reference.stderr)
        expected = json.loads(reference.stdout)
        env = dict(os.environ, CAIRN_F01_VALUE_HELPER="1", CAIRN_F01_VALUE_MODE=mode,
                   CAIRN_F01_VALUE_ENCODING=encoding,
                   CAIRN_F01_VALUE_DIGITS=str(expected["integer_digits"]))
        candidate_argv = [str(self.binary), "-test.run=^TestReaderOracleHelper$"]
        candidate = subprocess.run(candidate_argv, input=raw, env=env, capture_output=True, timeout=5)
        if evidence:
            (evidence / "candidate.stdout").write_bytes(candidate.stdout)
            (evidence / "candidate.stderr").write_bytes(candidate.stderr)
            (evidence / "candidate.json").write_text(json.dumps(dict(
                argv=candidate_argv, exit=candidate.returncode,
                integer_digits=expected["integer_digits"], mode=mode, encoding=encoding), indent=2) + "\n")
        self.assertEqual(candidate.returncode, 0, candidate.stderr)
        first, *tail = candidate.stdout.splitlines()
        self.assertEqual(tail, [b"PASS"], candidate.stdout)
        observed = json.loads(first)
        self.assertEqual(observed["error_class"], expected["error_class"], (raw[:60], mode, encoding))
        if expected["error_class"] == "":
            self.assertEqual(observed["value"], expected["value"], (raw[:60], mode, encoding))

    def test_scalar_and_unknown_ordered_values(self):
        rows = [b"null", b"false", b"true", b"0", b"-0", b"-0.0", b"1.0", b"1e400", b"-1e-4000",
                b"NaN", b"Infinity", b"-Infinity", b"9007199254740993123456789",
                b'{"x":{"old":1},"X":true,"x":{"new":2},"unknown":[1,1.0,true,-0.0,NaN]}',
                b'{"integer_digits":1,"max_depth":1,"unknown":{"z":2,"a":1}}',
                '{"\\ud83d\\ude00":1,"😀":2}'.encode(),
                '{"\\ud800":1,"�":2}'.encode(),
                '"é<>&\u2028\u2029"'.encode(), b'"\\ud800"', b'"\\ud83d\\ude00"',
                b'"\\udc80"', b'"\\ud800\\ud800"', b'""', b"[]", b"{}"]
        for raw in rows:
            with self.subTest(raw=repr(raw)[:60]):
                self.compare(raw)

    def test_byte_and_text_codec_routes(self):
        text = '{"text":"é😀"}'
        for encoding in ("utf-8-sig", "utf-16", "utf-16-le", "utf-16-be", "utf-32", "utf-32-le", "utf-32-be"):
            raw = text.encode(encoding)
            with self.subTest(encoding=encoding):
                self.compare(raw)
                self.compare(raw, "text", "UTF-8")
        for raw in (b'"\xed\xa0\x80"', b'"\xed\xa0\xbd\xed\xb8\x80"',
                    b'\xff\xfe"\x00\x00\xd8"\x00',
                    b'\x00\x00\xfe\xff\x00\x00\x00"\x00\x00\xd8\x00\x00\x00\x00"',
                    b'"\xff"', b'"\xef\xbf\xbd"', b'"\\ud800"'):
            for mode in ("bytes", "text"):
                with self.subTest(raw=raw, mode=mode):
                    self.compare(raw, mode)
        self.compare(b'"\xe9"', "text", "ascii")

    def test_deterministic_integer_boundary_and_malformed_input(self):
        for raw in (b"1" * 4300, b"1" * 4301, b"-" + b"1" * 4300, b"-" + b"1" * 4301,
                    b"[1,]", b'{"x":01}', b"[1e+]", b'"\\uZZZZ"', b"[}", b"true false", b""):
            with self.subTest(size=len(raw), prefix=raw[:20]):
                self.compare(raw)


if __name__ == "__main__":
    if sys.argv[1:2] == ["--reader-oracle"]:
        reader_oracle(sys.argv[2], sys.argv[3], Path(sys.argv[4]))
    else:
        unittest.main()
