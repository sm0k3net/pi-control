import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import maintenance_updates as update
import maintenance_restore as restore


class TransactionTests(unittest.TestCase):
    def test_failed_health_check_rolls_back_original_files_and_version(self):
        with tempfile.TemporaryDirectory() as work:
            root = Path(work)
            app = root / 'app'
            app.mkdir()
            snapshot = root / 'state'
            snapshot.mkdir()
            with patch.multiple(update, APP=app, DATA=snapshot, STATE=snapshot/'state',
                                UNDO=snapshot/'undo', JOURNAL=snapshot/'journal'), \
                 patch.object(update.bundle, 'FILES', ('app.js',)), \
                 patch.object(update, 'health', side_effect=[ValueError('failed'), None]):
                (app/'app.js').write_bytes(b'old')
                update.baseline('1.0.2')
                with self.assertRaisesRegex(ValueError, 'восстановлены'):
                    update.transaction({'app.js':b'new'}, '1.0.3')
                self.assertEqual((app/'app.js').read_bytes(), b'old')
                self.assertEqual(update.read_json(update.STATE)['version'], '1.0.2')
                self.assertFalse(update.JOURNAL.exists())


    def test_ui_and_new_modules_roll_back_together(self):
        with tempfile.TemporaryDirectory() as work:
            app = Path(work)
            snapshot = app / 'metadata'
            snapshot.mkdir()
            with patch.multiple(update, APP=app, DATA=snapshot, STATE=snapshot/'state',
                                UNDO=snapshot/'undo', JOURNAL=snapshot/'journal'), \
                 patch.multiple(update.bundle, FILES=('app.js',), ADDITIONS=('node.py',)), \
                 patch.object(update, 'health'):
                (app/'app.js').write_bytes(b'old')
                update.switch_ui('1.0.2')
                update.baseline('1.0.2')
                update.transaction({'app.js': b'new', 'node.py': b''}, '1.0.3',
                                   ui_install=lambda: update.switch_ui('1.0.3'))
                self.assertEqual(update.ui_version(), '1.0.3')
                self.assertTrue((app/'node.py').is_file())
                update.rollback()
                self.assertEqual(update.ui_version(), '1.0.2')
                self.assertEqual((app/'app.js').read_bytes(), b'old')
                self.assertFalse((app/'node.py').exists())

    def test_recovery_paths_are_classified_and_remain_relative(self):
        self.assertEqual(restore.archive_kind('home/user/file', '/home/user')[0], 'home')
        self.assertEqual(restore.archive_kind('./postgresql.sql', '/home/user')[0], 'postgres')
        with self.assertRaises(ValueError):
            restore.archive_kind('home/../file','/home/user')


if __name__ == '__main__':
    unittest.main()
