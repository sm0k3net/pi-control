import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

import usb_backup as usb


class ReconnectTests(unittest.TestCase):
    def test_reconnect_removes_old_managed_mount_even_with_same_uuid(self):
        item = {'uuid': '0123-ABCD', 'path': '/dev/sdb1', 'supported': True}
        with tempfile.TemporaryDirectory() as work:
            root = Path(work)
            slot = root / item['uuid']
            slot.mkdir()
            with patch.object(usb, 'FILES_MOUNT', root), \
                 patch.object(usb, 'media_identity', return_value='/dev/sdb1:25'), \
                 patch.object(usb.os.path, 'ismount', return_value=True), \
                 patch.object(usb, 'is_device_mount', return_value=False), \
                 patch.object(usb, 'existing_mount', return_value=None), \
                 patch.object(usb, 'run') as unmount, patch.object(usb, 'mount_device') as mount:
                result = usb.automount_files([item], {item['uuid']: {'identity': '/dev/sda1:24'}})
            unmount.assert_called_once_with(['umount', '--', str(slot)], timeout=12)
            mount.assert_called_once_with(item, slot)
            self.assertEqual(result[item['uuid']], {'identity': '/dev/sdb1:25'})

    def test_failed_mount_is_reported_and_retry_bypasses_delay(self):
        item = {'uuid': '0123-ABCD', 'path': '/dev/sdb1', 'supported': True}
        with tempfile.TemporaryDirectory() as work:
            with patch.object(usb, 'FILES_MOUNT', Path(work)), \
                 patch.object(usb, 'media_identity', return_value='disk'), \
                 patch.object(usb, 'existing_mount', return_value=None), \
                 patch.object(usb, 'mount_device', side_effect=ValueError('test error')) as mount:
                result = usb.automount_files([item], {})
                self.assertTrue(result[item['uuid']]['error'])
                self.assertEqual(usb.automount_files([item], result), result)
                usb.automount_files([item], result, retry=True)
                self.assertEqual(mount.call_count, 2)


if __name__ == '__main__':
    unittest.main()
