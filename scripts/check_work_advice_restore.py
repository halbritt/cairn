#!/usr/bin/env python3
"""Actual combined schema079 physical advice restore proof.

Requires an immutable candidate capture and an exact build receipt. Does not
build, install, provision production resources or call an external model.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('binary', type=Path)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--capture-manifest', type=Path, required=True)
    parser.add_argument('--binary-report', type=Path, required=True)
    parser.add_argument('--native-manifest', type=Path, required=True)
    parser.add_argument('--report-directory', type=Path, required=True)
    args = parser.parse_args()
    source, binary, output = args.source.resolve(), args.binary.resolve(), args.report_directory.resolve()
    sys.path.insert(0, str(source / 'scripts'))
    import disposable_host
    import check_codex_organic_native
    import work_coordination_ui_fixture as ui
    require = disposable_host.require
    for helper in (disposable_host, check_codex_organic_native, ui):
        require(Path(helper.__file__).resolve().parent == source / 'scripts', 'helper escaped the captured candidate')
    require(not output.is_relative_to(source) and not output.is_relative_to(HERE),
            'report output must be outside source and fixture scripts')
    before = ui.source_identity(source, args.capture_manifest.resolve())
    build_raw = args.binary_report.read_bytes()
    build = json.loads(build_raw)
    binary_sha = hashlib.sha256(binary.read_bytes()).hexdigest()
    require(build.get('schema') == 'cairn.physical-restore-build/1'
            and build.get('source_input_sha256') == before['input_sha256']
            and build.get('source_commit') == before['revision']
            and build.get('source_unchanged') is True and build.get('build_returncode') == 0
            and build.get('binary_sha256') == binary_sha,
            'build receipt does not bind the exact unchanged captured source and executable')
    spec = importlib.util.spec_from_file_location('selected_restore_runner',
        HERE / 'check_work_coordination_restore.py')
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    runner.ROOT = source
    case_spec = importlib.util.spec_from_file_location('selected_advice_case', HERE / 'work_advice_restore_case.py')
    case_module = importlib.util.module_from_spec(case_spec)
    case_spec.loader.exec_module(case_module)
    case = case_module.AdviceRestoreCase(source)
    case.extra_inputs = [Path(__file__).resolve(), HERE / 'check_work_coordination_restore.py',
                         args.capture_manifest.resolve(), args.binary_report.resolve()]
    wrapper_report = dict(schema='cairn.advice-restore-wrapper/1', status='running',
        source=before, binary_sha256=binary_sha, binary_report_sha256=hashlib.sha256(build_raw).hexdigest(),
        provider='registered synthetic advice process plus local scripted native Responses',
        external_model_calls=False, native_subscription_advice_claim=False)
    original_failure = None
    sys.argv = [str(HERE / 'check_work_advice_restore.py'), str(binary), '--native-manifest',
                str(args.native_manifest.resolve()), '--report-directory', str(output)]
    try:
        runner.main(extension_factory=lambda: case)
        wrapper_report['status'] = 'passed'
    except BaseException as error:
        original_failure = error
        wrapper_report.update(status='failed', error=str(error), error_type=type(error).__name__)
        raise
    finally:
        verification_error = None
        try:
            after = ui.source_identity(source, args.capture_manifest.resolve())
            wrapper_report['source_after_run'] = after
            require(after == before, 'candidate source changed during whole-database proof')
            require(hashlib.sha256(args.binary_report.read_bytes()).hexdigest() == wrapper_report['binary_report_sha256'],
                    'selected build receipt changed during proof')
            require(hashlib.sha256(binary.read_bytes()).hexdigest() == binary_sha, 'candidate binary changed during proof')
        except BaseException as error:
            verification_error = error
            wrapper_report.update(status='failed', final_identity_error=str(error))
        if output.is_dir():
            (output / 'advice-wrapper-report.json').write_text(json.dumps(wrapper_report, indent=2) + '\n')
        if verification_error is not None and original_failure is None:
            raise verification_error


if __name__ == '__main__':
    main()
