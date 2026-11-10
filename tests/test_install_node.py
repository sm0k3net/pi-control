import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('installer', Path(__file__).resolve().parents[1] / 'scripts/install-node.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)

class TestInstaller(unittest.TestCase):
    def info(self):
        return {'os': 'Linux', 'arch': 'aarch64', 'model': 'test', 'ram_bytes': 1_000_000_000,
                'disk_free_bytes': 10 * 1024**3, 'debian': True, 'systemd': True,
                'docker': True, 'openssl': True, 'source': True, 'existing_installation': False}

    def test_rejects_existing_or_unsupported(self):
        data = self.info()
        data['existing_installation'] = True
        self.assertTrue(installer.errors(data, False))
        data = self.info()
        data['systemd'] = False
        self.assertTrue(installer.errors(data, False))
        data = self.info()
        data['ram_bytes'] = 1000
        self.assertTrue(installer.errors(data, False))

    def test_provisions_distinct_secret_without_overwriting(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'node'
            unit = Path(tmp) / 'node.service'
            def fake_run(args, **kwargs):
                if args[0] == 'openssl':
                    (root / 'key.pem').write_text('test-key')
                    (root / 'cert.pem').write_text('test-cert')
            with patch.object(installer, 'ROOT', root), patch.object(installer, 'UNIT', unit), \
                 patch.object(installer, 'report', return_value=self.info()), \
                 patch.object(installer.os, 'geteuid', return_value=0), \
                 patch.object(installer, 'run', side_effect=fake_run), patch.object(installer.time, 'sleep'):
                installer.install()
                config = json.loads((root / 'config.json').read_text())
                password = (root / 'initial-password.txt').read_text().strip()
                self.assertEqual(hashlib.scrypt(password.encode(), salt=bytes.fromhex(config['salt']), n=16384, r=8, p=1).hex(), config['password_hash'])
                self.assertEqual((root / 'initial-password.txt').stat().st_mode & 0o777, 0o600)
                self.assertTrue(unit.exists())
                with self.assertRaises(FileExistsError):
                    installer.install()
                self.assertEqual((root / 'initial-password.txt').read_text().strip(), password)

if __name__ == '__main__':
    unittest.main()
