"""Pinned V8 input verification for the complete native package."""
import hashlib
import importlib.util
import io
from pathlib import Path
import tempfile
import subprocess
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('owner_build', Path(__file__).with_name('build.py'))
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)


class V8Inputs(unittest.TestCase):
    def test_source_manifest_detects_changed_and_new_inputs_excluding_ignored_builds(self):
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)
            subprocess.run(['git','init','-q',str(source)],check=True)
            (source/'.gitignore').write_text('target/\n')
            (source/'input.rs').write_text('original')
            subprocess.run(['git','add','.'],cwd=source,check=True)
            first=build.source_inputs(source)
            (source/'target').mkdir()
            (source/'target/binary').write_text('ignored output')
            self.assertEqual(build.source_inputs(source),first)
            (source/'input.rs').write_text('changed')
            self.assertNotEqual(build.source_inputs(source),first)
            (source/'input.rs').write_text('original')
            (source/'new.rs').write_text('untracked input')
            self.assertNotEqual(build.source_inputs(source),first)

    def test_upstream_manifest_authenticates_both_inputs_and_rejects_corruption(self):
        archive = 'librusty_v8_ptrcomp_sandbox_release_x86_64-unknown-linux-gnu.a.gz'
        binding = 'src_binding_ptrcomp_sandbox_release_x86_64-unknown-linux-gnu.rs'
        manifest = 'rusty_v8_ptrcomp_sandbox_release_x86_64-unknown-linux-gnu.sha256'
        files = {archive: b'archive fixture', binding: b'binding fixture'}
        files[manifest] = ''.join(hashlib.sha256(files[name]).hexdigest()+'  '+name+'\n'
                                  for name in (archive, binding)).encode()
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)/'source'
            trusted = source/'third_party/v8/rusty_v8_152_2_0_release_manifests.sha256'
            trusted.parent.mkdir(parents=True)
            trusted.write_text(hashlib.sha256(files[manifest]).hexdigest()+'  '+manifest+'\n')
            def fetch(url, **_kwargs):
                self.assertTrue(url.startswith('https://github.com/openai/codex/releases/download/rusty-v8-v152.2.0/'))
                return io.BytesIO(files[url.rsplit('/',1)[1]])
            with patch('urllib.request.urlopen', side_effect=fetch):
                result = build.prepare_v8(source, Path(directory)/'good')
                self.assertEqual(result['archive_sha256'], hashlib.sha256(b'archive fixture').hexdigest())
                self.assertEqual(Path(result['binding_path']).read_bytes(), b'binding fixture')
                files[archive] = b'changed archive'
                with self.assertRaisesRegex(ValueError, 'checksum'):
                    build.prepare_v8(source, Path(directory)/'bad-artifact')
                files[manifest] += b'bad manifest\n'
                with self.assertRaisesRegex(ValueError, 'checksum'):
                    build.prepare_v8(source, Path(directory)/'bad-manifest')


if __name__ == '__main__':
    unittest.main()
