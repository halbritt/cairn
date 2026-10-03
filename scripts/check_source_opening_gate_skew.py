#!/usr/bin/env python3
"""Cross-version check of the source-opening gate against two older builds.

Three builds run the same probe against one disposable database:
  PRE  the parent of the feature (receipts have never had a sealed opening policy),
  FEAT the feature before the gate (every new context-index receipt binds),
  HEAD this commit, with binding off and with binding on.

With binding off HEAD must write the receipt bytes PRE writes, and PRE must read
HEAD's receipts. With binding on HEAD must write the bytes FEAT writes and answer
reads exactly as FEAT does. PRE must not be able to read a bound receipt, which is
the downgrade boundary the operator documentation states. Needs a disposable
database in CAIRN_TEST_DATABASE_URL and the git history of both older builds;
without the history it says so and exits 0. Nothing is written to the repository.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
PRE = "3c668eb6395dd2be99c538ff9dfb106b2411c3ba"
FEATURE = "afe7746478ff7a72a22724f54247f2a417f644de"
PROBE = ROOT / "scripts/source_opening_gate_skew"
SPAN_CONTEXT_KEY = bytes.fromhex("6c7370616e5f636f6e74657874")  # CBOR text key "span_context"


def git(*args, **kwargs):
    return subprocess.run(["git", *args], cwd=ROOT, check=True, capture_output=True, **kwargs)


def export(revision, destination, variant):
    destination.mkdir(parents=True)
    archive = subprocess.Popen(["git", "archive", revision], cwd=ROOT, stdout=subprocess.PIPE)
    subprocess.run(["tar", "-x", "-C", str(destination)], stdin=archive.stdout, check=True)
    archive.stdout.close()
    if archive.wait() != 0:
        raise SystemExit(f"git archive {revision} failed")
    shutil.copy(PROBE / "probe_test.go.txt", destination / "core/zz_skew_probe_test.go")
    shutil.copy(PROBE / f"variant_{variant}_test.go.txt", destination / "core/zz_skew_variant_test.go")


def check():
    dsn = os.environ.get("CAIRN_TEST_DATABASE_URL")
    assert dsn, "CAIRN_TEST_DATABASE_URL names a disposable database"
    for revision in (PRE, FEATURE):
        if subprocess.run(["git", "cat-file", "-e", revision + "^{commit}"], cwd=ROOT, capture_output=True).returncode != 0:
            print(f"Source-opening gate skew: skipped, history for {revision[:12]} is unavailable")
            return
    work = Path(tempfile.mkdtemp(prefix="cairn-opening-gate-skew-"))
    try:
        head = git("rev-parse", "HEAD", text=True).stdout.strip()
        trees = {"head": (head, "head"), "feature": (FEATURE, "feature"), "pre": (PRE, "pre")}
        for name, (revision, variant) in trees.items():
            export(revision, work / name, variant)
        shared = work / "state"
        shared.mkdir()

        def run(tree, phase, out=None, gate="off", source=None, expand=False):
            env = dict(os.environ, CAIRN_TEST_DATABASE_URL=dsn, CAIRN_DATABASE_URL=dsn, SKEW_DIR=str(shared), SKEW_PHASE=phase, SKEW_GATE=gate,
                       SKEW_OUT=out or "", SKEW_IN=source or "", SKEW_EXPAND="1" if expand else "0")
            result = subprocess.run(["go", "test", "-count=1", "-run", "TestSkewProbe", "./core"], cwd=work / tree, env=env, capture_output=True, text=True)
            assert result.returncode == 0 and "no tests to run" not in result.stdout, f"{tree} {phase}: {result.stdout}{result.stderr}"
            return json.loads((shared / out).read_text()) if out else None

        run("head", "seed")
        # Receipts for cross reading (no expansion credits spent) and for comparing observable answers.
        head_off_read = run("head", "compile", "head_off_read.json", "off")
        head_on_read = run("head", "compile", "head_on_read.json", "on")
        feature_read = run("feature", "compile", "feature_read.json")
        head_off = run("head", "compile", "head_off.json", "off", expand=True)
        head_on = run("head", "compile", "head_on.json", "on", expand=True)
        feature = run("feature", "compile", "feature.json", expand=True)
        pre = run("pre", "compile", "pre.json", expand=True)
        pre_reads_off = run("pre", "read", "pre_reads_off.json", source="head_off_read.json")
        pre_reads_on = run("pre", "read", "pre_reads_on.json", source="head_on_read.json")
        feature_reads_on = run("feature", "read", "feature_reads_on.json", source="head_on_read.json")
        head_reads_feature = run("head", "read", "head_reads_feature.json", "off", source="feature_read.json")

        body = lambda receipt: bytes.fromhex(receipt["body_hex"])
        # Binding off writes exactly the pre-feature bytes; binding on writes exactly the feature's.
        assert body(head_off) == body(pre) and head_off["seal"] == pre["seal"], "gate off must write the pre-feature receipt bytes"
        assert SPAN_CONTEXT_KEY not in body(head_off) and SPAN_CONTEXT_KEY in body(head_on)
        assert body(head_on) == body(feature) and head_on["seal"] == feature["seal"], "gate on must write the feature's receipt bytes"
        assert head_on["seal"] != head_off["seal"]
        # Observable answers on equal input: unbound equals PRE, bound equals the feature, byte for byte.
        for key in ("span_declared", "whole"):
            assert head_off[key] == pre[key] and head_off[key]["ok"], f"gate off {key} differs from the pre-feature answer"
        for key in ("span_declared", "span_undeclared"):
            assert head_off[key]["ok"] and "source_opening" not in json.dumps(head_off[key]["value"]), f"gate off must not pair a span ({key})"
        for key in ("span_declared", "span_undeclared", "explicit_undeclared", "explicit_declared", "whole"):
            assert head_on[key] == feature[key], f"gate on {key} differs from the feature build's answer: {head_on[key]} vs {feature[key]}"
        assert head_on["span_declared"]["ok"] and "source_opening" in json.dumps(head_on["span_declared"]["value"])
        assert head_on["span_undeclared"]["code"] == "PROTOCOL_UNSUPPORTED" and head_on["explicit_undeclared"]["code"] == "PROTOCOL_UNSUPPORTED"
        # PRE reads what binding off wrote, retry included, and cannot read what binding on wrote.
        for key in ("span_declared", "span_undeclared", "whole", "replay", "recompile", "retry"):
            assert pre_reads_off[key]["ok"], f"the pre-feature build cannot read an unbound receipt ({key}): {pre_reads_off[key]}"
        assert pre_reads_off["retry"]["value"] == head_off_read["receipt"] + "/" + head_off_read["seal"], "the pre-feature retry must return the same receipt"
        for key, code in (("span_declared", "INTEGRITY_FAILURE"), ("whole", "INTEGRITY_FAILURE"), ("replay", "INTEGRITY_FAILURE"), ("recompile", "INTEGRITY_FAILURE"), ("retry", "STALE_PACKAGE")):
            assert pre_reads_on[key].get("code") == code, f"the downgrade boundary changed ({key}): {pre_reads_on[key]}"
        # The feature build reads HEAD's bound receipts as it reads its own; HEAD (binding off) keeps the guard on the feature's.
        assert feature_reads_on["span_declared"]["ok"] and feature_reads_on["span_undeclared"]["code"] == "PROTOCOL_UNSUPPORTED"
        assert feature_reads_on["whole"]["ok"] and feature_reads_on["replay"]["ok"] and feature_reads_on["recompile"]["ok"] and feature_reads_on["retry"]["ok"]
        assert head_reads_feature["span_declared"]["ok"] and head_reads_feature["span_undeclared"]["code"] == "PROTOCOL_UNSUPPORTED"
        assert head_reads_feature["replay"]["ok"] and head_reads_feature["recompile"]["ok"]
        assert head_reads_feature["retry"]["code"] == "STALE_PACKAGE", "a lexical retry across a binding change is stale"
        print("Source-opening gate skew: binding off writes the pre-feature bytes and answers as it does, binding on writes the feature bytes and answers as it does, "
              "the pre-feature build reads unbound receipts and cannot read bound ones")
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    check()
