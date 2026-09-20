"""Test concurrent execution of provision-event-profiles.py."""
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("provision_profiles", ROOT / "scripts/provision-event-profiles.py")
provisioner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(provisioner)


class ProvisionProfilesConcurrencyTests(unittest.TestCase):
    def test_concurrent_provisioning_preserves_all_identities(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config = home / "identities.json"
            config.write_text(json.dumps([]))

            def worker(name):
                return provisioner.provision(home, "/repo", [name])

            names = [f"agent-{i}" for i in range(8)]
            with ThreadPoolExecutor(max_workers=4) as executor:
                results = list(executor.map(worker, names))

            self.assertEqual(len(results), 8)
            identities = json.loads(config.read_text())
            principals = {i["principal"] for i in identities}
            expected_principals = {f"agent/{name}" for name in names}
            self.assertEqual(principals, expected_principals)
            self.assertEqual(len(identities), 8)


if __name__ == "__main__":
    unittest.main()
